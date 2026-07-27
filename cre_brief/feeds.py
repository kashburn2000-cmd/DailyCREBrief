"""RSS/Atom ingestion: fetch, validate, time-window, clean and dedupe.

Design goals:

* **Never let one dead feed break the run.** Every feed is fetched inside a
  try/except; failures are logged and skipped.
* **Recency.** Keep items published within a configurable look-back window
  (default 36h, see :data:`DEFAULT_WINDOW_HOURS`).
* **Dedupe.** Collapse the same story appearing in multiple feeds by normalized
  title and by link.

Edit :data:`FEEDS` to change what the brief reads.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import feedparser
import requests

from .models import FeedItem

log = logging.getLogger("cre_brief.feeds")

DEFAULT_WINDOW_HOURS = 36
_TIMEOUT = 25
_MAX_PER_FEED = 12
_MAX_TOTAL = 45
_SUMMARY_CHARS = 500

# A polite, real-browser-ish UA — some feeds reject the default python-requests UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CRE-Finance-Brief/1.0; +https://github.com/)"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


@dataclass(frozen=True)
class Feed:
    """A named RSS/Atom source. Add/remove entries in :data:`FEEDS`."""

    name: str
    url: str


# ===========================================================================
#  EDIT THIS LIST to change the brief's news sources.
#
#  Each run validates every feed and silently skips any that fail, so it is
#  safe to leave a flaky or wrong URL here — it just won't contribute that day.
#  Run `python -m cre_brief.feeds` (or read a workflow run's log) to see which
#  feeds resolve.
#
#  Tip for finding a site's real feed URL: try `/feed/`, `/rss/`, or `/feed.xml`;
#  or open the page source and search for `application/rss+xml`; or use a feed
#  finder. See the README section "Editing the feeds".
# ===========================================================================
FEEDS: List[Feed] = [
    # Multifamily development & construction — the reader's day job (added
    # 2026-07; verify each in your next run's log or `python -m cre_brief.feeds`):
    Feed("Multifamily Dive", "https://www.multifamilydive.com/feeds/news/"),        # MF development, finance, operations
    Feed("Construction Dive", "https://www.constructiondive.com/feeds/news/"),      # construction costs, labor, materials
    Feed("Multi-Housing News", "https://www.multihousingnews.com/feed/"),           # MF news incl. development & finance
    Feed("NAHB Eye on Housing", "https://eyeonhousing.org/feed/"),                  # starts/permits/cost economics
    # Confirmed live on the first production run (2026-06-26):
    Feed("Wolf Street", "https://wolfstreet.com/feed/"),
    Feed("Commercial Observer", "https://commercialobserver.com/feed/"),
    Feed("Connect CRE", "https://www.connectcre.com/feed/"),
    Feed("Trepp TreppTalk", "https://www.trepp.com/trepptalk/rss.xml"),
    # Reliable, on-topic additions (verify in your first run's log):
    Feed("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),  # ideal for Fed Watch
    Feed("CRE Direct", "https://www.crenews.com/feed/"),                            # CMBS / CRE credit
    #
    # Parked — no usable public RSS feed was found at the guessed URL. If you
    # locate a real feed (see README "Editing the feeds"), uncomment and fix it:
    #   Feed("GlobeSt", "..."),       # globest.com/feed/ -> 404; feeds sit behind globest.com/rss/
    #   Feed("CRE Daily", "..."),     # credaily.com/feed/ -> valid XML but no items (email newsletter)
    #   Feed("Bisnow", "..."),        # no native RSS feed (only third-party generators)
    #   Feed("MBA Newsroom", "..."),  # no native RSS feed found
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _SUMMARY_CHARS:
        text = text[: _SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def _entry_datetime(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        tm = getattr(entry, attr, None)
        if tm:
            try:
                return datetime(*tm[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _norm_title(title: str) -> str:
    return _WS_RE.sub(" ", re.sub(r"[^\w\s]", "", title.lower())).strip()


def fetch_feed(feed: Feed, cutoff: datetime, session: requests.Session) -> List[FeedItem]:
    """Fetch and parse a single feed, returning recent items (or [] on any failure)."""
    try:
        resp = session.get(feed.url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Feed '%s' fetch failed (%s): %s", feed.name, feed.url, exc)
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        log.warning("Feed '%s' did not parse as valid RSS/Atom: %s", feed.name, parsed.bozo)
        return []
    if not parsed.entries:
        log.warning("Feed '%s' parsed but contained no entries", feed.name)
        return []

    items: List[FeedItem] = []
    for entry in parsed.entries[: _MAX_PER_FEED * 2]:
        title = _clean(getattr(entry, "title", ""))
        link = getattr(entry, "link", "") or ""
        if not title:
            continue
        when = _entry_datetime(entry)
        # Keep dated items inside the window; keep undated items (feeds rarely
        # backfill, and dropping them would silently lose fresh stories).
        if when is not None and when < cutoff:
            continue
        summary = _clean(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        items.append(
            FeedItem(
                index=-1,  # assigned after global dedupe
                title=title,
                source=feed.name,
                link=link,
                summary=summary,
                published=when,
            )
        )
        if len(items) >= _MAX_PER_FEED:
            break

    log.info("Feed '%s': %d recent item(s)", feed.name, len(items))
    return items


def collect_items(
    feeds: Optional[List[Feed]] = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now: Optional[datetime] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[List[FeedItem], List[str]]:
    """Fetch all feeds, dedupe, and return ``(items, sources_used)``.

    ``sources_used`` is the list of feed names that contributed at least one
    item this run — used for the footer and logging.
    """
    feeds = feeds if feeds is not None else FEEDS
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    session = session or requests.Session()

    seen_titles: set = set()
    seen_links: set = set()
    collected: List[FeedItem] = []
    sources_used: List[str] = []

    for feed in feeds:
        feed_items = fetch_feed(feed, cutoff, session)
        contributed = False
        for item in feed_items:
            key_title = _norm_title(item.title)
            key_link = item.link.split("?")[0].rstrip("/").lower() if item.link else ""
            if key_title and key_title in seen_titles:
                continue
            if key_link and key_link in seen_links:
                continue
            if key_title:
                seen_titles.add(key_title)
            if key_link:
                seen_links.add(key_link)
            collected.append(item)
            contributed = True
            if len(collected) >= _MAX_TOTAL:
                break
        if contributed:
            sources_used.append(feed.name)
        if len(collected) >= _MAX_TOTAL:
            log.info("Hit max total items (%d); stopping feed collection", _MAX_TOTAL)
            break

    # Newest first, then assign stable indices the LLM can reference.
    collected.sort(key=lambda it: it.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for i, item in enumerate(collected):
        item.index = i

    log.info("Collected %d unique item(s) from %d source(s)", len(collected), len(sources_used))
    return collected, sources_used


def items_for_prompt(items: List[FeedItem]) -> str:
    """Render the deduped items as a compact, indexed block for the LLM prompt."""
    blocks = []
    for it in items:
        summary = f"\n  summary: {it.summary}" if it.summary else ""
        blocks.append(
            f"[{it.index}] {it.title}\n  source: {it.source}\n  link: {it.link}{summary}"
        )
    return "\n\n".join(blocks)


def _diagnose() -> None:
    """`python -m cre_brief.feeds` — check which feeds resolve and how many items each yields."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    session = requests.Session()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_WINDOW_HOURS)
    print(f"Validating {len(FEEDS)} feed(s); window = last {DEFAULT_WINDOW_HOURS}h\n")
    ok = 0
    for feed in FEEDS:
        items = fetch_feed(feed, cutoff, session)
        status = f"OK  ({len(items)} item(s))" if items else "FAILED / empty — will be skipped"
        if items:
            ok += 1
        print(f"  {'✓' if items else '✗'} {feed.name:<22} {status}\n     {feed.url}")
    print(f"\n{ok}/{len(FEEDS)} feed(s) resolved with recent items.")


if __name__ == "__main__":
    _diagnose()

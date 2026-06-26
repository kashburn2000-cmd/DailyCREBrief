"""Turn ground-truth FRED facts + deduped news items into the brief's prose.

The contract with the model is strict and enforced structurally:

* Rate numbers are supplied as immutable facts and the model is told never to
  emit or alter one. The Tape itself is built in code, not here.
* For "Headlines & Deal Flow" the model returns *indices* into the supplied
  item list plus a one-line takeaway; the real title/source/link are attached in
  code afterward, so the model cannot fabricate a source or URL.
* Empty sections are allowed — the model is told to say so briefly, not invent.

If Gemini fails entirely, we degrade gracefully to empty prose so The Tape (the
most important, fully-factual part) still goes out.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .gemini import GeminiClient, GeminiError
from .models import FeedItem, Headline, Synthesis, TapeRow

log = logging.getLogger("cre_brief.synthesize")

MAX_HEADLINES = 6
MIN_HEADLINES = 3  # spec target is 3-6 bullets; backfill toward 3 when material exists

# --- Editorial priority: Fed/rates first, industry next, single deals last ----
# Keyword buckets used to rank items before they reach the model and the backfill.
_FED_RATE_KW = (
    "fed", "fomc", "powell", "central bank", "monetary", "rate cut", "rate hike",
    "interest rate", "rates", "yield", "treasury", "inflation", "cpi", "pce",
    "basis point", " bps", "sofr", "fed funds", "rate-cut", "rate-hike", "tightening",
    "easing", "dot plot", "jobs report", "payroll", "gdp", "recession", "soft landing",
)
_INDUSTRY_KW = (
    "cmbs", "delinquenc", "distress", "default", "credit", "lending", "loan",
    "debt", "maturity wall", "refinanc", "spread", "bank", "regulat", "cap rate",
    "valuation", "occupancy", "vacancy", "capital markets", "issuance", "originations",
    "fund", "office", "multifamily", "industrial", "retail sector", "data center",
)
# Single-property / personnel transactions the reader does NOT want.
_DEAL_KW = (
    "acquire", "acquisition", "sells", " sold ", "buys", "purchase", "snaps up",
    "signs lease", "leases ", "inks", "joint venture", " jv ", "breaks ground",
    "tops out", "groundbreak", "names ", "hires", "appoints", "promotes", "taps ",
)


def _relevance_score(item: FeedItem) -> int:
    """Higher = more aligned with what this reader cares about (Fed/rates/industry)."""
    text = f" {item.title} {item.summary} ".lower()
    score = 0
    if any(k in text for k in _FED_RATE_KW):
        score += 3
    if any(k in text for k in _INDUSTRY_KW):
        score += 2
    if any(k in text for k in _DEAL_KW):
        score -= 2
    return score


def prioritize_items(items: List[FeedItem]) -> List[FeedItem]:
    """Reorder by editorial relevance (then recency) and reassign indices.

    Run before building the prompt so the model sees — and the backfill draws
    from — the most rate/Fed/industry-relevant stories first.
    """
    def sort_key(it: FeedItem):
        ts = it.published.timestamp() if it.published else 0
        return (-_relevance_score(it), -ts)

    ordered = sorted(items, key=sort_key)
    for i, it in enumerate(ordered):
        it.index = i
    return ordered

# Gemini structured-output schema (OpenAPI subset — no additionalProperties).
SYNTHESIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "tape_context": {"type": "string"},
        "fed_watch": {"type": "string"},
        "cmbs_watch": {"type": "string"},
        "headlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_index": {"type": "integer"},
                    "takeaway": {"type": "string"},
                },
                "required": ["item_index", "takeaway"],
            },
        },
        "one_to_watch": {"type": "string"},
    },
    "required": ["tape_context", "fed_watch", "cmbs_watch", "headlines", "one_to_watch"],
    "propertyOrdering": [
        "tape_context",
        "fed_watch",
        "cmbs_watch",
        "headlines",
        "one_to_watch",
    ],
}

SYSTEM_PROMPT = (
    "You are the editor of the 'CRE Finance Brief', a daily commercial real "
    "estate finance digest for finance professionals. The reader cares MOST about "
    "the Fed, monetary policy, interest rates and the rate outlook; next about "
    "broad industry and CRE-credit trends; and does NOT care about individual "
    "property transactions. Write tight, factual, skimmable copy.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Use ONLY the supplied rate facts and news items. Never invent figures, "
    "company names, deal sizes, people, dates or events.\n"
    "2. The rate numbers in THE TAPE FACTS are ground truth. Do not restate, "
    "recompute, round, or contradict them, and never introduce a rate number "
    "that is not in those facts.\n"
    "3. If a section has no relevant supplied material, say so in one short, "
    "honest sentence (e.g. 'No fresh Fed-policy items in today's feeds.'). Do "
    "NOT pad or fabricate.\n"
    "4. PRIORITIZE Fed / monetary-policy / rates / inflation and broad industry "
    "trends. DE-PRIORITIZE single-property deals (one building being bought, "
    "sold, leased, or refinanced) — skip them unless they signal a market-wide "
    "trend.\n"
    "5. No hype, no advice, no price targets. Neutral, professional tone.\n"
    "6. Keep every section short — a reader skims the whole brief in 60 seconds."
)


def _build_user_prompt(tape_facts: str, items_block: str, has_items: bool) -> str:
    news_section = items_block if has_items else "(No news items were retrieved in the look-back window.)"
    return f"""THE TAPE FACTS (ground truth — never alter or restate as new numbers):
{tape_facts}

NEWS ITEMS (each prefixed with an [index]; already ordered with the most
rate/Fed/industry-relevant first. Reference indices for headlines):
{news_section}

Produce a JSON object with these fields:

- "tape_context": ONE short sentence (max ~25 words) of plain-English context
  for the rate moves above. Describe direction/shape only; do not write any
  specific rate number. If moves are minimal, say markets were quiet.

- "fed_watch": THE PRIORITY SECTION. 2-4 sentences synthesizing the
  monetary-policy, Fed-official, inflation, and rate-outlook material from the
  news above, and how it bears on the rate picture. Be substantive but factual,
  strictly from the supplied items. If there is genuinely no such material, say
  so briefly.

- "cmbs_watch": 1-3 sentences synthesizing ONLY CMBS, CRE-credit, delinquency,
  maturity-wall, or distress items from the news above. If none, say so briefly.

- "headlines": an array of 3 to 6 objects, each {{"item_index": <int>,
  "takeaway": "<one-line, ~15-word takeaway>"}}. Choose the most relevant
  INDUSTRY and RATE/POLICY items by their [index] — macro, lending, regulation,
  sector and market trends. Do NOT pick single-property transactions unless they
  illustrate a broader trend. Only use indices that appear above. If fewer than
  3 qualifying items exist, return only those.

- "one_to_watch": ONE forward-looking sentence about what to watch next,
  grounded in the supplied material (a data release, Fed event, or theme already
  present above). No speculation beyond the material.

Return ONLY the JSON object."""


def _coerce_headlines(raw_headlines: Any, items_by_index: Dict[int, FeedItem]) -> List[Headline]:
    headlines: List[Headline] = []
    seen: set = set()
    if not isinstance(raw_headlines, list):
        return headlines
    for entry in raw_headlines:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("item_index")
        takeaway = (entry.get("takeaway") or "").strip()
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if idx in seen or idx not in items_by_index:
            # Drop hallucinated/duplicate indices — links must be real.
            continue
        seen.add(idx)
        item = items_by_index[idx]
        headlines.append(
            Headline(
                takeaway=takeaway or item.title,
                title=item.title,
                source=item.source,
                link=item.link,
            )
        )
        if len(headlines) >= MAX_HEADLINES:
            break
    return headlines


def _short(text: str, limit: int = 140) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _backfill_headlines(headlines: List[Headline], items: List[FeedItem]) -> List[Headline]:
    """Pad to MIN_HEADLINES from the freshest unused items when the model under-selects.

    The empty-section fallback in the renderer still applies on genuinely empty
    news days (no items at all); this only fires when material exists but Gemini
    returned fewer than three usable, distinct headlines.
    """
    if len(headlines) >= MIN_HEADLINES:
        return headlines
    used_links = {h.link for h in headlines if h.link}
    used_titles = {h.title for h in headlines}
    for item in items:  # items are already sorted newest-first
        if len(headlines) >= MIN_HEADLINES:
            break
        if (item.link and item.link in used_links) or item.title in used_titles:
            continue
        headlines.append(
            Headline(
                takeaway=_short(item.summary) or item.title,
                title=item.title,
                source=item.source,
                link=item.link,
            )
        )
        used_links.add(item.link)
        used_titles.add(item.title)
    return headlines


def synthesize(
    client: GeminiClient,
    tape: List[TapeRow],
    items: List[FeedItem],
    tape_facts: str,
    items_block: str,
) -> Synthesis:
    """Call Gemini and return a :class:`Synthesis`, degrading gracefully on failure."""
    items_by_index = {it.index: it for it in items}
    prompt = _build_user_prompt(tape_facts, items_block, has_items=bool(items))

    try:
        # Low temperature -> stable, focused selection (less run-to-run shuffling).
        data = client.generate_json(prompt, SYNTHESIS_SCHEMA, system=SYSTEM_PROMPT, temperature=0.2)
    except GeminiError as exc:
        log.error("Gemini synthesis failed; sending The Tape with empty prose. %s", exc)
        return Synthesis()

    if not isinstance(data, dict):
        log.error("Gemini returned non-object JSON; using empty prose")
        return Synthesis()

    headlines = _coerce_headlines(data.get("headlines"), items_by_index)
    headlines = _backfill_headlines(headlines, items)
    synthesis = Synthesis(
        tape_context=str(data.get("tape_context", "")).strip(),
        fed_watch=str(data.get("fed_watch", "")).strip(),
        cmbs_watch=str(data.get("cmbs_watch", "")).strip(),
        headlines=headlines,
        one_to_watch=str(data.get("one_to_watch", "")).strip(),
    )
    log.info(
        "Synthesis complete: %d headline(s), fed=%dch, cmbs=%dch",
        len(synthesis.headlines), len(synthesis.fed_watch), len(synthesis.cmbs_watch),
    )
    return synthesis

"""Honor unsubscribes automatically: fetch the suppression list before sending.

The unsubscribe worker (see ``unsubscribe-worker/``) records one-click and
footer-button unsubscribes in Workers KV and exposes them at ``GET /list``
(Bearer-authenticated). ``fetch_suppressed`` pulls that list; ``run`` drops
matching recipients before delivery.

Fail-closed by design: if the feed is configured but unreachable, the caller
aborts the send. Mailing someone who already unsubscribed is a spam-complaint
(and CAN-SPAM) risk that outweighs skipping one edition.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Set, Tuple

import requests

log = logging.getLogger("cre_brief.suppression")

_TIMEOUT = 30
_BACKOFF = (2, 4, 8)
_RETRY_STATUS = {429, 500, 502, 503}


class SuppressionError(RuntimeError):
    """The suppression feed is configured but could not be read."""


def fetch_suppressed(
    url: str,
    secret: str = "",
    session: Optional[requests.Session] = None,
    sleeper=time.sleep,
) -> Set[str]:
    """Return the set of unsubscribed addresses (lowercased) from ``url``.

    Expects a JSON array of email strings. Retries transient failures, then
    raises :class:`SuppressionError` so the caller can refuse to send.
    """
    session = session or requests.Session()
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}

    last_error: Optional[str] = None
    for attempt in range(len(_BACKOFF) + 1):
        try:
            resp = session.get(url, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise SuppressionError(f"suppression feed returned non-JSON: {resp.text[:200]}")
                if not isinstance(data, list):
                    raise SuppressionError(
                        f"suppression feed must be a JSON array, got {type(data).__name__}"
                    )
                emails = {e.strip().lower() for e in data if isinstance(e, str) and "@" in e}
                log.info("Suppression feed: %d unsubscribed address(es)", len(emails))
                return emails
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in _RETRY_STATUS:
                break
        if attempt < len(_BACKOFF):
            delay = _BACKOFF[attempt]
            log.warning("Suppression feed fetch failed (%s); retry in %ss", last_error, delay)
            sleeper(delay)

    raise SuppressionError(f"could not read suppression feed {url}: {last_error}")


def filter_recipients(recipients: List[str], suppressed: Set[str]) -> Tuple[List[str], List[str]]:
    """Split ``recipients`` into (kept, dropped) against the suppressed set."""
    kept: List[str] = []
    dropped: List[str] = []
    for recipient in recipients:
        if recipient.strip().lower() in suppressed:
            dropped.append(recipient)
        else:
            kept.append(recipient)
    return kept, dropped

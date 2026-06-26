"""Email delivery via Resend (free tier).

Sends one multipart message (HTML + plaintext fallback) **per recipient** rather
than one shared email. For a small fixed list this is cleaner: recipients don't
see each other's addresses, and one bad address doesn't block the rest.

Docs: https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

log = logging.getLogger("cre_brief.mailer")

RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = 30
_BACKOFF = (1, 2, 4)  # Resend free tier throttles ~2 req/s; brief backoff on 429/5xx
RETRY_STATUS = {429, 500, 502, 503}


@dataclass
class SendResult:
    sent: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.sent) and not self.failed


def _send_one(
    session: requests.Session,
    api_key: str,
    sender: str,
    recipient: str,
    subject: str,
    html: str,
    text: str,
    sleeper,
) -> bool:
    payload = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": html,
        "text": text,  # plaintext fallback -> multipart/alternative
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    attempts = len(_BACKOFF) + 1
    last_error: Optional[str] = None
    for attempt in range(attempts):
        try:
            resp = session.post(RESEND_URL, headers=headers, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
        else:
            if resp.status_code in (200, 201):
                msg_id = ""
                try:
                    msg_id = resp.json().get("id", "")
                except ValueError:
                    pass
                log.info("Sent to %s (id=%s)", recipient, msg_id or "?")
                return True
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRY_STATUS:
                log.error("Send to %s failed (not retryable): %s", recipient, last_error)
                return False

        if attempt < len(_BACKOFF):
            delay = _BACKOFF[attempt]
            log.warning("Send to %s failed (%s); retry in %ss", recipient, last_error, delay)
            sleeper(delay)

    log.error("Send to %s failed after %d attempts: %s", recipient, attempts, last_error)
    return False


def send_brief(
    api_key: str,
    sender: str,
    recipients: List[str],
    subject: str,
    html: str,
    text: str,
    session: Optional[requests.Session] = None,
    sleeper=time.sleep,
) -> SendResult:
    """Send the brief to every recipient. Returns a :class:`SendResult`."""
    session = session or requests.Session()
    result = SendResult()
    for recipient in recipients:
        if _send_one(session, api_key, sender, recipient, subject, html, text, sleeper):
            result.sent.append(recipient)
        else:
            result.failed.append(recipient)
    log.info("Delivery complete: %d sent, %d failed", len(result.sent), len(result.failed))
    return result

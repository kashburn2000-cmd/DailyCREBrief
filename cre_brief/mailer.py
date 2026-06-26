"""Email delivery — two free options, no code change to switch between them.

* **Gmail (SMTP)** — send straight from your own Gmail account. **No domain
  needed**, which makes it the simplest choice for personal use. Requires a
  Google "App Password" (2-Step Verification must be on). ~500 recipients/day.
* **Resend (HTTP API)** — needs a verified sending domain, but gives you a
  branded from-address. Free tier: 100/day, 3,000/month.

Both send one multipart message (HTML + plaintext fallback) **per recipient** so
recipients never see each other's addresses, and one bad address can't block the
rest. ``cre_brief.brief.run`` picks the method from ``Config.delivery_method``.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List, Optional

import requests

log = logging.getLogger("cre_brief.mailer")

# --- Resend -----------------------------------------------------------------
RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = 30
_BACKOFF = (1, 2, 4)  # Resend free tier throttles ~2 req/s; brief backoff on 429/5xx
RETRY_STATUS = {429, 500, 502, 503}

# --- Gmail ------------------------------------------------------------------
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


@dataclass
class SendResult:
    sent: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.sent) and not self.failed


# ===========================================================================
#  Gmail (SMTP) — no domain required
# ===========================================================================
def _build_mime(sender_name: str, sender_addr: str, recipient: str,
                subject: str, html: str, text: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender_addr))
    msg["To"] = recipient
    # Plain part first, HTML second — clients render the last part they support.
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def send_via_gmail(
    gmail_address: str,
    app_password: str,
    sender_name: str,
    recipients: List[str],
    subject: str,
    html: str,
    text: str,
    smtp_factory=None,
) -> SendResult:
    """Send the brief from a Gmail account over SMTP (TLS).

    ``app_password`` is a 16-char Google App Password (spaces are ignored). Gmail
    requires the From address to be the authenticated account, so emails come
    from ``gmail_address`` with ``sender_name`` as the display name.
    """
    result = SendResult()
    password = (app_password or "").replace(" ", "")

    try:
        if smtp_factory is not None:
            server = smtp_factory()
        else:
            server = smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=_TIMEOUT)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        server.login(gmail_address, password)
    except Exception as exc:  # login/connection failure dooms every recipient
        log.error("Gmail SMTP connect/login failed: %s", exc)
        result.failed = list(recipients)
        return result

    try:
        for recipient in recipients:
            msg = _build_mime(sender_name, gmail_address, recipient, subject, html, text)
            try:
                server.sendmail(gmail_address, [recipient], msg.as_string())
                log.info("Sent to %s via Gmail", recipient)
                result.sent.append(recipient)
            except Exception as exc:
                log.error("Gmail send to %s failed: %s", recipient, exc)
                result.failed.append(recipient)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    log.info("Gmail delivery: %d sent, %d failed", len(result.sent), len(result.failed))
    return result


# ===========================================================================
#  Resend (HTTP API) — needs a verified domain
# ===========================================================================
def _resend_one(
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
                log.info("Sent to %s via Resend (id=%s)", recipient, msg_id or "?")
                return True
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRY_STATUS:
                log.error("Resend to %s failed (not retryable): %s", recipient, last_error)
                return False

        if attempt < len(_BACKOFF):
            delay = _BACKOFF[attempt]
            log.warning("Resend to %s failed (%s); retry in %ss", recipient, last_error, delay)
            sleeper(delay)

    log.error("Resend to %s failed after %d attempts: %s", recipient, attempts, last_error)
    return False


def send_via_resend(
    api_key: str,
    sender: str,
    recipients: List[str],
    subject: str,
    html: str,
    text: str,
    session: Optional[requests.Session] = None,
    sleeper=time.sleep,
) -> SendResult:
    """Send the brief to every recipient through Resend."""
    session = session or requests.Session()
    result = SendResult()
    for recipient in recipients:
        if _resend_one(session, api_key, sender, recipient, subject, html, text, sleeper):
            result.sent.append(recipient)
        else:
            result.failed.append(recipient)
    log.info("Resend delivery: %d sent, %d failed", len(result.sent), len(result.failed))
    return result

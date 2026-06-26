"""Configuration: everything sensitive or deployment-specific comes from env vars.

Locally these are loaded from a ``.env`` file (see ``.env.example``); in GitHub
Actions they come from repository **Secrets**. Nothing secret is ever hardcoded.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

try:  # Local convenience; a no-op in CI where the file is absent.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 only
    ZoneInfo = None  # type: ignore

log = logging.getLogger("cre_brief.config")

# Default free-tier Gemini Flash model. Kept as a constant *and* overridable via
# the GEMINI_MODEL env var so it is trivial to swap when Google renames models.
#
# The default is the ROLLING ALIAS `gemini-flash-latest`, which Google maps to the
# current stable free-tier Flash model. For an unattended daily job this is the
# most resilient choice: it keeps working across model deprecations with no code
# change. (Per Google's deprecation notices checked 2026-06, the pinned
# `gemini-2.5-flash` was retired and `gemini-3.5-flash` became the current GA
# Flash — exactly the churn the alias insulates you from.)
#
# Prefer a pinned version for reproducibility? Set GEMINI_MODEL to e.g.
# `gemini-3.5-flash` (current GA) or `gemini-3.1-flash-lite` (lighter). Always
# confirm the live model names + free-tier limits in Google AI Studio.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_TIMEZONE = "America/New_York"


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing for the requested mode."""


DEFAULT_SENDER_NAME = "CRE Finance Brief"


@dataclass
class Config:
    gemini_api_key: str = ""
    fred_api_key: str = ""
    # Delivery — Gmail (no domain needed) OR Resend (needs a verified domain).
    gmail_address: str = ""
    gmail_app_password: str = ""
    resend_api_key: str = ""
    sender_email: str = ""
    sender_name: str = DEFAULT_SENDER_NAME
    # Optional override: "gmail" or "resend". Blank = auto-detect. Use this to
    # force a method even when the other's secrets are still present.
    delivery_override: str = ""
    recipients: List[str] = field(default_factory=list)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    news_window_hours: int = 36
    timezone: str = DEFAULT_TIMEZONE

    @property
    def _gmail_ready(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password)

    @property
    def _resend_ready(self) -> bool:
        return bool(self.resend_api_key and self.sender_email)

    @property
    def delivery_method(self) -> Optional[str]:
        """Which method to send with, or None if the chosen one isn't configured.

        ``DELIVERY_METHOD`` (delivery_override) forces a choice when set; otherwise
        Gmail wins if configured (it needs no domain), else Resend.
        """
        override = (self.delivery_override or "").lower()
        if override == "gmail":
            return "gmail" if self._gmail_ready else None
        if override == "resend":
            return "resend" if self._resend_ready else None
        if self._gmail_ready:
            return "gmail"
        if self._resend_ready:
            return "resend"
        return None

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        try:
            window = int(os.getenv("NEWS_WINDOW_HOURS", "36"))
        except ValueError:
            log.warning("NEWS_WINDOW_HOURS not an int; defaulting to 36")
            window = 36
        if window <= 0:
            log.warning("NEWS_WINDOW_HOURS must be positive (got %d); defaulting to 36", window)
            window = 36
        elif window > 168:  # a week — guard against an accidental huge look-back
            log.warning("NEWS_WINDOW_HOURS capped at 168 (got %d)", window)
            window = 168
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            fred_api_key=os.getenv("FRED_API_KEY", "").strip(),
            gmail_address=os.getenv("GMAIL_ADDRESS", "").strip(),
            gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", "").strip(),
            resend_api_key=os.getenv("RESEND_API_KEY", "").strip(),
            sender_email=os.getenv("SENDER_EMAIL", "").strip(),
            sender_name=os.getenv("SENDER_NAME", DEFAULT_SENDER_NAME).strip() or DEFAULT_SENDER_NAME,
            delivery_override=os.getenv("DELIVERY_METHOD", "").strip(),
            recipients=_split_csv(os.getenv("RECIPIENTS")),
            gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL,
            news_window_hours=window,
            timezone=os.getenv("BRIEF_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE,
        )

    # ------------------------------------------------------------------
    def validate(self, require_send: bool) -> None:
        """Raise :class:`ConfigError` if anything required for the mode is missing.

        Content generation always needs Gemini + FRED keys. Delivery additionally
        needs at least one recipient and a configured delivery method — EITHER
        Gmail (GMAIL_ADDRESS + GMAIL_APP_PASSWORD, no domain needed) OR Resend
        (RESEND_API_KEY + SENDER_EMAIL, needs a verified domain).
        """
        missing: List[str] = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.fred_api_key:
            missing.append("FRED_API_KEY")
        if require_send:
            if not self.recipients:
                missing.append("RECIPIENTS")
            if self.delivery_method is None:
                override = (self.delivery_override or "").lower()
                if override == "resend":
                    missing.append("RESEND_API_KEY + SENDER_EMAIL (DELIVERY_METHOD=resend)")
                elif override == "gmail":
                    missing.append("GMAIL_ADDRESS + GMAIL_APP_PASSWORD (DELIVERY_METHOD=gmail)")
                else:
                    missing.append(
                        "a delivery method — set either GMAIL_ADDRESS + GMAIL_APP_PASSWORD "
                        "(no domain needed) or RESEND_API_KEY + SENDER_EMAIL"
                    )
        if missing:
            raise ConfigError(
                "Missing required configuration: "
                + "; ".join(missing)
                + ". Set them as env vars / GitHub Actions secrets (see .env.example)."
            )

    # ------------------------------------------------------------------
    def now_local(self) -> datetime:
        if ZoneInfo is not None:
            try:
                return datetime.now(ZoneInfo(self.timezone))
            except Exception:
                log.warning("Unknown timezone %r; falling back to UTC", self.timezone)
        return datetime.now(timezone.utc)

    def date_str(self) -> str:
        """e.g. 'Friday, June 26, 2026' — localized to the brief's timezone."""
        # %-d is platform-specific; format the day without zero-padding portably.
        local = self.now_local()
        return local.strftime("%A, %B ") + str(local.day) + local.strftime(", %Y")

    def timestamp_str(self) -> str:
        local = self.now_local()
        tz_abbr = local.strftime("%Z") or self.timezone
        return local.strftime("%Y-%m-%d %H:%M ") + tz_abbr

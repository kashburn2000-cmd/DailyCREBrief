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
# NOTE (verified against ai.google.dev, 2026-06): `gemini-2.5-flash` was
# DEPRECATED on 2026-06-17 and will eventually stop serving (calls 404), so it is
# NOT a safe default. The current GA free-tier Flash model is `gemini-3.5-flash`.
# Alternatives you can drop into GEMINI_MODEL without code changes:
#   * gemini-flash-latest   — alias that auto-tracks the newest stable Flash
#   * gemini-3.1-flash-lite  — cheaper/lighter, also free-tier
# Always confirm the live model name + free-tier limits in Google AI Studio.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_TIMEZONE = "America/New_York"


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing for the requested mode."""


@dataclass
class Config:
    gemini_api_key: str = ""
    fred_api_key: str = ""
    resend_api_key: str = ""
    sender_email: str = ""
    recipients: List[str] = field(default_factory=list)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    news_window_hours: int = 36
    timezone: str = DEFAULT_TIMEZONE

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        try:
            window = int(os.getenv("NEWS_WINDOW_HOURS", "36"))
        except ValueError:
            log.warning("NEWS_WINDOW_HOURS not an int; defaulting to 36")
            window = 36
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            fred_api_key=os.getenv("FRED_API_KEY", "").strip(),
            resend_api_key=os.getenv("RESEND_API_KEY", "").strip(),
            sender_email=os.getenv("SENDER_EMAIL", "").strip(),
            recipients=_split_csv(os.getenv("RECIPIENTS")),
            gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL,
            news_window_hours=window,
            timezone=os.getenv("BRIEF_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE,
        )

    # ------------------------------------------------------------------
    def validate(self, require_send: bool) -> None:
        """Raise :class:`ConfigError` if anything required for the mode is missing.

        Content generation always needs Gemini + FRED keys. Delivery additionally
        needs the Resend key, a verified sender, and at least one recipient.
        """
        missing: List[str] = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.fred_api_key:
            missing.append("FRED_API_KEY")
        if require_send:
            if not self.resend_api_key:
                missing.append("RESEND_API_KEY")
            if not self.sender_email:
                missing.append("SENDER_EMAIL")
            if not self.recipients:
                missing.append("RECIPIENTS")
        if missing:
            raise ConfigError(
                "Missing required configuration: "
                + ", ".join(missing)
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

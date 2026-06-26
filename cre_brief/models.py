"""Shared dataclasses for the CRE Finance Brief.

These are deliberately plain data containers. The important design rule lives
here in spirit: every *number* on The Tape originates from FRED and is formatted
in code (see :mod:`cre_brief.fred`). The language model only ever receives these
as read-only context and is never allowed to emit or alter a rate figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class TapeRow:
    """One row of The Tape — a single rate/series with level and day-over-day move."""

    key: str                       # FRED-ish key, e.g. "DGS10" or "FEDFUNDS_TARGET"
    label: str                     # human label, e.g. "10Y Treasury"
    level: Optional[float]         # latest numeric level (None if unavailable)
    level_display: str             # formatted level, e.g. "4.28%" / "4.33–4.58%" / "n/a"
    change_bps: Optional[float]    # day-over-day change in basis points (None if n/a)
    change_display: str            # formatted change, e.g. "+3 bps" / "−2 bps" / "flat" / "n/a"
    as_of: Optional[str] = None         # ISO date of the latest observation
    prior_as_of: Optional[str] = None   # ISO date of the prior observation used for the delta

    @property
    def direction(self) -> str:
        """'up', 'down' or 'flat' — used to colour the change cell."""
        if self.change_bps is None:
            return "na"
        if self.change_bps > 0:
            return "up"
        if self.change_bps < 0:
            return "down"
        return "flat"


@dataclass
class FeedItem:
    """A single normalized news item pulled from an RSS/Atom feed."""

    index: int                     # stable id Gemini references to attach real links
    title: str
    source: str
    link: str
    summary: str
    published: Optional[datetime] = None

    def published_display(self) -> str:
        if not self.published:
            return ""
        return self.published.strftime("%b %d, %H:%M UTC")


@dataclass
class Headline:
    """A 'Headlines & Deal Flow' bullet: a one-line takeaway tied to a real source."""

    takeaway: str
    title: str
    source: str
    link: str


@dataclass
class Synthesis:
    """Everything the language model is allowed to write — prose only, no numbers it invented."""

    tape_context: str = ""
    fed_watch: str = ""
    cmbs_watch: str = ""
    headlines: List[Headline] = field(default_factory=list)
    one_to_watch: str = ""


@dataclass
class Brief:
    """The fully-assembled newsletter, ready to render and send."""

    date_str: str                  # "Friday, June 26, 2026"
    subject: str                   # "CRE Finance Brief — Friday, June 26, 2026"
    tape: List[TapeRow]
    synthesis: Synthesis
    sources_used: List[str]        # feed names that resolved this run
    fred_series_used: List[str]    # FRED series ids that returned data
    generated_at_utc: str          # ISO-ish timestamp string for the footer
    model_name: str                # Gemini model used (for the footer)
    item_count: int = 0            # number of news items considered

"""FRED client and "The Tape" builder.

Every rate number in the newsletter is produced here, from the Federal Reserve
Bank of St. Louis FRED API, and formatted in code. The language model receives
these figures only as read-only ground truth — it never generates or edits a
rate value (that is the whole anti-hallucination strategy).

FRED daily series omit weekends/holidays and use ``"."`` for missing values, so
we pull a short descending window per series and take the two most recent
*numeric* observations as "latest" and "prior business day".

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from .models import TapeRow

log = logging.getLogger("cre_brief.fred")

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Pull a generous-but-small window so we step over holidays/missing values and
# still always find the two most recent valid observations.
_WINDOW = 12
_TIMEOUT = 30


@dataclass
class SeriesPoint:
    """Latest + prior valid numeric observations for a single FRED series."""

    series_id: str
    latest: Optional[float] = None
    latest_date: Optional[str] = None
    prior: Optional[float] = None
    prior_date: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.latest is not None

    @property
    def change_pp(self) -> Optional[float]:
        """Day-over-day change in *percentage points* (None if either side missing)."""
        if self.latest is None or self.prior is None:
            return None
        return self.latest - self.prior


class FredClient:
    """Minimal FRED REST client. One method, one job: fetch observations."""

    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self.api_key = api_key
        self.session = session or requests.Session()

    def fetch_point(self, series_id: str) -> SeriesPoint:
        """Return the two most recent valid numeric observations for ``series_id``.

        Network/parse errors are swallowed into an empty SeriesPoint and logged,
        so one bad series never breaks the whole run.
        """
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",   # newest first
            "limit": _WINDOW,
        }
        try:
            resp = self.session.get(FRED_OBS_URL, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
        except requests.RequestException as exc:
            log.warning("FRED request failed for %s: %s", series_id, exc)
            return SeriesPoint(series_id=series_id)
        except ValueError as exc:  # JSON decode
            log.warning("FRED returned non-JSON for %s: %s", series_id, exc)
            return SeriesPoint(series_id=series_id)

        valid: List[Tuple[str, float]] = []
        for obs in observations:
            raw = obs.get("value", ".")
            if raw in (".", "", None):
                continue
            try:
                valid.append((obs.get("date", ""), float(raw)))
            except (TypeError, ValueError):
                continue
            if len(valid) >= 2:
                break

        point = SeriesPoint(series_id=series_id)
        if valid:
            point.latest_date, point.latest = valid[0]
        if len(valid) >= 2:
            point.prior_date, point.prior = valid[1]
        if not point.ok:
            log.warning("FRED series %s returned no usable observations", series_id)
        return point


# ---------------------------------------------------------------------------
# Formatting helpers — all rate numbers become strings here.
# ---------------------------------------------------------------------------
def _fmt_pct(value: Optional[float]) -> str:
    return f"{value:.2f}%" if value is not None else "n/a"


def _fmt_bps_level(value_pp: Optional[float]) -> str:
    """Format a spread expressed in percentage points as basis points, e.g. 0.45 -> '+45 bps'."""
    if value_pp is None:
        return "n/a"
    bps = round(value_pp * 100)
    sign = "+" if bps > 0 else ""
    return f"{sign}{bps} bps"


def _fmt_change(change_pp: Optional[float]) -> Tuple[Optional[float], str]:
    """Return (change_in_bps, display) from a percentage-point change."""
    if change_pp is None:
        return None, "n/a"
    bps = round(change_pp * 100)
    if bps == 0:
        return 0.0, "flat"
    sign = "+" if bps > 0 else "-"
    return float(bps), f"{sign}{abs(bps)} bps"


# Series fetched from FRED. Editing this list changes what The Tape covers.
# Ordered for a construction lender: the floating-rate indices construction
# loans price over (SOFR, 30-day average SOFR) up top with the curve points
# that drive perm/agency exit pricing (5Y/10Y) right behind them.
TREASURY_AND_RATE_SERIES = [
    ("SOFR", "SOFR"),
    ("SOFR30DAYAVG", "30-Day Avg SOFR"),
    ("DGS10", "10Y Treasury"),
    ("DGS5", "5Y Treasury"),
    ("DGS2", "2Y Treasury"),
    ("DGS30", "30Y Treasury"),
    ("DGS3MO", "3M Treasury"),
]
SPREAD_SERIES = ("T10Y2Y", "2s/10s Spread")
FED_TARGET_UPPER = "DFEDTARU"
FED_TARGET_LOWER = "DFEDTARL"

# Monthly Census/HUD series for the multifamily development pipeline
# ("Development Pulse"). Values are thousands of units, SAAR; the delta shown
# is month-over-month. Monthly data only changes ~once a month — the point is
# having the latest print and its direction at hand, not a daily move.
PULSE_SERIES = [
    ("HOUST5F", "MF Starts (5+ units)"),
    ("PERMIT5", "MF Permits (5+ units)"),
]

ALL_FRED_SERIES = (
    [s for s, _ in TREASURY_AND_RATE_SERIES]
    + [SPREAD_SERIES[0], FED_TARGET_UPPER, FED_TARGET_LOWER]
    + [s for s, _ in PULSE_SERIES]
)


def build_tape(client: FredClient) -> Tuple[List[TapeRow], List[str]]:
    """Fetch every series and assemble The Tape.

    Returns ``(rows, series_used)`` where ``series_used`` is the list of FRED
    series ids that actually returned data (for the footer / logging).
    """
    rows: List[TapeRow] = []
    used: List[str] = []

    # Standard percent-quoted rates.
    for series_id, label in TREASURY_AND_RATE_SERIES:
        point = client.fetch_point(series_id)
        change_bps, change_display = _fmt_change(point.change_pp)
        rows.append(
            TapeRow(
                key=series_id,
                label=label,
                level=point.latest,
                level_display=_fmt_pct(point.latest),
                change_bps=change_bps,
                change_display=change_display,
                as_of=point.latest_date,
                prior_as_of=point.prior_date,
            )
        )
        if point.ok:
            used.append(series_id)

    # 2s/10s spread — level shown in basis points, change in basis points.
    spread_id, spread_label = SPREAD_SERIES
    sp = client.fetch_point(spread_id)
    change_bps, change_display = _fmt_change(sp.change_pp)
    rows.append(
        TapeRow(
            key=spread_id,
            label=spread_label,
            level=sp.latest,
            level_display=_fmt_bps_level(sp.latest),
            change_bps=change_bps,
            change_display=change_display,
            as_of=sp.latest_date,
            prior_as_of=sp.prior_date,
        )
    )
    if sp.ok:
        used.append(spread_id)

    # Fed funds target range — combine lower + upper bounds into one row.
    upper = client.fetch_point(FED_TARGET_UPPER)
    lower = client.fetch_point(FED_TARGET_LOWER)
    if upper.ok and lower.ok:
        level_display = f"{lower.latest:.2f}–{upper.latest:.2f}%"  # en dash
    elif upper.ok:
        level_display = f"{upper.latest:.2f}% (upper)"
    elif lower.ok:
        level_display = f"{lower.latest:.2f}% (lower)"
    else:
        level_display = "n/a"
    # Day-over-day move tracked off whichever bound we have (moves only on policy
    # changes, and both bounds step together).
    bound = upper if upper.ok else lower
    change_bps, change_display = _fmt_change(bound.change_pp)
    rows.append(
        TapeRow(
            key="FEDFUNDS_TARGET",
            label="Fed Funds Target",
            level=bound.latest,
            level_display=level_display,
            change_bps=change_bps,
            change_display=change_display,
            as_of=bound.latest_date,
            prior_as_of=bound.prior_date,
        )
    )
    # Record only the bounds that actually returned data.
    if upper.ok:
        used.append(FED_TARGET_UPPER)
    if lower.ok:
        used.append(FED_TARGET_LOWER)

    return rows, used


def _fmt_units(value: Optional[float]) -> str:
    """Format a thousands-of-units SAAR level, e.g. 379.0 -> '379k SAAR'."""
    return f"{value:,.0f}k SAAR" if value is not None else "n/a"


def _fmt_pct_change(latest: Optional[float], prior: Optional[float]) -> Tuple[Optional[float], str]:
    """Return (percent_change, display) month-over-month, e.g. '+5.3% m/m'."""
    if latest is None or prior is None or prior == 0:
        return None, "n/a"
    pct = (latest - prior) / prior * 100.0
    if round(pct, 1) == 0:
        return 0.0, "flat"
    sign = "+" if pct > 0 else "-"
    return pct, f"{sign}{abs(pct):.1f}% m/m"


def build_pulse(client: FredClient) -> Tuple[List[TapeRow], List[str]]:
    """Fetch the monthly development-pipeline series (Development Pulse).

    Same shape as :func:`build_tape` — ``(rows, series_used)`` — but levels are
    thousands of units (SAAR) and the delta is month-over-month percent. The
    percent change is stored in ``change_bps`` purely so the renderer can colour
    the direction; it is never treated as basis points.
    """
    rows: List[TapeRow] = []
    used: List[str] = []
    for series_id, label in PULSE_SERIES:
        point = client.fetch_point(series_id)
        change_pct, change_display = _fmt_pct_change(point.latest, point.prior)
        rows.append(
            TapeRow(
                key=series_id,
                label=label,
                level=point.latest,
                level_display=_fmt_units(point.latest),
                change_bps=change_pct,
                change_display=change_display,
                as_of=point.latest_date,
                prior_as_of=point.prior_date,
            )
        )
        if point.ok:
            used.append(series_id)
    return rows, used


def pulse_facts_for_prompt(rows: List[TapeRow]) -> str:
    """Render the monthly pipeline series as ground-truth facts for the LLM."""
    lines = []
    for r in rows:
        as_of = f" (monthly print as of {r.as_of})" if r.as_of else ""
        lines.append(
            f"- {r.label}: {r.level_display}, month-over-month {r.change_display}{as_of}"
        )
    return "\n".join(lines)


def tape_facts_for_prompt(rows: List[TapeRow]) -> str:
    """Render The Tape as a compact, unambiguous block of ground-truth facts for the LLM.

    This is the *only* place rate numbers reach the model, and they are labelled
    as immutable facts in the prompt that consumes this string.
    """
    lines = []
    for r in rows:
        as_of = f" (as of {r.as_of})" if r.as_of else ""
        lines.append(f"- {r.label}: {r.level_display}, day-over-day {r.change_display}{as_of}")
    return "\n".join(lines)

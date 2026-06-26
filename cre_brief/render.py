"""Render a :class:`~cre_brief.models.Brief` into a multipart email body.

Two outputs:

* ``render_html`` — a table-based, inline-styled HTML email that survives the
  major mail clients (Gmail, Outlook, Apple Mail). No external CSS or images.
* ``render_text`` — a clean plaintext fallback for the multipart message.

Nothing here talks to an API or invents data; it only formats what it is given.
"""

from __future__ import annotations

import html
from typing import List

from .models import Brief, TapeRow

# ---------------------------------------------------------------------------
# Palette — muted, professional, "finance terminal" feel.
# ---------------------------------------------------------------------------
_INK = "#1a1a1a"
_MUTED = "#6b7280"
_RULE = "#e5e7eb"
_BG = "#f6f7f9"
_CARD = "#ffffff"
_ACCENT = "#0b3d5c"      # deep slate-blue header
_UP = "#b42318"          # yields up = red (cost of money rising)
_DOWN = "#067647"        # yields down = green
_FLAT = "#6b7280"

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
    "sans-serif,'Apple Color Emoji','Segoe UI Emoji'"
)
_MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _prose(text: str) -> str:
    """Escape LLM prose and preserve line breaks (HTML collapses raw newlines)."""
    return _esc(text).replace("\n", "<br>")


def _change_color(row: TapeRow) -> str:
    return {"up": _UP, "down": _DOWN, "flat": _FLAT, "na": _MUTED}[row.direction]


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def _tape_rows_html(tape: List[TapeRow]) -> str:
    cells = []
    for row in tape:
        color = _change_color(row)
        cells.append(
            f"""
            <tr>
              <td style="padding:9px 12px;border-bottom:1px solid {_RULE};font-size:14px;color:{_INK};">
                {_esc(row.label)}
              </td>
              <td style="padding:9px 12px;border-bottom:1px solid {_RULE};font-family:{_MONO};
                         font-size:14px;color:{_INK};text-align:right;white-space:nowrap;">
                {_esc(row.level_display)}
              </td>
              <td style="padding:9px 12px;border-bottom:1px solid {_RULE};font-family:{_MONO};
                         font-size:14px;color:{color};text-align:right;white-space:nowrap;font-weight:600;">
                {_esc(row.change_display)}
              </td>
            </tr>"""
        )
    return "".join(cells)


def _section_html(title: str, body_html: str) -> str:
    return f"""
        <tr><td style="padding:22px 24px 0 24px;">
          <div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
                      color:{_ACCENT};border-bottom:2px solid {_ACCENT};padding-bottom:6px;margin-bottom:10px;">
            {_esc(title)}
          </div>
          <div style="font-size:14px;line-height:1.55;color:{_INK};">{body_html}</div>
        </td></tr>"""


def _headlines_html(brief: Brief) -> str:
    if not brief.synthesis.headlines:
        return '<div style="color:%s;">No standout deal-flow items in the last window.</div>' % _MUTED
    items = []
    for h in brief.synthesis.headlines:
        src = _esc(h.source)
        link = _esc(h.link)
        title = _esc(h.title)
        takeaway = _esc(h.takeaway)
        src_link = (
            f'<a href="{link}" style="color:{_ACCENT};text-decoration:none;font-weight:600;">{src} ↗</a>'
            if link
            else f'<span style="color:{_MUTED};">{src}</span>'
        )
        items.append(
            f"""
            <li style="margin:0 0 11px 0;">
              <span style="color:{_INK};">{takeaway}</span><br>
              <span style="font-size:12px;color:{_MUTED};">{title} &middot; {src_link}</span>
            </li>"""
        )
    return f'<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul>'


def render_html(brief: Brief) -> str:
    """Return the full HTML email body for ``brief``."""
    s = brief.synthesis
    sources = ", ".join(_esc(name) for name in brief.sources_used) or "—"
    fred_note = "FRED (Federal Reserve Bank of St. Louis)"

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{_esc(brief.subject)}</title>
</head>
<body style="margin:0;padding:0;background:{_BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
  The Tape, Fed Watch, CMBS Watch and the day's CRE deal flow.
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
  <tr><td align="center" style="padding:20px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0"
           style="max-width:600px;width:100%;background:{_CARD};border:1px solid {_RULE};
                  border-radius:10px;overflow:hidden;font-family:{_FONT};">

      <!-- Masthead -->
      <tr><td style="background:{_ACCENT};padding:20px 24px;">
        <div style="font-size:19px;font-weight:800;color:#ffffff;letter-spacing:.02em;">
          CRE Finance Brief
        </div>
        <div style="font-size:13px;color:#cbd7e0;margin-top:2px;">
          {_esc(brief.date_str)} &middot; Commercial real estate &amp; CMBS
        </div>
      </td></tr>

      <!-- The Tape -->
      <tr><td style="padding:22px 24px 0 24px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
                    color:{_ACCENT};border-bottom:2px solid {_ACCENT};padding-bottom:6px;margin-bottom:10px;">
          The Tape
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid {_RULE};border-radius:6px;overflow:hidden;">
          <tr style="background:{_BG};">
            <td style="padding:8px 12px;font-size:11px;font-weight:700;letter-spacing:.05em;
                       text-transform:uppercase;color:{_MUTED};">Instrument</td>
            <td style="padding:8px 12px;font-size:11px;font-weight:700;letter-spacing:.05em;
                       text-transform:uppercase;color:{_MUTED};text-align:right;">Level</td>
            <td style="padding:8px 12px;font-size:11px;font-weight:700;letter-spacing:.05em;
                       text-transform:uppercase;color:{_MUTED};text-align:right;">Δ 1d</td>
          </tr>
          {_tape_rows_html(brief.tape)}
        </table>
        <div style="font-size:13px;line-height:1.5;color:{_MUTED};margin-top:8px;font-style:italic;">
          {_prose(s.tape_context) or "Rate levels and day-over-day moves shown above are sourced directly from FRED."}
        </div>
      </td></tr>

      {_section_html("Fed Watch", _prose(s.fed_watch) or "No fresh monetary-policy items in the last window.")}
      {_section_html("CMBS Watch", _prose(s.cmbs_watch) or "No fresh CMBS or CRE-credit items in the last window.")}
      {_section_html("Headlines & Deal Flow", _headlines_html(brief))}
      {_section_html("One to Watch", _prose(s.one_to_watch) or "—")}

      <!-- Footer -->
      <tr><td style="padding:22px 24px 24px 24px;">
        <div style="border-top:1px solid {_RULE};padding-top:14px;font-size:11px;line-height:1.55;color:{_MUTED};">
          <strong style="color:{_INK};">Sources.</strong> Rate data: {fred_note}.
          News: {sources}.<br>
          Generated {_esc(brief.generated_at_utc)} &middot; synthesis by {_esc(brief.model_name)}
          from {brief.item_count} item(s).<br>
          <em>Informational only — not investment, legal, tax or accounting advice.</em>
        </div>
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""


# ---------------------------------------------------------------------------
# Plaintext
# ---------------------------------------------------------------------------
def _rule(width: int = 60) -> str:
    return "-" * width


def render_text(brief: Brief) -> str:
    """Return the plaintext fallback body for ``brief``."""
    s = brief.synthesis
    out: List[str] = []
    out.append("CRE FINANCE BRIEF")
    out.append(brief.date_str + "  |  Commercial real estate & CMBS")
    out.append("=" * 60)
    out.append("")

    # The Tape
    out.append("THE TAPE")
    out.append(_rule())
    label_w = max((len(r.label) for r in brief.tape), default=12)
    for r in brief.tape:
        out.append(
            f"{r.label.ljust(label_w)}   {r.level_display.rjust(12)}   {r.change_display.rjust(9)}"
        )
    out.append("")
    if s.tape_context:
        out.append(s.tape_context)
        out.append("")

    def block(title: str, body: str, empty: str) -> None:
        out.append(title.upper())
        out.append(_rule())
        out.append(body.strip() if body and body.strip() else empty)
        out.append("")

    block("Fed Watch", s.fed_watch, "No fresh monetary-policy items in the last window.")
    block("CMBS Watch", s.cmbs_watch, "No fresh CMBS or CRE-credit items in the last window.")

    out.append("HEADLINES & DEAL FLOW")
    out.append(_rule())
    if brief.synthesis.headlines:
        for h in brief.synthesis.headlines:
            out.append(f"* {h.takeaway}")
            tail = f"  {h.title} [{h.source}]"
            out.append(tail)
            if h.link:
                out.append(f"  {h.link}")
            out.append("")
    else:
        out.append("No standout deal-flow items in the last window.")
        out.append("")

    out.append("ONE TO WATCH")
    out.append(_rule())
    out.append(s.one_to_watch.strip() if s.one_to_watch else "-")
    out.append("")

    out.append("=" * 60)
    out.append("Sources — Rate data: FRED (Federal Reserve Bank of St. Louis).")
    out.append("News: " + (", ".join(brief.sources_used) or "-"))
    out.append(f"Generated {brief.generated_at_utc} | synthesis by {brief.model_name} "
               f"from {brief.item_count} item(s).")
    out.append("Informational only - not investment, legal, tax or accounting advice.")
    return "\n".join(out)

"""End-to-end orchestration: FRED -> RSS -> Gemini -> render -> Resend.

Importable (``build_brief`` / ``run``) and runnable (``python -m cre_brief``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

from .config import Config
from .feeds import collect_items, items_for_prompt
from .fred import FredClient, build_tape, tape_facts_for_prompt
from .gemini import GeminiClient
from .mailer import send_via_gmail, send_via_resend
from .models import Brief
from .render import render_html, render_text
from .synthesize import synthesize

log = logging.getLogger("cre_brief.brief")


def build_brief(config: Config, session: Optional[requests.Session] = None) -> Brief:
    """Gather data, synthesize prose, and assemble the :class:`Brief` (no sending)."""
    session = session or requests.Session()

    date_str = config.date_str()
    subject = f"CRE Finance Brief — {date_str}"
    log.info("Building '%s'", subject)

    # 1. The Tape — ground-truth rate numbers from FRED, formatted in code.
    fred = FredClient(config.fred_api_key, session=session)
    tape, series_used = build_tape(fred)
    tape_facts = tape_facts_for_prompt(tape)
    log.info("The Tape built from %d FRED series", len(series_used))

    # 2. News — fetch, validate, window, dedupe.
    items, sources_used = collect_items(
        window_hours=config.news_window_hours, session=session
    )
    items_block = items_for_prompt(items)

    # 3. Prose — Gemini synthesizes ONLY from the supplied facts + items.
    gemini = GeminiClient(config.gemini_api_key, model=config.gemini_model, session=session)
    synthesis = synthesize(gemini, tape, items, tape_facts, items_block)

    # 4. Assemble.
    return Brief(
        date_str=date_str,
        subject=subject,
        tape=tape,
        synthesis=synthesis,
        sources_used=sources_used,
        fred_series_used=series_used,
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        model_name=config.gemini_model,
        item_count=len(items),
    )


def _write_preview(out_dir: str, html: str, text: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "brief.rendered.html")
    text_path = os.path.join(out_dir, "brief.rendered.txt")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(text_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return html_path


def run(send: bool = True, out_dir: Optional[str] = "out", print_html: bool = False) -> int:
    """Run the full pipeline. Returns a process exit code (0 = success)."""
    config = Config.load()
    config.validate(require_send=send)

    brief = build_brief(config)
    html = render_html(brief)
    text = render_text(brief)

    if not send:
        print("\n" + "=" * 70)
        print(f"DRY RUN — would send: {brief.subject}")
        print(f"Delivery method: {config.delivery_method or '(none configured)'}")
        if config.recipients:
            print(f"Recipients: {', '.join(config.recipients)}")
        else:
            print("Recipients: (none configured — set RECIPIENTS to send)")
        print("=" * 70 + "\n")
        print(text)
        if print_html:
            print("\n" + "=" * 70 + "\nHTML\n" + "=" * 70 + "\n")
            print(html)
        if out_dir:
            html_path = _write_preview(out_dir, html, text)
            print(f"\n[dry-run] HTML preview written to: {html_path}")
        log.info("Dry run complete (no email sent)")
        return 0

    # Refuse to mail a content-free brief (e.g. a full FRED + Gemini outage left
    # every Tape row 'n/a' and no news). Fail visibly so the scheduled run
    # surfaces the problem instead of silently sending a useless email.
    if not brief.fred_series_used and brief.item_count == 0:
        log.error("No FRED data and no news items — refusing to send an empty brief.")
        return 1

    method = config.delivery_method
    log.info("Sending via %s to %d recipient(s)", method, len(config.recipients))
    if method == "gmail":
        result = send_via_gmail(
            gmail_address=config.gmail_address,
            app_password=config.gmail_app_password,
            sender_name=config.sender_name,
            recipients=config.recipients,
            subject=brief.subject,
            html=html,
            text=text,
        )
    else:
        result = send_via_resend(
            api_key=config.resend_api_key,
            sender=config.sender_email,
            recipients=config.recipients,
            subject=brief.subject,
            html=html,
            text=text,
        )
    if not result.sent:
        log.error("No emails were delivered")
        return 1
    if result.failed:
        log.warning("Partial delivery: failed for %s", ", ".join(result.failed))
    return 0

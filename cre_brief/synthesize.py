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
    "estate and CMBS finance digest for finance professionals. You write tight, "
    "factual, skimmable copy.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Use ONLY the supplied rate facts and news items. Never invent figures, "
    "company names, deal sizes, people, dates or events.\n"
    "2. The rate numbers in THE TAPE FACTS are ground truth. Do not restate, "
    "recompute, round, or contradict them, and never introduce a rate number "
    "that is not in those facts.\n"
    "3. If a section has no relevant supplied material, say so in one short, "
    "honest sentence (e.g. 'No fresh Fed-policy items in today's feeds.'). Do "
    "NOT pad or fabricate.\n"
    "4. No hype, no advice, no price targets. Neutral, professional tone.\n"
    "5. Keep every section short — a reader skims the whole brief in 60 seconds."
)


def _build_user_prompt(tape_facts: str, items_block: str, has_items: bool) -> str:
    news_section = items_block if has_items else "(No news items were retrieved in the look-back window.)"
    return f"""THE TAPE FACTS (ground truth — never alter or restate as new numbers):
{tape_facts}

NEWS ITEMS (each prefixed with an [index] you must reference for headlines):
{news_section}

Produce a JSON object with these fields:

- "tape_context": ONE short sentence (max ~25 words) of plain-English context
  for the rate moves above. Describe direction/shape only; do not write any
  specific rate number. If moves are minimal, say markets were quiet.

- "fed_watch": 1-3 sentences synthesizing ONLY monetary-policy, Fed-official,
  inflation, or rate-outlook items from the news above. If none, say so briefly.

- "cmbs_watch": 1-3 sentences synthesizing ONLY CMBS, CRE-credit, delinquency,
  maturity-wall, or distress items from the news above. If none, say so briefly.

- "headlines": an array of 3 to 6 objects, each {{"item_index": <int>,
  "takeaway": "<one-line, ~15-word takeaway>"}}. Choose the most
  CRE/finance-relevant DISTINCT items by their [index]. Only use indices that
  appear above. If fewer than 3 relevant items exist, return only those.

- "one_to_watch": ONE forward-looking sentence about what to watch next,
  grounded in the supplied material (a data release, maturity, or theme already
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
        data = client.generate_json(prompt, SYNTHESIS_SCHEMA, system=SYSTEM_PROMPT)
    except GeminiError as exc:
        log.error("Gemini synthesis failed; sending The Tape with empty prose. %s", exc)
        return Synthesis()

    if not isinstance(data, dict):
        log.error("Gemini returned non-object JSON; using empty prose")
        return Synthesis()

    synthesis = Synthesis(
        tape_context=str(data.get("tape_context", "")).strip(),
        fed_watch=str(data.get("fed_watch", "")).strip(),
        cmbs_watch=str(data.get("cmbs_watch", "")).strip(),
        headlines=_coerce_headlines(data.get("headlines"), items_by_index),
        one_to_watch=str(data.get("one_to_watch", "")).strip(),
    )
    log.info(
        "Synthesis complete: %d headline(s), fed=%dch, cmbs=%dch",
        len(synthesis.headlines), len(synthesis.fed_watch), len(synthesis.cmbs_watch),
    )
    return synthesis

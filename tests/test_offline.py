"""Offline tests — no network required.

Exercises the deterministic, hallucination-sensitive parts of the pipeline with
fakes: Tape number formatting, the headline index->link mapping that prevents
fabricated sources, HTML/text rendering, and the Gemini 429 backoff.

Run directly (no pytest needed):    python -m tests.test_offline
Or with pytest if installed:        pytest -q
"""

from __future__ import annotations

import sys

from cre_brief.fred import SeriesPoint, build_tape, tape_facts_for_prompt
from cre_brief.gemini import GeminiClient, GeminiError
from cre_brief.models import Brief, FeedItem, Synthesis
from cre_brief.render import render_html, render_text
from cre_brief.synthesize import synthesize


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeFred:
    """Stands in for FredClient.fetch_point with canned numbers."""

    def __init__(self, points):
        self._points = points

    def fetch_point(self, series_id):
        return self._points.get(series_id, SeriesPoint(series_id=series_id))


class FakeGemini:
    """Stands in for GeminiClient.generate_json."""

    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, prompt, response_schema, system=None, temperature=0.3):
        return self.payload


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued responses in order from .post()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_tape_formatting_and_bps():
    points = {
        "DGS10": SeriesPoint("DGS10", latest=4.28, latest_date="2026-06-25", prior=4.25, prior_date="2026-06-24"),
        "DGS2": SeriesPoint("DGS2", latest=3.80, latest_date="2026-06-25", prior=3.85, prior_date="2026-06-24"),
        "DGS30": SeriesPoint("DGS30", latest=4.90, latest_date="2026-06-25", prior=4.90, prior_date="2026-06-24"),
        "DGS3MO": SeriesPoint("DGS3MO", latest=4.31, latest_date="2026-06-25", prior=4.30, prior_date="2026-06-24"),
        "SOFR": SeriesPoint("SOFR", latest=4.32, latest_date="2026-06-25", prior=4.33, prior_date="2026-06-24"),
        "T10Y2Y": SeriesPoint("T10Y2Y", latest=0.48, latest_date="2026-06-25", prior=0.40, prior_date="2026-06-24"),
        "DFEDTARU": SeriesPoint("DFEDTARU", latest=4.50, latest_date="2026-06-25", prior=4.50, prior_date="2026-06-24"),
        "DFEDTARL": SeriesPoint("DFEDTARL", latest=4.25, latest_date="2026-06-25", prior=4.25, prior_date="2026-06-24"),
    }
    tape, used = build_tape(FakeFred(points))
    rows = {r.label: r for r in tape}

    assert rows["10Y Treasury"].level_display == "4.28%"
    assert rows["10Y Treasury"].change_display == "+3 bps"     # 4.28 - 4.25 = +3bps
    assert rows["10Y Treasury"].direction == "up"
    assert rows["2Y Treasury"].change_display == "-5 bps"
    assert rows["2Y Treasury"].direction == "down"
    assert rows["30Y Treasury"].change_display == "flat"       # unchanged
    assert rows["SOFR"].change_display == "-1 bps"
    assert rows["2s/10s Spread"].level_display == "+48 bps"     # 0.48 -> 48 bps
    assert rows["2s/10s Spread"].change_display == "+8 bps"     # 0.48 - 0.40
    assert rows["Fed Funds Target"].level_display == "4.25–4.50%"
    assert rows["Fed Funds Target"].change_display == "flat"
    assert "DGS10" in used and "DFEDTARU" in used

    facts = tape_facts_for_prompt(tape)
    assert "10Y Treasury: 4.28%, day-over-day +3 bps" in facts
    print("  ✓ tape formatting & bps math")


def test_missing_series_is_graceful():
    # Only DGS10 has data; everything else is missing -> n/a, no crash.
    points = {"DGS10": SeriesPoint("DGS10", latest=4.10, latest_date="2026-06-25")}
    tape, used = build_tape(FakeFred(points))
    rows = {r.label: r for r in tape}
    assert rows["10Y Treasury"].level_display == "4.10%"
    assert rows["10Y Treasury"].change_display == "n/a"        # no prior
    assert rows["2Y Treasury"].level_display == "n/a"
    assert rows["Fed Funds Target"].level_display == "n/a"
    assert used == ["DGS10"]
    print("  ✓ missing series degrade to n/a")


def _sample_items():
    return [
        FeedItem(index=0, title="Fed holds rates steady", source="Wolf Street",
                 link="https://wolfstreet.com/a", summary="FOMC keeps target range."),
        FeedItem(index=1, title="CMBS delinquencies tick up", source="Trepp TreppTalk",
                 link="https://trepp.com/b", summary="Office distress rising."),
        FeedItem(index=2, title="Big office refinance closes", source="Commercial Observer",
                 link="https://commercialobserver.com/c", summary="$500M loan."),
    ]


def test_synthesize_maps_indices_and_drops_hallucinations():
    items = _sample_items()
    payload = {
        "tape_context": "Yields drifted modestly higher.",
        "fed_watch": "The FOMC held its target range steady.",
        "cmbs_watch": "Trepp flagged rising office delinquencies.",
        "headlines": [
            {"item_index": 1, "takeaway": "Office distress is pushing CMBS delinquencies up."},
            {"item_index": 2, "takeaway": "A large office refinancing closed."},
            {"item_index": 99, "takeaway": "FABRICATED — index does not exist."},  # must be dropped
            {"item_index": 1, "takeaway": "duplicate of 1"},                        # must be dropped
        ],
        "one_to_watch": "Watch next week's office maturity wall.",
    }
    synth = synthesize(FakeGemini(payload), [], items, "facts", "items")
    takeaways = [h.takeaway for h in synth.headlines]
    # Hallucinated (index 99) and duplicate (second index 1) entries are dropped.
    assert "FABRICATED — index does not exist." not in takeaways
    assert "duplicate of 1" not in takeaways
    # The model's two valid picks are preserved, in order, with REAL links.
    assert synth.headlines[0].link == "https://trepp.com/b"
    assert synth.headlines[0].source == "Trepp TreppTalk"
    assert synth.headlines[1].link == "https://commercialobserver.com/c"
    # Backfilled to the 3-item minimum from the remaining fresh item (index 0).
    assert len(synth.headlines) == 3
    assert synth.headlines[2].link == "https://wolfstreet.com/a"
    # Every link is a real item link — never fabricated.
    real_links = {it.link for it in items}
    assert all(h.link in real_links for h in synth.headlines)
    print("  ✓ synthesize maps indices to real links, drops bad/dupes, backfills to 3")


def test_headlines_backfill_only_when_material_exists():
    # Model selects nothing valid; backfill pulls the 3 freshest real items.
    items = _sample_items()
    payload = {"tape_context": "", "fed_watch": "", "cmbs_watch": "",
               "headlines": [{"item_index": 50, "takeaway": "nope"}], "one_to_watch": ""}
    synth = synthesize(FakeGemini(payload), [], items, "facts", "items")
    assert len(synth.headlines) == 3
    assert {h.link for h in synth.headlines} == {it.link for it in items}

    # With zero items there is nothing to backfill -> empty (renderer shows fallback).
    synth_empty = synthesize(FakeGemini(payload), [], [], "facts", "items")
    assert synth_empty.headlines == []
    print("  ✓ backfill fills to 3 when items exist, stays empty on a no-news day")


def test_gemini_non_json_200_degrades():
    # A 200 with a non-JSON body must raise GeminiError (not a bare ValueError),
    # so synthesize() can degrade gracefully instead of crashing the run.
    class BadJson(FakeResponse):
        def json(self):
            raise ValueError("no json")

    session = FakeSession([BadJson(200, text="<html>error</html>")])
    client = GeminiClient("k", "gemini-flash-latest", session=session, sleeper=lambda s: None)
    try:
        client.generate("hi")
    except GeminiError:
        print("  ✓ gemini 200-with-non-JSON-body raises GeminiError (graceful)")
        return
    raise AssertionError("expected GeminiError on non-JSON 200 body")


def test_fed_funds_single_bound():
    from cre_brief.fred import SeriesPoint as SP, build_tape as bt
    # Only the lower bound returns -> show it, don't fall through to n/a.
    pts = {"DFEDTARL": SP("DFEDTARL", latest=4.25, latest_date="2026-06-25")}
    tape, used = bt(FakeFred(pts))
    fed = {r.label: r for r in tape}["Fed Funds Target"]
    assert fed.level_display == "4.25% (lower)"
    assert "DFEDTARL" in used and "DFEDTARU" not in used
    print("  ✓ fed funds target uses available bound when the other is missing")


def test_render_preserves_newlines():
    from cre_brief.render import _prose
    assert _prose("a.\nb.") == "a.&lt;br&gt;b.".replace("&lt;", "<").replace("&gt;", ">")
    # And it still escapes HTML special chars.
    assert _prose("x & <y>") == "x &amp; &lt;y&gt;"
    print("  ✓ html prose preserves newlines as <br> and escapes specials")


def test_gmail_send_per_recipient():
    from cre_brief.mailer import send_via_gmail
    sent = []

    class FakeSMTP:
        def login(self, addr, pw):
            assert pw == "abcdefghijklmnop", "app-password spaces should be stripped"
        def sendmail(self, frm, to, raw):
            sent.append((to[0], raw))
        def quit(self):
            pass

    res = send_via_gmail(
        "me@gmail.com", "abcd efgh ijkl mnop", "CRE Brief",
        ["a@x.com", "b@y.com"], "Subj", "<b>hi</b>", "hi",
        smtp_factory=lambda: FakeSMTP(),
    )
    assert res.sent == ["a@x.com", "b@y.com"] and not res.failed
    _, raw = sent[0]
    assert "text/plain" in raw and "text/html" in raw   # multipart/alternative
    assert "CRE Brief" in raw and "me@gmail.com" in raw  # display name + from
    print("  ✓ gmail sends one multipart message per recipient")


def test_gmail_login_failure_marks_all_failed():
    from cre_brief.mailer import send_via_gmail

    class BadSMTP:
        def login(self, *a):
            raise OSError("bad app password")
        def quit(self):
            pass

    res = send_via_gmail("me@gmail.com", "x", "n", ["a@x.com", "b@y.com"],
                         "s", "<b>h</b>", "h", smtp_factory=lambda: BadSMTP())
    assert res.failed == ["a@x.com", "b@y.com"] and not res.sent
    print("  ✓ gmail login failure fails all recipients cleanly (no crash)")


def test_delivery_method_selection_and_validate():
    from cre_brief.config import Config, ConfigError
    gmail = Config(gemini_api_key="g", fred_api_key="f", gmail_address="me@gmail.com",
                   gmail_app_password="p", recipients=["a@x.com"])
    assert gmail.delivery_method == "gmail"
    gmail.validate(require_send=True)  # should not raise

    resend = Config(gemini_api_key="g", fred_api_key="f", resend_api_key="re_x",
                    sender_email="a@b.com", recipients=["a@x.com"])
    assert resend.delivery_method == "resend"
    resend.validate(require_send=True)

    none = Config(gemini_api_key="g", fred_api_key="f", recipients=["a@x.com"])
    assert none.delivery_method is None
    none.validate(require_send=False)  # dry run only needs gemini + fred
    try:
        none.validate(require_send=True)
    except ConfigError:
        print("  ✓ delivery method auto-selects; send without one is rejected")
        return
    raise AssertionError("expected ConfigError when no delivery method configured")


def test_window_hours_clamped():
    import os
    from cre_brief.config import Config
    for raw, expected in [("0", 36), ("-5", 36), ("999", 168), ("48", 48), ("oops", 36)]:
        os.environ["NEWS_WINDOW_HOURS"] = raw
        try:
            assert Config.load().news_window_hours == expected, f"{raw} -> {expected}"
        finally:
            del os.environ["NEWS_WINDOW_HOURS"]
    print("  ✓ NEWS_WINDOW_HOURS clamped to a sane positive range")


def test_synthesize_degrades_on_gemini_error():
    class Boom:
        def generate_json(self, *a, **k):
            raise GeminiError("429 forever")

    synth = synthesize(Boom(), [], _sample_items(), "facts", "items")
    assert isinstance(synth, Synthesis)
    assert synth.headlines == [] and synth.fed_watch == ""
    print("  ✓ synthesis degrades gracefully when Gemini fails")


def test_render_html_and_text():
    points = {"DGS10": SeriesPoint("DGS10", latest=4.28, latest_date="2026-06-25",
                                   prior=4.25, prior_date="2026-06-24")}
    tape, used = build_tape(FakeFred(points))
    synth = Synthesis(
        tape_context="Yields edged higher.",
        fed_watch="FOMC steady.",
        cmbs_watch="No fresh CMBS items.",
        headlines=[],
        one_to_watch="Watch CPI.",
    )
    brief = Brief(
        date_str="Friday, June 26, 2026",
        subject="CRE Finance Brief — Friday, June 26, 2026",
        tape=tape, synthesis=synth, sources_used=["Wolf Street"], fred_series_used=used,
        generated_at_utc="2026-06-26 11:00 UTC", model_name="gemini-3.5-flash", item_count=3,
    )
    html = render_html(brief)
    text = render_text(brief)
    assert "CRE Finance Brief" in html and "The Tape" in html
    assert "4.28%" in html and "+3 bps" in html
    assert "not investment" in html.lower()
    assert "THE TAPE" in text and "4.28%" in text
    assert "gemini-3.5-flash" in html
    # No unrendered template artifacts.
    assert "{" not in html.split("<body")[1][:50]
    print("  ✓ html & text render with real numbers and disclaimer")


def test_gemini_backoff_then_success():
    sleeps = []
    ok_payload = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    session = FakeSession([
        FakeResponse(429, text="rate limited", headers={"Retry-After": "1"}),
        FakeResponse(503, text="overloaded"),
        FakeResponse(200, payload=ok_payload),
    ])
    client = GeminiClient("fake-key", "gemini-3.5-flash", session=session,
                          sleeper=lambda s: sleeps.append(s))
    out = client.generate("hello")
    assert out == '{"ok": true}'
    assert session.calls == 3, "should retry twice then succeed"
    assert len(sleeps) == 2, "should back off before each retry"
    print(f"  ✓ gemini retries on 429/503 then succeeds (backoffs={sleeps})")


def test_gemini_fails_fast_on_400():
    session = FakeSession([FakeResponse(400, text="bad request")])
    client = GeminiClient("fake-key", "gemini-3.5-flash", session=session, sleeper=lambda s: None)
    try:
        client.generate("hello")
    except GeminiError:
        print("  ✓ gemini fails fast (no retry) on non-transient 400")
        return
    raise AssertionError("expected GeminiError on 400")


def main():
    tests = [
        test_tape_formatting_and_bps,
        test_missing_series_is_graceful,
        test_synthesize_maps_indices_and_drops_hallucinations,
        test_headlines_backfill_only_when_material_exists,
        test_synthesize_degrades_on_gemini_error,
        test_gemini_non_json_200_degrades,
        test_fed_funds_single_bound,
        test_render_html_and_text,
        test_render_preserves_newlines,
        test_gmail_send_per_recipient,
        test_gmail_login_failure_marks_all_failed,
        test_delivery_method_selection_and_validate,
        test_window_hours_clamped,
        test_gemini_backoff_then_success,
        test_gemini_fails_fast_on_400,
    ]
    print(f"Running {len(tests)} offline test(s):")
    for t in tests:
        t()
    print(f"\nAll {len(tests)} test(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

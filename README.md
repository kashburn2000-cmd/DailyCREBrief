# CRE Finance Brief

An automated **daily commercial-real-estate finance digest** (CRE / CMBS focus)
that emails a small fixed list every weekday morning. It runs entirely on free
infrastructure — **$0 to operate**: no server, no database, no paid APIs.

```
GitHub Actions (cron)  →  FRED (rates)  +  RSS (news)  →  Gemini (writing)  →  Gmail / Resend (email)
```

Each edition has a fixed structure a reader can skim in 60 seconds:

* **The Tape** — a rate table (10Y, 2Y, 30Y, 3M, SOFR, 2s/10s spread, fed funds
  target) with levels and day-over-day moves in basis points.
* **Fed Watch** — monetary-policy / rate-outlook synthesis from the day's feeds.
* **CMBS Watch** — CMBS / CRE-credit / delinquency / distress synthesis.
* **Headlines & Deal Flow** — 3–6 bullets, each a one-line takeaway + source link.
* **One to Watch** — a single forward-looking line.
* **Footer** — data sources, timestamp, and a "not investment advice" disclaimer.

<sub>Rendered HTML preview: run a dry run (below) and open `out/brief.rendered.html`.</sub>

---

## How it minimizes hallucinated numbers

This is the core design choice. **Every rate number is computed in code from
FRED and inserted as a fact** — the language model never generates or edits a
figure:

1. `cre_brief/fred.py` pulls each series from FRED, finds the latest and prior
   business-day values, and computes the basis-point change. The Tape table is
   built from these exact numbers.
2. The FRED numbers are passed to Gemini **only as read-only ground truth**, with
   a strict instruction never to restate, recompute, or invent a rate.
3. For *Headlines*, Gemini returns the **index** of a supplied news item plus a
   one-line takeaway; the real title/source/link are attached in code afterward,
   so it cannot fabricate a source or URL. Out-of-range or duplicate indices are
   dropped.
4. If a section has no material, the model is told to say so briefly rather than
   pad. If Gemini fails entirely, the brief still sends with The Tape and empty
   prose (graceful degradation).

---

## Repository layout

```
DailyCREBrief/
├── cre_brief/
│   ├── __main__.py      # CLI: python -m cre_brief [--no-send] [-v]
│   ├── config.py        # env-var loading + validation; model/timezone defaults
│   ├── fred.py          # FRED client; builds The Tape from ground-truth numbers
│   ├── feeds.py         # RSS fetch + validate + window + dedupe  (EDIT FEEDS here)
│   ├── gemini.py        # Gemini REST client w/ exponential backoff on 429/503
│   ├── synthesize.py    # strict prompt + structured-output parsing
│   ├── render.py        # HTML + plaintext email rendering
│   ├── mailer.py        # Gmail (SMTP) or Resend delivery (one message per recipient)
│   ├── models.py        # shared dataclasses
│   └── brief.py         # end-to-end orchestration
├── main.py              # convenience entry point (== python -m cre_brief)
├── tests/test_offline.py# offline tests (no network): python -m tests.test_offline
├── .github/workflows/daily-brief.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 1. Get the three free API keys

### Gemini (Google AI Studio) — the writer
1. Go to **https://aistudio.google.com/apikey** and sign in with a Google account.
2. Click **Create API key**. Copy it.
3. **Do NOT enable billing** — the free tier is all this needs.

> **Model note (important):** the default is the rolling alias
> **`gemini-flash-latest`**, which Google maps to the current stable free-tier
> Flash model — so the newsletter keeps working across model deprecations with
> no code change. (Model names churn: a recent example is `gemini-2.5-flash`
> being retired in favor of `gemini-3.5-flash`. The alias insulates you from
> that.) The value lives in the `GEMINI_MODEL` env var, so you can pin a specific
> version any time — e.g. `gemini-3.5-flash` or `gemini-3.1-flash-lite`. Confirm
> live model names + free-tier limits in Google AI Studio.

> **Data-usage note:** on the Gemini **free** tier, Google may use your prompts
> and the model's responses to improve its products, and human reviewers may see
> them. **That's fine here** — every input is public market data and public news
> headlines; nothing private is sent. (To opt out you would have to enable
> billing and use a paid tier, which this project deliberately avoids.)

### FRED (St. Louis Fed) — the rate data
1. Create a free account at **https://fredaccount.stlouisfed.org/**.
2. Go to **https://fredaccount.stlouisfed.org/apikeys** and request a key
   (a 32-character lowercase string, issued instantly).

### Email delivery — pick ONE of two free options

The brief auto-detects which to use from your secrets (Gmail wins if both are set).

**Option A — Gmail (recommended; no domain needed).** Sends from your own Gmail.
1. Turn on **2-Step Verification**: https://myaccount.google.com/security
2. Create an **App Password**: https://myaccount.google.com/apppasswords — Google
   gives you a 16-character code. Copy it (spaces don't matter).
3. You'll set two secrets: `GMAIL_ADDRESS` (your `you@gmail.com`) and
   `GMAIL_APP_PASSWORD` (that code). Optional: `SENDER_NAME` for the display name.
   * Limit ~500 recipients/day — far more than a small newsletter needs.

**Option B — Resend (needs a domain you own).** Branded from-address.
1. Sign up at **https://resend.com** (free tier: 100 emails/day, 3,000/month).
2. **API Keys → Create API Key** (starts with `re_`). Copy it now — it's shown once.
3. **Verify a sender domain:** Resend dashboard → **Domains → Add Domain**, add the
   DNS records it gives you, and click **Verify**. `SENDER_EMAIL` must be on that
   domain. (No domain? Use Option A instead — it needs none.)
4. Set `RESEND_API_KEY` and `SENDER_EMAIL`.

---

## 2. Test locally before relying on the schedule

```bash
# Python 3.11+
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then edit .env and fill in your keys
```

**Dry run** — renders and prints the brief, writes an HTML preview, sends nothing:

```bash
python -m cre_brief --no-send          # or: python main.py --no-send
# open the preview that gets written:
#   out/brief.rendered.html   (open in a browser)
#   out/brief.rendered.txt
python -m cre_brief --no-send -v        # add debug logging
```

A dry run needs only `GEMINI_API_KEY` and `FRED_API_KEY`. To actually send, also
set `RECIPIENTS` and one delivery method (Gmail: `GMAIL_ADDRESS` +
`GMAIL_APP_PASSWORD`, or Resend: `RESEND_API_KEY` + `SENDER_EMAIL`), then drop
`--no-send`:

```bash
python -m cre_brief
```

**Check your feeds** (which resolve, how many items each yields):

```bash
python -m cre_brief.feeds
```

**Run the offline tests** (no network, no keys needed):

```bash
python -m tests.test_offline      # or: pytest -q
```

---

## 3. Push to GitHub

```bash
git add .
git commit -m "Add CRE Finance Brief"
git push -u origin main           # or your branch
```

### Add the secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add each of:

Always add these three:

| Secret           | Value                                                        |
| ---------------- | ----------------------------------------------------------- |
| `GEMINI_API_KEY` | from Google AI Studio                                       |
| `FRED_API_KEY`   | from FRED                                                   |
| `RECIPIENTS`     | comma-separated list, e.g. `a@x.com, b@y.com`              |

Then add **one** delivery pair:

| If using Gmail (no domain) | If using Resend (has domain) |
| -------------------------- | ---------------------------- |
| `GMAIL_ADDRESS` — `you@gmail.com` | `RESEND_API_KEY` — `re_…` |
| `GMAIL_APP_PASSWORD` — 16-char code | `SENDER_EMAIL` — `Name <brief@yourdomain.com>` |

Optional (override defaults without editing code): `GEMINI_MODEL` and
`SENDER_NAME` as **secrets**; `NEWS_WINDOW_HOURS`, `BRIEF_TIMEZONE`, and
`DELIVERY_METHOD` (`resend` or `gmail`, to force one even if both pairs of
secrets exist) as repo **Variables** (Settings → Secrets and variables → Actions
→ *Variables* tab).

### Trigger a manual test run
**Actions → Daily CRE Finance Brief → Run workflow**. Tick **Dry run** to render
without sending (check the run logs), or leave it unticked to send for real.
After that the cron schedule takes over automatically.

---

## 4. Customizing

### Recipients
Stored in the `RECIPIENTS` secret (comma-separated). Edit the secret; no code or
redeploy needed. Each recipient gets their own copy (addresses are not shared
between recipients).

### Feeds
Edit the `FEEDS` list at the top of **`cre_brief/feeds.py`**. Each entry is just
`Feed("Display Name", "https://…/feed-url")`. Every run validates each feed and
**silently skips any that fail**, so a dead or wrong URL never breaks a send —
it just won't contribute that day.

> The starter URLs follow each site's known feed convention (WordPress `/feed/`,
> HubSpot `/<blog>/rss.xml`, etc.) but could not be fetched from the build
> environment, so confirm them on your first real run: `python -m cre_brief.feeds`
> lists which resolve. If one is dropped, open the site, find its RSS link
> (often in the footer, or try `/feed/`, `/rss/`, `/feed.xml`), and update the URL.
> `GlobeSt`, `Bisnow`, and `MBA` are the lowest-confidence starters.

### Send time
Edit the `cron:` line in **`.github/workflows/daily-brief.yml`**. Remember it's
**UTC and does not auto-adjust for daylight saving**. `0 11 * * 1-5` means 11:00
UTC weekdays = **06:00 EST / 07:00 EDT**. Bump the hour to shift it. The
subject-line date is rendered in `BRIEF_TIMEZONE` (default US Eastern).

### News look-back window
`NEWS_WINDOW_HOURS` (default 36). Consider ~72 on Mondays so Friday's stories
aren't missed.

---

## Cost

Everything sits inside free tiers for a small daily newsletter:

| Component       | Free tier                                  | This project's usage        |
| --------------- | ------------------------------------------ | --------------------------- |
| GitHub Actions  | generous free minutes for public repos     | ~1–2 min/day                |
| FRED            | free, unlimited for this volume            | ~8 requests/day             |
| Gemini (Flash)  | free tier, no billing                      | 1 request/day               |
| Gmail (SMTP)    | free, ~500 recipients/day                  | (#recipients)/day           |
| Resend (alt.)   | 100 emails/day, 3,000/month                | (#recipients)/day           |

No paid services are used or required.

---

## Troubleshooting

* **`Missing required configuration: …`** — a needed env var/secret is unset. For
  a dry run you only need `GEMINI_API_KEY` + `FRED_API_KEY`.
* **A feed is missing from the brief** — it failed validation and was skipped.
  Run `python -m cre_brief.feeds` and fix or replace the URL in `feeds.py`.
* **Gemini `404 … model is no longer available`** — the model name was retired.
  Set `GEMINI_MODEL` to a current one (e.g. `gemini-flash-latest`).
* **Gemini `429`** — free-tier rate limit. The client already backs off
  (1→2→4→8s, honoring `Retry-After`). One brief a day is far under the limit;
  this mostly bites during rapid manual testing — wait a minute and retry.
* **Gmail `Username and Password not accepted`** — you used your normal Google
  password. You must use a 16-char **App Password** (and 2-Step Verification must
  be on). Re-create it at https://myaccount.google.com/apppasswords.
* **Resend `403 / domain not verified`** — finish domain verification, or just
  use the Gmail option (it needs no domain).

---

## Disclaimer

The CRE Finance Brief is generated automatically for **informational purposes
only** and is **not investment, legal, tax, or accounting advice**. Rate data is
from FRED; news is synthesized from public RSS feeds and may contain errors.
Verify anything before acting on it.

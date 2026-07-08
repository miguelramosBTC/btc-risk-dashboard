<!-- repo path: bot/README.md -->
# X Risk Bot v2 — daily automated post for bitcoinrisk.net

One post per day via GitHub Actions. v2 posts a **long-form satirical column**
(250–750 words) on the day's most viral market-relevant story — naming the
public figures involved as reported — connected to Bitcoin's uncensorable
nature and to today's risk reading, with the **gauge card image** attached and
**zero investment advice** anywhere.

```
🟢 BTC Risk 0.28 — LOW                          ← always matches the dashboard
📊 Lower than 78% of the last 365 days · as of Jul 6

<hook line>

<2–5 short paragraphs: the story (grounded in real headlines
 and Grok's live X/web search), the satire, the Bitcoin angle,
 the risk reading as a fact — never a recommendation>

<closing punch>

not financial advice · full model → link in bio
[attached: gauge_today.png — needle at 0.28 on the dashboard's color ramp]
```

## What changed vs v1

| # | change | how |
|---|--------|-----|
| 1 | No buy/sell recommendations | Neutral levels (LOW/MODERATE/ELEVATED/HIGH); the dashboard's regimes/multipliers are never shown; an advice-guard rejects LLM text with recommendation language and degrades to the short format |
| 2 | Longer-term context | 365-day percentile (streak lines only for ≥90-day extremes) |
| 3 | Gauge image | `bot/render_gauge.py` draws a 1200×675 card (exact dashboard `RISK_STOPS` ramp, needle at today's value) and uploads it via the X v2 media endpoints, with alt text |
| 4 | Long content (250+ words) | Three formats via `TWEET_MODE`: **`thread`** (~250 words split across ≤`THREAD_MAX_TWEETS` chained ≤280-char tweets — **no Premium needed**), **`long`** (one >280-char post — **needs X Premium**, auto-degrades to short if rejected), **`short`** (one ≤280-char post, newsless fallback lines) |
| 5 | Naming politicians/leaders | Allowed **when they appear in the fetched headlines or live-search results**; inventing events/quotes stays forbidden, spicy claims get attributed to the reporting — that's the no-fabrication rule plus basic libel hygiene |
| 6 | Grok | Default LLM. Uses xAI's **Responses API** with server-side `x_search` + `web_search` tools (date-bounded to the last 3 days) for live viral trends and X sentiment. DeepSeek/Groq still work by changing two variables |

## Files

| repo path | role |
|---|---|
| `.github/workflows/x-daily-post.yml` | cron 07:15 UTC + manual dispatch with dry-run toggle |
| `bot/post_risk_update.py` | data, stats, headlines, LLM (Grok/DeepSeek), guards, assembly, media upload, posting |
| `bot/render_gauge.py` | the daily gauge card PNG |
| `bot/requirements.txt` | requests, tweepy, requests-oauthlib, Pillow |
| `bot/test_assembly.py` | offline tests incl. pixel-validation of the gauge |

## Hard guarantees

* **Number = dashboard.** `model.lastRisk`/`lastDate` from the same
  `data.json`, printed `.2f`. The gauge needle is drawn from the same value.
* **No advice, ever.** Prompt forbids it, the advice-guard enforces it, the
  fallback short format contains none, tests lock the wording.
* **No invented news.** RSS headlines (titles + descriptions) are injected;
  on Grok, live `x_search`/`web_search` results ground the story; the prompt
  forbids referencing anything outside those sources and requires attribution.
* **Stale-data abort**, **no URL in the post text** (the $0.20 tier — the
  image and bio carry the branding), **auto-degrade** on any failure: LLM
  down → short format; media down → text-only; no Premium → short format.

---

## Thread mode (recommended — 250 words, no Premium)

Set repo variable `TWEET_MODE=thread`. The bot writes the same news-grounded
column and posts it as a numbered reply chain (risk header + gauge on tweet 1,
footer on the last). This reaches ~250 words **without any X Premium
subscription**, because the Premium requirement only applies to a single
post over 280 characters — a chain of normal tweets has no such limit.

Cost: each tweet is a $0.015 post, so a 6-tweet daily thread ≈ **$2.80/mo**.
`THREAD_MAX_TWEETS` (default 8) caps the daily spend; the column is targeted
at ~150–250 words (`LONG_MIN_WORDS`/`LONG_MAX_WORDS`) so it typically lands in
5–7 tweets. If the model output is unusable or the LLM is unreachable, the bot
degrades to a single short post as always.

**Thread mode still requires a funded LLM key** (below) — the news comes from
the model, not from templates.

## Setup — your steps (~20 min on top of v1)

### 1 · Posting format
- **`TWEET_MODE=thread`** (recommended): no Premium; see the section above.
- **`TWEET_MODE=long`**: single long post — subscribe the BOT account to X
  Premium (any tier unlocks >280-char posts; your personal sub doesn't
  transfer). Skip Premium and the bot auto-degrades long → short.
- **`TWEET_MODE=short`**: single ≤280-char post; no LLM or Premium needed.

### 2 · Grok API key (this is NOT your $8 Grok subscription)
Your X Premium / consumer Grok plan and the **xAI developer API are separate
products with separate billing** — the "never hit token limits" allowance of
the app does not apply to the API. Get the API key at **console.x.ai**:
create an account, add a small credit balance, create an API key.

Two cost softeners worth checking in the consoles:
* Buying X API credits earns **xAI API credits back (up to 20% of spend)** —
  your existing X credit purchases already generated some.
* xAI has intermittently offered free monthly API credits via a data-sharing
  program (Settings → Data Sharing in the xAI console); availability has
  changed over time, so verify rather than count on it.

Then in the repo: set secret `LLM_API_KEY` to the xAI key. Defaults already
target `https://api.x.ai/v1` + `grok-4.3` — nothing else to configure.
(Prefer DeepSeek instead? Variables `LLM_BASE_URL=https://api.deepseek.com/v1`,
`LLM_MODEL=deepseek-v4-flash`, and its key in `LLM_API_KEY`. Live X search is
Grok-only; DeepSeek runs use the RSS headlines.)

### 3 · Commit the updated files, dry-run, review, go live
Same procedure as v1: overwrite the four bot files + workflow, run the
workflow manually with *Dry run = true*, read the drafted long post in the
job Summary daily during your review window, then set `BOT_DRY_RUN=false`.
The gauge PNG is rendered in dry-runs too (path shown in the log) — eyeball
the first one.

## Configuration reference (repo → Settings → …)

**Secrets:** `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
`X_ACCESS_TOKEN_SECRET`, `LLM_API_KEY`

**Variables (all optional — defaults in parentheses):**

```
BOT_DRY_RUN       (true)     flip to false to go live
TWEET_MODE        (long)     thread | long | short
ATTACH_GAUGE      (true)     attach the daily gauge card (all modes)
GROK_SEARCH       (on)       on | x_only | web_only | off   [api.x.ai only]
LLM_BASE_URL      (https://api.x.ai/v1)
LLM_MODEL         (grok-4.3)  pin a dated snapshot for absolute consistency;
                              the alias auto-upgrades when xAI ships new models
LLM_TEMPERATURE   (0.85)     chat-completions providers only
THREAD_MAX_TWEETS (8)        hard cap on tweets/day = cost cap [thread mode]
LONG_MIN_WORDS    (thread 150 / long 250)   LONG_MAX_WORDS (thread 250 / long 750)
MAX_LONG_CHARS    (6000)     [long mode]
NEWS_FEEDS        (CoinDesk, Cointelegraph, Bitcoin Magazine, Decrypt)
MAX_STALE_DAYS    (3)
```

## Costs (verified July 2026)

| item | rate | monthly (~31 posts) |
|---|---|---|
| X posts — thread mode (~6/day, no URL) | $0.015 each | **~$2.80** |
| X post — single (short/long, no URL) | $0.015 | **~$0.47** |
| X Premium on bot account (long mode only) | from ~$3–8/mo tier | **$3–8** |
| Grok 4.3 tokens (~2k in / 1–3k out+reasoning per day) | $1.25 / $2.50 per M | **~$0.10–0.30** |
| Grok server-side search tools (model-decided, ~2–6 calls/day) | ~$5 per 1k calls | **~$0.30–0.90** |
| DeepSeek alternative (no live search) | $0.14 / $0.28 per M | < $0.01 |
| GitHub Actions / Pages builds | bot commits nothing | $0 |

Notes: grok-4.3's reasoning tokens bill as output and can spike — budget
headroom; xAI also charges a $0.05 fee per request its safety layer blocks
before generation, which is one more reason the prompt's grounding and
no-slur rails stay in place. Watch the first live week in both consoles
(X Developer Console usage + console.x.ai).

## Tuning the voice

Everything editable is flagged in `bot/post_risk_update.py`:

* **`LONG_SYSTEM_PROMPT`** — the columnist persona. Naming public figures
  from the day's reporting is explicitly in; two rails are deliberate and
  should stay: *ground everything, invent nothing* (your no-fabricated-data
  rule — an invented quote about a named president is how bot accounts die),
  and *mock the reported action, not the identity* (X automation policy).
* **`LONG_FEWSHOT_USER/ASSISTANT`** — the one worked example; the fastest
  tone lever. Keep any replacement free of advice or it will be auto-rejected.
* **`ADVICE_PAT`** — the recommendation blocklist, if you want it stricter.
* **`FALLBACK_HUMOR`** — the short-format safety-net lines.
* Word range via `LONG_MIN/MAX_WORDS`; a draft below the minimum triggers one
  automatic "expand" retry before posting or degrading.

Test any change free: workflow dispatch with *Dry run = true*, or feed a
canned body through the `BOT_FAKE_LLM_TEXT` env hook (see tests).

## Troubleshooting

* **Long post → 403 / "not permitted"** → the bot account has no active
  Premium; the run auto-posts the short format and says so in the log.
* **Image missing on the post** → media upload failed; the log shows which
  step (initialize/append/finalize). The post still goes out text-only.
* **`grok call failed` daily** → key/balance at console.x.ai, or the model
  alias moved — pin `LLM_MODEL` to a dated snapshot from docs.x.ai/models.
* **Story feels stale** → raise `GROK_SEARCH` from `off`/feed-only to `on`,
  or add fresher feeds to `NEWS_FEEDS`.
* **"refusing to post stale numbers"** → the 06:00 data workflow didn't
  commit; fix upstream, the bot is intentionally downstream.

*Not financial advice. Keep the account labeled as automated.*

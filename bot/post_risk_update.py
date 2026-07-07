#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# repo path: bot/post_risk_update.py
"""
post_risk_update.py — daily X (Twitter) post for bitcoinrisk.net
================================================================

Reads the SAME canonical data.json the dashboard and the email engine use
(regenerated daily at 06:00 UTC by the data workflow), so the number in the
tweet ALWAYS matches the dashboard's "as of <date>" reading exactly:

  * risk shown with .2f            — identical to the email subject (r.toFixed(2))
  * regime label + action line     — verbatim port of the dashboard's actionFor()
  * value = model.lastRisk/lastDate — the dataset point, no re-derivation here

Tweet layout (strict priority order — later lines are trimmed/dropped first):

    <emoji> BTC Risk 0.61 — DISTRIBUTE            (1. score, never dropped)
    <interpretation / investment suggestion>       (2. LLM or template)
    📊 <historical stat from series.r>             (3. computed, deterministic)
    <dark-humor / news blurb>                      (4. LLM or rotating fallback)
    as of Jul 4 · full model → link in bio         (5. dated promo tail)

No URL is ever included in the post: X bills $0.20 per post containing a link
vs $0.015 without (pay-per-use, 2026). Promotion goes through the profile bio.
A regex guard blocks accidental domains before posting.

LLM layer (interpretation + humor) is grounded on real RSS headlines fetched
at run time — the model is explicitly forbidden from inventing events. If the
LLM or the feeds fail, deterministic fallbacks keep the daily post alive.

Environment (secrets in CAPS are required to actually post):
  X_API_KEY, X_API_SECRET,
  X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET   OAuth 1.0a user-context keys (Read+Write)
  LLM_API_KEY                             DeepSeek / Groq key (optional: falls back)
  LLM_BASE_URL   default https://api.deepseek.com/v1   (Groq: https://api.groq.com/openai/v1)
  LLM_MODEL      default deepseek-v4-flash
  LLM_TEMPERATURE default 0.9
  DATA_FILE      local data.json path (default: repo root ./data.json)
  DATA_URL       fallback fetch (default https://bitcoinrisk.net/data.json)
  NEWS_FEEDS     comma-separated RSS urls (defaults below)
  BOT_DRY_RUN    "true" => print/summarize only, never post (DEFAULT true if unset)
  BOT_FAKE_LLM_JSON  testing hook: JSON string used instead of a real LLM call
  MAX_STALE_DAYS default 3 — abort a LIVE post if data.json is older than this

Exit codes: 0 ok (posted or dry-run) · 1 data unusable/stale or post failed.
LLM/news failures NEVER fail the run — they degrade to fallbacks.

NOT financial advice. Not affiliated with any third-party metric.
"""

import json
import os
import random
import re
import sys
import datetime as dt
import xml.etree.ElementTree as ET
from html import unescape

import requests

# --------------------------------------------------------------------------- #
#  CONFIG
# --------------------------------------------------------------------------- #
DEFAULT_DATA_URL = "https://bitcoinrisk.net/data.json"
DEFAULT_LLM_BASE = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"          # legacy 'deepseek-chat' alias dies 2026-07-24
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/feed",
    "https://decrypt.co/feed",
]

MAX_TWEET_WEIGHT = 276            # hard X limit is 280 weighted; keep margin
INTERP_MAX = 92                   # char budgets for the two generated lines
HUMOR_MAX = 92
HISTORY_MAX = 60
LLM_TIMEOUT = 25
FEED_TIMEOUT = 10
MAX_HEADLINES = 6

# --------------------------------------------------------------------------- #
#  REGIME — verbatim port of the dashboard's actionFor() (index.html /
#  btc-risk-weekly.mjs). Do NOT edit thresholds here: they must stay identical
#  to the site so the tweet can never disagree with the gauge.
# --------------------------------------------------------------------------- #
def action_for(r: float) -> dict:
    if r < 0.10: return {"kind": "buy",  "mult": 5, "regime": "ACCUMULATE — MAX"}
    if r < 0.20: return {"kind": "buy",  "mult": 4, "regime": "ACCUMULATE — HIGH"}
    if r < 0.30: return {"kind": "buy",  "mult": 3, "regime": "ACCUMULATE"}
    if r < 0.60: return {"kind": "hold",            "regime": "HOLD"}
    if r < 0.70: return {"kind": "sell", "parts": 2, "regime": "DISTRIBUTE"}
    if r < 0.80: return {"kind": "sell", "parts": 3, "regime": "DISTRIBUTE"}
    if r < 0.90: return {"kind": "sell", "parts": 4, "regime": "DISTRIBUTE — HEAVY"}
    return             {"kind": "sell", "parts": 5, "regime": "DISTRIBUTE — MAX"}


def action_line(a: dict) -> str:
    """Same wording as the email engine's actionLine(, 'en')."""
    if a["kind"] == "buy":  return f"Buy {a['mult']}× your base"
    if a["kind"] == "hold": return "Hold — neither buy nor sell"
    return f"Sell {a['parts']}/15 of your stack"


def regime_emoji(r: float) -> str:
    if r < 0.30: return "🟢"
    if r < 0.60: return "🟡"
    if r < 0.80: return "🟠"
    return "🔴"


def band_word(r: float) -> str:
    """Coarse flavor word passed to the LLM only (tweet shows the real regime)."""
    if r < 0.30: return "Low"
    if r < 0.60: return "Moderate"
    if r < 0.80: return "Elevated"
    return "High"


# --------------------------------------------------------------------------- #
#  DATA — canonical data.json ({model:{lastRisk,lastDate,...}, series:{t,p,r,c}})
# --------------------------------------------------------------------------- #
def load_data() -> dict:
    tried = []
    candidates = []
    if os.environ.get("DATA_FILE"):
        candidates.append(os.environ["DATA_FILE"])
    ws = os.environ.get("GITHUB_WORKSPACE", "")
    if ws:
        candidates.append(os.path.join(ws, "data.json"))
    candidates.append("data.json")
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data.json"))

    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            print(f"[data] loaded local {p}")
            return d
        except Exception as e:
            tried.append(f"{p}: {e.__class__.__name__}")

    url = os.environ.get("DATA_URL", DEFAULT_DATA_URL)
    print(f"[data] local read failed ({'; '.join(tried)}) — fetching {url}")
    resp = requests.get(url, timeout=20, headers={"cache-control": "no-store"})
    resp.raise_for_status()
    return resp.json()


def validate_data(d: dict) -> dict:
    model, series = d.get("model"), d.get("series")
    if not model or not series:
        raise ValueError("data.json missing 'model'/'series'")
    risk = float(model["lastRisk"])
    date = str(model["lastDate"])
    rs = [float(x) for x in series["r"]]
    ts = [str(x) for x in series["t"]]
    if not rs or len(rs) != len(ts):
        raise ValueError("series r/t empty or misaligned")
    if not (0.0 <= risk <= 1.0):
        raise ValueError(f"lastRisk out of range: {risk}")
    return {"risk": risk, "date": date, "r": rs, "t": ts}


def staleness_days(date_str: str) -> int:
    d = dt.date.fromisoformat(date_str)
    return (dt.datetime.now(dt.timezone.utc).date() - d).days


# --------------------------------------------------------------------------- #
#  HISTORICAL STATS — deterministic pick, purely descriptive (no hindsight
#  cherry-picking): N-day extreme if N>=30, else a 90-day percentile line.
# --------------------------------------------------------------------------- #
def history_line(rs: list, current: float) -> str:
    hist = rs[:-1] if rs and abs(rs[-1] - current) < 1e-9 else rs[:]
    if not hist:
        return ""

    n_hi = 0
    for v in reversed(hist):
        if current >= v: n_hi += 1
        else: break
    n_lo = 0
    for v in reversed(hist):
        if current <= v: n_lo += 1
        else: break

    if n_hi >= 30:
        return f"📊 Highest reading in {min(n_hi + 1, 365)} days"
    if n_lo >= 30:
        return f"📊 Lowest reading in {min(n_lo + 1, 365)} days"

    last90 = hist[-90:]
    below = sum(1 for v in last90 if v < current)
    p = round(100 * below / len(last90))
    if p >= 50:
        return f"📊 Higher than {p}% of the last 90 days"
    return f"📊 Lower than {100 - p}% of the last 90 days"


# --------------------------------------------------------------------------- #
#  HEADLINES — real news context so the humor line never invents events.
#  stdlib-only RSS/Atom title scrape; fully fail-soft.
# --------------------------------------------------------------------------- #
def fetch_headlines() -> list:
    feeds_env = os.environ.get("NEWS_FEEDS", "").strip()
    feeds = [u.strip() for u in feeds_env.split(",") if u.strip()] or DEFAULT_FEEDS
    titles, seen = [], set()
    for url in feeds:
        try:
            resp = requests.get(url, timeout=FEED_TIMEOUT,
                                headers={"User-Agent": "bitcoinrisk-bot/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            count = 0
            for item in root.iter():
                if item.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
                    continue                              # RSS <item> / Atom <entry>
                for child in item:
                    if child.tag.rsplit("}", 1)[-1] == "title" and child.text:
                        title = unescape(re.sub(r"\s+", " ", child.text)).strip()
                        key = title.casefold()
                        if title and key not in seen:
                            seen.add(key)
                            titles.append(title[:110])
                            count += 1
                        break
                if count >= 2:            # max 2 per feed for variety
                    break
        except Exception as e:
            print(f"[news] {url} failed: {e.__class__.__name__}")
    print(f"[news] {len(titles)} headlines collected")
    return titles[:MAX_HEADLINES]


# --------------------------------------------------------------------------- #
#  LLM PROMPT — EDIT THIS BLOCK TO TUNE THE VOICE.
#  Persona: sharp, irreverent, crypto-native, dark humor welcome. Political /
#  macro references allowed when genuinely market-relevant. Floor kept in
#  place so the account never crosses into abuse X would suspend for:
#  punch at institutions, markets, policies and human greed — never at
#  ethnicities, religions, genders or private individuals; no slurs; no
#  violence; and NEVER invent events not present in the provided headlines.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You write two short lines for a daily Bitcoin risk tweet.

Persona: a sharp, cynical, crypto-native commentator. Dry, compressed, dark
humor welcome. Sarcasm welcome. Never corporate, never boring, never hedging.

You are given today's risk score from a transparent on-chain model, its regime
(the model's own action), a historical stat, the date, and real headlines.

Produce STRICT JSON, one line, exactly these keys:
{"interpretation": "...", "humor": "..."}

interpretation — max 92 chars. What this risk level means for Bitcoin holders
RIGHT NOW (accumulation vs caution, DCA vs leverage). It must stay directionally
consistent with the model regime provided: never tell people to buy more in a
DISTRIBUTE regime or to sell in an ACCUMULATE regime. Opinionated is good.

humor — max 92 chars. One witty, dark, irreverent line about today's market
vibe. Ground it in ONE of the provided headlines, or — if none fit — go
evergreen about market psychology. Political/macro jabs (Fed, elections,
regulators, geopolitics) are fine WHEN a headline makes them market-relevant.

Hard rules:
- Never invent events, numbers or quotes not present in the headlines.
- Punch at institutions, markets, policies and human greed — never at
  ethnicities, religions, genders, nationalities or private individuals.
- No slurs, no violence, no death wishes.
- No hashtags, no @mentions, no links, no emojis, no quotes around the lines.
- Plain text only. No markdown. Output ONLY the JSON object."""

# Few-shot pairs (Prompt C deliverable). Inputs mirror the real runtime message.
FEWSHOTS = [
    (
        "Risk: 0.08 (Low) — regime ACCUMULATE — MAX — model action: Buy 5× your base\n"
        "Historical: Lowest reading in 210 days\nDate: 2026-01-12\nHeadlines:\n"
        "- Bitcoin miners sell reserves as hashprice hits yearly low\n"
        "- Exchange balances drop to multi-year lows\nJSON:",
        '{"interpretation": "Deep value zone — 5× base per the model. Windows like this never feel comfortable.", '
        '"humor": "Miners selling the bottom again. Some traditions survive every cycle."}',
    ),
    (
        "Risk: 0.22 (Low) — regime ACCUMULATE — model action: Buy 3× your base\n"
        "Historical: Lower than 71% of the last 90 days\nDate: 2026-02-03\nHeadlines:\n"
        "- Spot Bitcoin ETFs post third week of outflows\nJSON:",
        '{"interpretation": "Still accumulation territory: 3× base. Value fades as risk climbs toward 0.30.", '
        '"humor": "ETF tourists heading for the exit. Locals keep stacking."}',
    ),
    (
        "Risk: 0.47 (Moderate) — regime HOLD — model action: Hold — neither buy nor sell\n"
        "Historical: Higher than 58% of the last 90 days\nDate: 2026-03-19\nHeadlines:\n"
        "- Fed holds rates steady, cites persistent uncertainty\nJSON:",
        '{"interpretation": "Dead-center HOLD. No edge either way — sit tight and let the plan run.", '
        '"humor": "The Fed is data-dependent again. Translation: they don\'t know either."}',
    ),
    (
        "Risk: 0.68 (Elevated) — regime DISTRIBUTE — model action: Sell 2/15 of your stack\n"
        "Historical: Highest reading in 34 days\nDate: 2026-05-02\nHeadlines:\n"
        "- Bitcoin breaks all-time high as funding rates spike\nJSON:",
        '{"interpretation": "Distribution starts here: 2/15 off. Leverage is how you get carried out.", '
        '"humor": "New ATH and funding through the roof. Nothing bad ever follows that."}',
    ),
    (
        "Risk: 0.85 (High) — regime DISTRIBUTE — HEAVY — model action: Sell 4/15 of your stack\n"
        "Historical: Highest reading in 190 days\nDate: 2026-06-11\nHeadlines:\n"
        "- Retail trading app downloads hit record\n"
        "- Senator announces personal memecoin\nJSON:",
        '{"interpretation": "Heavy distribution: 4/15 per the model. This is where conviction gets expensive.", '
        '"humor": "A senator launched a memecoin. You may now ring the bell yourselves."}',
    ),
]


def build_user_msg(risk, band, regime, act, hist_plain, date, headlines):
    hl = "\n".join(f"- {t}" for t in headlines) if headlines else \
         "(none available — go evergreen; do NOT reference specific events)"
    return (f"Risk: {risk:.2f} ({band}) — regime {regime} — model action: {act}\n"
            f"Historical: {hist_plain}\nDate: {date}\nHeadlines:\n{hl}\nJSON:")


# --------------------------------------------------------------------------- #
#  LLM CALL — OpenAI-compatible /chat/completions (DeepSeek, Groq, ...).
#  Two attempts, JSON-mode when supported, then deterministic fallbacks.
# --------------------------------------------------------------------------- #
def call_llm(user_msg: str):
    fake = os.environ.get("BOT_FAKE_LLM_JSON")
    if fake:
        print("[llm] using BOT_FAKE_LLM_JSON test hook")
        return fake
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        print("[llm] LLM_API_KEY not set — using fallbacks")
        return None

    base = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE).rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
    try:
        temp = float(os.environ.get("LLM_TEMPERATURE", "0.9"))
    except ValueError:
        temp = 0.9

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for u, a in FEWSHOTS:
        messages.append({"role": "user", "content": u})
        messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user_msg})

    payload = {"model": model, "messages": messages,
               "temperature": temp, "max_tokens": 200}
    for attempt, use_json_mode in ((1, True), (2, False)):
        body = dict(payload)
        if use_json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(f"{base}/chat/completions",
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=LLM_TIMEOUT)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            print(f"[llm] attempt {attempt} ok ({model})")
            return content
        except Exception as e:
            print(f"[llm] attempt {attempt} failed: {e.__class__.__name__}: {e}")
    return None


def parse_llm_json(raw: str):
    if not raw:
        return None
    txt = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw.strip())
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    interp = obj.get("interpretation")
    humor = obj.get("humor")
    if not isinstance(interp, str) or not isinstance(humor, str):
        return None
    return interp, humor


def sanitize(line: str, max_chars: int) -> str:
    s = re.sub(r"\s+", " ", line).strip().strip('"\u201c\u201d`').strip()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\b[\w-]+\.(?:com|net|org|io|xyz|co|ai)\b", "", s, flags=re.I)
    s = re.sub(r"[@#](\w)", r"\1", s)          # de-fang mentions/hashtags
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        s = s[:max_chars - 1].rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"
    return s


REFUSAL_PAT = re.compile(r"\b(as an ai|i can'?t|i cannot|language model)\b", re.I)
CONTRA = {  # crude directional tripwire: LLM line must not contradict the regime
    "buy":  re.compile(r"\b(sell|exit|take profit|trim|distribute)\b", re.I),
    "sell": re.compile(r"\b(buy more|add here|accumulate|back up the truck|leverage up|dca harder)\b", re.I),
    "hold": None,
}


# --------------------------------------------------------------------------- #
#  FALLBACKS — deterministic templates; the daily post must never break.
#  EDIT freely: these are the "safe but still sharp" rotating lines.
# --------------------------------------------------------------------------- #
FALLBACK_INTERP = {
    ("buy", 5):  "Deep value by this model's history — 5× base zone. These windows don't last.",
    ("buy", 4):  "Strong accumulation zone: 4× base per the model. Value like this is rare.",
    ("buy", 3):  "Accumulation zone — 3× base. Better value than most of the cycle.",
    ("hold", 0): "HOLD zone. No edge either way — standard plan, no heroics.",
    ("sell", 2): "Early distribution: 2/15 off per the model. Leverage here is donating.",
    ("sell", 3): "Distribution: 3/15 off. Late-cycle heat — respect it.",
    ("sell", 4): "Heavy distribution: 4/15 off. Euphoria pricing — discipline beats hope.",
    ("sell", 5): "Max distribution: 5/15 off. Historically, the air is thin up here.",
}

FALLBACK_HUMOR = [
    "Charts don't care about your feelings. That's why we trust them.",
    "Everyone's a long-term holder until the first -20% week.",
    "The market did something today. Analysts explained it afterwards. Tradition.",
    "Your risk tolerance is theoretical until the drawdown shows up.",
    "Somewhere, a leveraged trader just learned what funding rate means.",
    "Volatility is the tuition. Some keep repeating the semester.",
    "Number went somewhere today. Zoom out.",
    "Fear and greed take turns driving. Neither one signals.",
    "Another cycle, same emotions, new participants.",
    "Patience is a position. The hardest one to hold.",
    "Liquidity giveth and liquidity taketh away. Mostly on weekends.",
    "Print, pump, panic, repeat — the four macro seasons.",
]


def fallback_interp(a: dict) -> str:
    key = (a["kind"], a.get("mult", a.get("parts", 0)))
    return FALLBACK_INTERP.get(key, FALLBACK_INTERP[("hold", 0)])


def fallback_humor(date: dt.date) -> str:
    return FALLBACK_HUMOR[date.timetuple().tm_yday % len(FALLBACK_HUMOR)]


# --------------------------------------------------------------------------- #
#  TWEET ASSEMBLY — X weighted length (twitter-text ranges: these codepoint
#  ranges weigh 1, everything else — emoji, CJK, arrows — weighs 2).
# --------------------------------------------------------------------------- #
_W1 = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))

def x_len(s: str) -> int:
    total = 0
    for ch in s:
        cp = ord(ch)
        total += 1 if any(a <= cp <= b for a, b in _W1) else 2
    return total


TAILS = [  # rotating dated promo tail — the only "ad"; no URL, ever.
    "full model → link in bio",
    "11 on-chain signals → bio",
    "open, replicable model → bio",
    "live gauge + strategies → bio",
]


def make_tail(data_date: str, post_date: dt.date) -> str:
    d = dt.date.fromisoformat(data_date)
    stamp = d.strftime("%b %-d") if os.name != "nt" else d.strftime("%b %d")
    return f"as of {stamp} · {TAILS[post_date.toordinal() % len(TAILS)]}"


def assemble(score_line, interp, hist, humor, tail):
    """Priority: score > interpretation > history > humor > tail.
    Trim ladder drops the lowest-priority content first; tail goes last
    because the dated stamp also guarantees uniqueness vs X's dup filter."""
    def join(parts):
        return "\n".join(p for p in parts if p)

    attempts = [
        (score_line, interp, hist, humor, tail),
        (score_line, interp, hist, sanitize(humor, 60) if humor else "", tail),
        (score_line, interp, hist, "", tail),
        (score_line, interp, "", "", tail),
        (score_line, sanitize(interp, 60), "", "", tail),
        (score_line, "", "", "", tail),
        (score_line, interp, hist, humor, ""),   # near-impossible; log if reached
    ]
    for i, parts in enumerate(attempts):
        t = join(parts)
        if x_len(t) <= MAX_TWEET_WEIGHT:
            if i > 0:
                print(f"[assemble] trim ladder step {i} used")
            return t
    t = join(attempts[-1])
    print("[assemble] WARNING: even minimal layout over budget — hard truncating")
    while x_len(t) > MAX_TWEET_WEIGHT:
        t = t[:-1]
    return t


# --------------------------------------------------------------------------- #
#  POSTING
# --------------------------------------------------------------------------- #
def post_tweet(text: str) -> bool:
    import tweepy  # lazy: DRY_RUN/tests work without the package
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    try:
        resp = client.create_tweet(text=text)
        print(f"[post] ok — tweet id {resp.data.get('id')}")
        return True
    except tweepy.Forbidden as e:
        if "duplicate" in str(e).lower():
            print("[post] X rejected as duplicate content — treating as non-fatal")
            return True
        print(f"[post] FORBIDDEN: {e}")
        return False
    except Exception as e:
        print(f"[post] FAILED: {e.__class__.__name__}: {e}")
        return False


def write_summary(mode, tweet, extra):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"## Daily risk tweet — {mode}\n\n```\n{tweet}\n```\n\n"
                    f"- weighted length: **{x_len(tweet)}** / 280\n")
            for k, v in extra.items():
                f.write(f"- {k}: {v}\n")
    except Exception as e:
        print(f"[summary] {e.__class__.__name__}")


# --------------------------------------------------------------------------- #
#  MAIN
# --------------------------------------------------------------------------- #
def main() -> int:
    dry = os.environ.get("BOT_DRY_RUN", "true").strip().lower() != "false"
    today = dt.datetime.now(dt.timezone.utc).date()
    print(f"[bot] {today.isoformat()} UTC — mode: {'DRY-RUN' if dry else 'LIVE'}")

    # 1. canonical data ------------------------------------------------------
    try:
        data = validate_data(load_data())
    except Exception as e:
        print(f"[bot] FATAL: cannot load canonical data: {e}")
        return 1

    risk, ddate = data["risk"], data["date"]
    stale = staleness_days(ddate)
    max_stale = int(os.environ.get("MAX_STALE_DAYS", "3"))
    print(f"[bot] risk={risk:.4f} lastDate={ddate} (staleness {stale}d; "
          f"1d is normal — the Coin Metrics CSV lags one day)")
    if stale > max_stale:
        msg = f"data is {stale} days old (> {max_stale}) — refusing to tweet stale numbers"
        if dry:
            print(f"[bot] WARNING: {msg} (continuing because DRY-RUN)")
        else:
            print(f"[bot] FATAL: {msg}")
            return 1

    a = action_for(risk)
    act = action_line(a)
    band = band_word(risk)
    score_line = f"{regime_emoji(risk)} BTC Risk {risk:.2f} — {a['regime']}"

    # 2. historical stat -----------------------------------------------------
    hist = history_line(data["r"], risk)
    hist_plain = hist.replace("📊 ", "") if hist else "n/a"

    # 3. headlines + LLM -----------------------------------------------------
    headlines = fetch_headlines()
    llm_used = False
    interp, humor = fallback_interp(a), fallback_humor(today)

    parsed = parse_llm_json(call_llm(
        build_user_msg(risk, band, a["regime"], act, hist_plain, ddate, headlines)))
    if parsed:
        cand_i = sanitize(parsed[0], INTERP_MAX)
        cand_h = sanitize(parsed[1], HUMOR_MAX)
        bad = (not cand_i or not cand_h
               or REFUSAL_PAT.search(cand_i + " " + cand_h)
               or (CONTRA[a["kind"]] and CONTRA[a["kind"]].search(cand_i)))
        if bad:
            print("[llm] output rejected by validators — using fallbacks")
        else:
            interp, humor, llm_used = cand_i, cand_h, True
    else:
        print("[llm] no usable output — using fallbacks")

    # 4. assemble ------------------------------------------------------------
    tail = make_tail(ddate, today)
    tweet = assemble(score_line, interp, hist, humor, tail)

    # last-line-of-defense guards (should never trigger; belt and braces)
    assert x_len(tweet) <= 280, "over weighted limit"
    if re.search(r"https?://|\b[\w-]+\.(?:com|net|org|io|xyz|co|ai)\b", tweet, re.I):
        print("[bot] FATAL: a URL/domain slipped into the tweet — aborting "
              "(URL posts bill at $0.20 and it's against this bot's design)")
        return 1

    print("─" * 60)
    print(tweet)
    print("─" * 60)
    print(f"[bot] weighted length {x_len(tweet)}/280 · LLM {'✓' if llm_used else 'fallback'} "
          f"· headlines {len(headlines)}")

    extra = {"data date": ddate, "risk": f"{risk:.4f}", "regime": a["regime"],
             "LLM": "used" if llm_used else "fallback",
             "headlines": len(headlines)}

    # 5. post ----------------------------------------------------------------
    if dry:
        write_summary("DRY-RUN (not posted)", tweet, extra)
        print("[bot] DRY-RUN — nothing posted. Set BOT_DRY_RUN=false to go live.")
        return 0

    missing = [k for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
                           "X_ACCESS_TOKEN_SECRET") if not os.environ.get(k)]
    if missing:
        print(f"[bot] FATAL: missing X secrets: {', '.join(missing)}")
        return 1

    ok = post_tweet(tweet)
    write_summary("POSTED" if ok else "POST FAILED", tweet, extra)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

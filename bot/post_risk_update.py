#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# repo path: bot/post_risk_update.py
"""
post_risk_update.py — daily X post for bitcoinrisk.net (v2)
===========================================================

v2 changes vs v1:
  1. NO buy/sell recommendations anywhere. The post shows the risk NUMBER and
     a neutral level word (LOW/MODERATE/ELEVATED/HIGH); an advice-guard rejects
     LLM text containing recommendation language.
  2. Historical context uses the last 365 days (was 90).
  3. A gauge card PNG (bot/render_gauge.py — dashboard's exact color ramp,
     needle at today's value) is attached via the X API v2 chunked media flow.
  4. Long-form mode (default): one 250–750 word satirical piece on the day's
     most viral market-relevant story, connecting it to Bitcoin's properties
     (nobody can print it, freeze it, or overturn the referee's call) and to
     today's reading. Requires X Premium on the POSTING account; if the long
     post is rejected, the bot automatically degrades to the short format.
  5. Named public figures are allowed WHEN they appear in the fetched
     headlines or live-search results; inventing events or quotes stays
     forbidden (grounding rule), and satire targets reported actions.
  6. Grok support: when LLM_BASE_URL points at api.x.ai the bot uses xAI's
     Responses API with server-side x_search + web_search tools (live X
     sentiment + news, date-bounded to the last few days). Any
     OpenAI-compatible /chat/completions provider (DeepSeek, Groq) still
     works as before.

The risk number remains canonical: model.lastRisk / model.lastDate from the
same data.json the dashboard serves, printed with .2f like the email engine.

Environment (secrets):
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, LLM_API_KEY
Environment (variables, all optional):
  LLM_BASE_URL     default https://api.x.ai/v1   (DeepSeek: https://api.deepseek.com/v1)
  LLM_MODEL        default grok-4.3              (DeepSeek: deepseek-v4-flash)
  LLM_TEMPERATURE  default 0.85
  GROK_SEARCH      on|x_only|web_only|off        (default on; only used on api.x.ai)
  TWEET_MODE       long|short                    (default long)
  ATTACH_GAUGE     true|false                    (default true)
  HEADER_TAGS      e.g. "$BTC #Bitcoin"          (default empty; appended to header line 1)
  EXTREME_STYLE    on|off                        (default on; gauge glow+badge at LOW/HIGH)
  LONG_MIN_WORDS / LONG_MAX_WORDS                (default 250 / 750)
  MAX_LONG_CHARS   default 6000  (X Premium long posts allow 25k)
  DATA_FILE / DATA_URL / NEWS_FEEDS / MAX_STALE_DAYS / BOT_DRY_RUN / BOT_FAKE_LLM_TEXT

Exit codes: 0 ok · 1 data unusable/stale or post failed. LLM/news/media
failures never kill the run — they degrade.

NOT financial advice. The account must stay labeled as automated.
"""

import datetime as dt
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html import unescape

import requests

# --------------------------------------------------------------------------- #
#  CONFIG
# --------------------------------------------------------------------------- #
DEFAULT_DATA_URL = "https://bitcoinrisk.net/data.json"
DEFAULT_LLM_BASE = "https://api.x.ai/v1"
DEFAULT_LLM_MODEL = "grok-4.3"
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/feed",
    "https://decrypt.co/feed",
]
X_API = "https://api.x.com"
SHORT_MAX_WEIGHT = 276
LLM_TIMEOUT = 90
FEED_TIMEOUT = 10
MAX_HEADLINES = 6


def env(name, default=""):
    return os.environ.get(name, default).strip() or default


# --------------------------------------------------------------------------- #
#  NEUTRAL LEVELS — descriptive only; the dashboard's actionFor() regimes and
#  multipliers are intentionally NOT shown (change #1). The NUMBER still
#  matches the dashboard exactly.
# --------------------------------------------------------------------------- #
def level_word(r: float) -> str:
    if r < 0.30: return "LOW"
    if r < 0.60: return "MODERATE"
    if r < 0.80: return "ELEVATED"
    return "HIGH"


def level_emoji(r: float) -> str:
    if r < 0.30: return "🟢"
    if r < 0.60: return "🟡"
    if r < 0.80: return "🟠"
    return "🔴"


# --------------------------------------------------------------------------- #
#  DATA (canonical data.json)
# --------------------------------------------------------------------------- #
def load_data() -> dict:
    tried, candidates = [], []
    if env("DATA_FILE"):
        candidates.append(env("DATA_FILE"))
    if env("GITHUB_WORKSPACE"):
        candidates.append(os.path.join(env("GITHUB_WORKSPACE"), "data.json"))
    candidates += ["data.json",
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data.json")]
    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            print(f"[data] loaded local {p}")
            return d
        except Exception as e:
            tried.append(f"{p}:{e.__class__.__name__}")
    url = env("DATA_URL", DEFAULT_DATA_URL)
    print(f"[data] local read failed ({'; '.join(tried)}) — fetching {url}")
    r = requests.get(url, timeout=20, headers={"cache-control": "no-store"})
    r.raise_for_status()
    return r.json()


def validate_data(d: dict) -> dict:
    model, series = d.get("model"), d.get("series")
    if not model or not series:
        raise ValueError("data.json missing model/series")
    risk = float(model["lastRisk"])
    date = str(model["lastDate"])
    rs = [float(x) for x in series["r"]]
    if not rs or not (0.0 <= risk <= 1.0):
        raise ValueError("bad series or lastRisk")
    return {"risk": risk, "date": date, "r": rs}


def staleness_days(date_str: str) -> int:
    return (dt.datetime.now(dt.timezone.utc).date() - dt.date.fromisoformat(date_str)).days


# --------------------------------------------------------------------------- #
#  HISTORY — 365-day framing (change #2). Streak line only when it is a
#  genuinely notable (>=90d) extreme; otherwise the 365d percentile.
# --------------------------------------------------------------------------- #
def history_line(rs: list, current: float) -> str:
    hist = rs[:-1] if rs and abs(rs[-1] - current) < 1e-9 else rs[:]
    if not hist:
        return ""
    n_hi = n_lo = 0
    for v in reversed(hist):
        if current >= v: n_hi += 1
        else: break
    for v in reversed(hist):
        if current <= v: n_lo += 1
        else: break
    if n_hi >= 90:
        return f"Highest reading in {min(n_hi + 1, 365)} days"
    if n_lo >= 90:
        return f"Lowest reading in {min(n_lo + 1, 365)} days"
    window = hist[-365:]
    below = sum(1 for v in window if v < current)
    p = round(100 * below / len(window))
    span = "365" if len(window) >= 365 else str(len(window))
    if p >= 50:
        return f"Higher than {p}% of the last {span} days"
    return f"Lower than {100 - p}% of the last {span} days"


# --------------------------------------------------------------------------- #
#  HEADLINES — titles AND descriptions now (long-form needs substance).
# --------------------------------------------------------------------------- #
def fetch_headlines() -> list:
    feeds = [u.strip() for u in env("NEWS_FEEDS").split(",") if u.strip()] or DEFAULT_FEEDS
    items, seen = [], set()
    for url in feeds:
        try:
            resp = requests.get(url, timeout=FEED_TIMEOUT,
                                headers={"User-Agent": "bitcoinrisk-bot/2.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            count = 0
            for item in root.iter():
                if item.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
                    continue
                title, desc = "", ""
                for child in item:
                    tag = child.tag.rsplit("}", 1)[-1]
                    if tag == "title" and child.text and not title:
                        title = unescape(re.sub(r"\s+", " ", child.text)).strip()
                    elif tag in ("description", "summary") and child.text and not desc:
                        desc = re.sub(r"<[^>]+>", " ", child.text)
                        desc = unescape(re.sub(r"\s+", " ", desc)).strip()
                key = title.casefold()
                if title and key not in seen:
                    seen.add(key)
                    items.append({"title": title[:130], "desc": desc[:300]})
                    count += 1
                if count >= 2:
                    break
        except Exception as e:
            print(f"[news] {url} failed: {e.__class__.__name__}")
    print(f"[news] {len(items)} headlines collected")
    return items[:MAX_HEADLINES]


# --------------------------------------------------------------------------- #
#  LLM PROMPT — long-form satire. EDIT THIS BLOCK TO TUNE THE VOICE.
#  Named public figures are allowed when they appear in the provided
#  headlines / live-search results; inventing events or quotes about anyone
#  is forbidden (both your no-fabrication rule and basic libel hygiene), and
#  the satire targets reported actions, never identities.
# --------------------------------------------------------------------------- #
LONG_SYSTEM_PROMPT = """You write the daily long-form X post for bitcoinrisk.net, an open on-chain
Bitcoin risk model. You are a sharp, cynical, crypto-native columnist. Dark
humor, irreverence and satire are the voice. Never corporate, never bland.

Your job every day: take the single most viral, market-adjacent story of the
moment — politics, macro, central banks, regulators, sports-meets-politics,
whatever the timeline is actually screaming about — and connect it, with wit
rather than lecture, to why Bitcoin's properties matter: nobody can print it,
freeze it, seize the narrative, or call the head of referees to overturn the
result. Weave in today's risk reading (provided) as factual observation.

Name names. Presidents, ministers, central bankers, CEOs, league officials —
IF and only if they appear in the provided headlines or in your live search
results. Satirize what they DID as reported, never who they are.

Structure (plain text only, no markdown):
- A hook line that stops the scroll.
- 2 to 5 short paragraphs: what happened (grounded), the satire, the Bitcoin
  angle, the risk-reading tie-in.
- A closing line that lands the punch.
Blank line between paragraphs. Total length {min_w}-{max_w} words; aim ~{sweet} words.

Hard rules:
- Ground EVERY factual claim in the provided headlines or your search
  results. Never invent events, numbers, or quotes. Attribute spicy claims
  to the reporting ("per Reuters", "according to ESPN").
- No slurs; no attacks on ethnicity, religion, gender, or nationality; no
  violence; nothing about private individuals.
- ABSOLUTELY NO investment advice: no buy/sell/hold, no "accumulate", no DCA
  or leverage suggestions, no price predictions, no "time to". Describe the
  risk reading and its percentile factually — nothing more.
- No hashtags. No @ symbols (write names plainly). No links or URLs. No
  emojis. No markdown formatting. No "Sources:" section.
- One story per post. If the day is truly dead, satirize the silence itself."""

LONG_FEWSHOT_USER = """Risk: 0.41 (MODERATE) — Higher than 58% of the last 365 days
Date: 2026-03-14
Headlines:
- EU finance ministers advance plan requiring traceability for all transfers above €200 (Reuters) :: Draft framework would extend reporting duties to self-custody wallets; final vote expected in June.
- Spot Bitcoin ETF inflows hit three-week high (CoinDesk) :: Net inflows across US funds reached the highest level since February.
Write today's post."""

LONG_FEWSHOT_ASSISTANT = """Two hundred euros. That is now the official threshold at which your money becomes a matter of state interest.

Per Reuters, EU finance ministers just advanced a framework to trace every transfer above €200 — self-custody wallets included. Two hundred euros. A decent dinner for two in Madrid. The plan was drafted by people whose own expense accounts wouldn't survive the scrutiny they're proposing for your grocery-adjacent transactions, and it will be voted on by people who have never once waited for a bank transfer to clear on a Friday.

The stated goal is fighting crime. The historical record of financial surveillance thresholds is that they only ever move in one direction, and it isn't up. Yesterday it was ten thousand. Today it's two hundred. Tomorrow's number will be justified with the same press release, lightly edited.

Meanwhile, the asset they cannot trace by committee just posted its strongest ETF inflows in three weeks, per CoinDesk. That's the quiet part: while one system was designing new permission slips, capital was flowing into the one that doesn't issue them. Bitcoin doesn't care who chairs the working group. There is no threshold to lower, because there is no one with the authority to lower it.

For the record-keepers: the model reads 0.41 today — MODERATE, higher than 58% of the last 365 days. Not a signal, not advice. Just a number nobody can vote on.

They can trace two hundred euros. They still can't print a single sat."""


def build_long_user(risk, level, hist, date, headlines, grok_search):
    hl = "\n".join(f"- {h['title']}" + (f" :: {h['desc']}" if h["desc"] else "")
                   for h in headlines) or "(no feed headlines available)"
    extra = ("\nYou also have live x_search and web_search tools: use them to find "
             "the most viral, market-relevant story of the last 2-3 days and the "
             "current sentiment on X. Prefer live results over the feed list if "
             "something bigger is trending.") if grok_search else \
            ("\nNo live search is available: pick from the headlines above only, "
             "and do not reference any event not listed.")
    return (f"Risk: {risk:.2f} ({level}) — {hist}\nDate: {date}\n"
            f"Headlines:\n{hl}{extra}\nWrite today's post.")


# --------------------------------------------------------------------------- #
#  LLM CALLS — two provider shapes:
#   * api.x.ai → Responses API (POST /responses) with server-side search tools
#   * anything else → OpenAI-style /chat/completions (DeepSeek, Groq, ...)
# --------------------------------------------------------------------------- #
def _grok_tools(today: dt.date):
    mode = env("GROK_SEARCH", "on").lower()
    if mode == "off":
        return []
    frm = (today - dt.timedelta(days=3)).isoformat()
    x_tool = {"type": "x_search", "from_date": frm, "to_date": today.isoformat()}
    w_tool = {"type": "web_search"}
    if mode == "x_only":
        return [x_tool]
    if mode == "web_only":
        return [w_tool]
    return [x_tool, w_tool]


def _extract_responses_text(j: dict) -> str:
    if isinstance(j.get("output_text"), str) and j["output_text"].strip():
        return j["output_text"]
    parts = []
    for item in j.get("output", []) or []:
        for c in item.get("content", []) or []:
            t = c.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def call_llm(system_prompt, fewshots, user_msg, max_tokens, today) -> str:
    fake = env("BOT_FAKE_LLM_TEXT")
    if fake:
        print("[llm] using BOT_FAKE_LLM_TEXT test hook")
        return fake
    key = env("LLM_API_KEY")
    if not key:
        print("[llm] LLM_API_KEY not set")
        return ""
    base = env("LLM_BASE_URL", DEFAULT_LLM_BASE).rstrip("/")
    model = env("LLM_MODEL", DEFAULT_LLM_MODEL)
    try:
        temp = float(env("LLM_TEMPERATURE", "0.85"))
    except ValueError:
        temp = 0.85
    hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if "api.x.ai" in base:                                   # ---- Grok path
        tools = _grok_tools(today)
        body = {"model": model,
                "input": ([{"role": "system", "content": system_prompt}]
                          + [{"role": r, "content": c} for r, c in fewshots]
                          + [{"role": "user", "content": user_msg}]),
                "max_output_tokens": max_tokens}
        if tools:
            body["tools"] = tools
        try:
            r = requests.post(f"{base}/responses", headers=hdrs, json=body,
                              timeout=LLM_TIMEOUT)
            r.raise_for_status()
            j = r.json()
            cites = j.get("citations") or []
            print(f"[llm] grok responses ok ({model}) · tools={len(tools)} "
                  f"· citations={len(cites)}")
            return _extract_responses_text(j)
        except Exception as e:
            print(f"[llm] grok call failed: {e.__class__.__name__}: {e}")
            return ""

    messages = [{"role": "system", "content": system_prompt}]   # ---- OpenAI-style
    for role, content in fewshots:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})
    body = {"model": model, "messages": messages,
            "temperature": temp, "max_tokens": max_tokens}
    try:
        r = requests.post(f"{base}/chat/completions", headers=hdrs, json=body,
                          timeout=LLM_TIMEOUT)
        r.raise_for_status()
        print(f"[llm] chat/completions ok ({model})")
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[llm] call failed: {e.__class__.__name__}: {e}")
        return ""


# --------------------------------------------------------------------------- #
#  SANITIZE + GUARDS
# --------------------------------------------------------------------------- #
def sanitize_long(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t.strip())
    t = re.sub(r"^\s*(sources?|citations?)\s*:.*$", "", t,
               flags=re.I | re.M | re.S)                      # trailing cite blocks
    t = re.sub(r"[\[【]\d+[\]】]", "", t)                      # [1] style refs
    t = re.sub(r"(\*\*|__|\*|_|#+\s|>\s)", "", t)             # markdown
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\b[\w-]+\.(?:com|net|org|io|xyz|co|ai)\b", "", t, flags=re.I)
    t = re.sub(r"[@#](\w)", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    cap = int(env("MAX_LONG_CHARS", "6000"))
    if len(t) > cap:
        cut = t.rfind("\n\n", 0, cap)
        t = t[:cut].strip() if cut > cap * 0.5 else t[:cap].rsplit(" ", 1)[0] + "…"
        print(f"[sanitize] long text trimmed to {len(t)} chars")
    return t


def word_count(t: str) -> int:
    return len(re.findall(r"\b\w+\b", t))


ADVICE_PAT = re.compile(
    r"\b(you should (?:buy|sell|hold|accumulate)|time to (?:buy|sell)|"
    r"now is (?:the|a) (?:good )?(?:time|moment) to (?:buy|sell)|"
    r"consider (?:buying|selling)|back up the truck|leverage up|"
    r"take profits? (?:now|here)|accumulate (?:here|now|aggressively)|"
    r"(?:buy|sell) the dip)\b", re.I)
REFUSAL_PAT = re.compile(r"\b(as an ai|i can'?t|i cannot|language model)\b", re.I)


def long_text_ok(t: str, min_w: int) -> str:
    if not t or word_count(t) < 40:
        return "empty/too tiny"
    if REFUSAL_PAT.search(t[:200]):
        return "refusal-shaped"
    if ADVICE_PAT.search(t):
        return "contains investment advice"
    if word_count(t) < min_w:
        return f"too short ({word_count(t)} words)"
    return ""


# ---- short-mode fallback pieces (neutral: no interpretation, no advice) ----
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

_W1 = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def x_len(s: str) -> int:
    return sum(1 if any(a <= ord(c) <= b for a, b in _W1) else 2 for c in s)


def fmt_day(date_str: str) -> str:
    d = dt.date.fromisoformat(date_str)
    return d.strftime("%b %d").replace(" 0", " ")


def header_lines(risk, ddate, hist):
    l1 = f"{level_emoji(risk)} BTC Risk {risk:.2f} — {level_word(risk)}"
    tags = re.sub(r"\s+", " ", env("HEADER_TAGS", "")).strip()   # e.g. "$BTC #Bitcoin"
    if tags:
        l1 = f"{l1} {tags}"
    return (l1, f"📊 {hist} · as of {fmt_day(ddate)}")


FOOTER = "not financial advice · full model → link in bio"


def build_short(risk, ddate, hist, today) -> str:
    l1, l2 = header_lines(risk, ddate, hist)
    humor = FALLBACK_HUMOR[today.timetuple().tm_yday % len(FALLBACK_HUMOR)]
    for parts in ((l1, l2, humor, FOOTER), (l1, l2, FOOTER), (l1, FOOTER), (l1,)):
        t = "\n".join(parts)
        if x_len(t) <= SHORT_MAX_WEIGHT:
            return t
    return l1


def build_long(risk, ddate, hist, body) -> str:
    l1, l2 = header_lines(risk, ddate, hist)
    return f"{l1}\n{l2}\n\n{body}\n\n{FOOTER}"


# --------------------------------------------------------------------------- #
#  THREAD MODE — split the same news-grounded column into a chain of
#  <=280-weighted-char tweets. Tweet 1 carries the risk header (+ gauge image,
#  attached at post time); the last tweet carries the footer; each is numbered
#  "i/N". Achieves ~250 words WITHOUT X Premium (Premium is only needed for a
#  single >280-char post). Each tweet is a separate $0.015 post — THREAD_MAX_
#  TWEETS caps the daily cost.
# --------------------------------------------------------------------------- #
THREAD_CONTENT_BUDGET = 262      # weighted chars per tweet before the "i/N" tag
THREAD_NUM_RESERVE = 8           # room for "\n12/12"


def _sentences(text: str) -> list:
    flat = re.sub(r"\s*\n\s*", " ", text).strip()      # flatten paragraphs
    parts = re.split(r'(?<=[.!?…"])\s+', flat)
    return [p.strip() for p in parts if p.strip()]


def _wjoin(a: str, b: str, sep: str) -> str:
    return (a + sep + b) if a else b


def _hardwrap(s: str, budget: int) -> list:
    """Last resort: split a single over-budget sentence on word boundaries."""
    out, cur = [], ""
    for w in s.split(" "):
        cand = _wjoin(cur, w, " ")
        if x_len(cand) <= budget:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out or [s[:budget]]


def _fit_ellipsis(t: str, budget: int) -> str:
    while x_len(t + "…") > budget and " " in t:
        t = t.rsplit(" ", 1)[0].rstrip(",;:—- ")
    return t + "…"


def build_thread_tweets(header: str, body: str, footer: str,
                        max_tweets: int) -> list:
    """Return a list of ready-to-post tweet strings (numbered), or [] if empty."""
    budget = THREAD_CONTENT_BUDGET
    sents = _sentences(body)
    if not sents:
        return []

    tweets, cur, i = [], header, 0
    seeded_header = True
    while i < len(sents):
        s = sents[i]
        sep = "\n\n" if (cur == header and seeded_header) else (" " if cur else "")
        cand = _wjoin(cur, s, sep)
        if x_len(cand) <= budget:
            cur, i = cand, i + 1
            seeded_header = False if cur != header else seeded_header
        elif cur and cur != header:
            tweets.append(cur); cur = ""; seeded_header = False
        elif cur == header:                       # header + 1st sentence too big
            tweets.append(header); cur = ""; seeded_header = False
        else:                                     # lone giant sentence
            pieces = _hardwrap(s, budget)
            tweets.extend(pieces[:-1]); cur = pieces[-1]; i += 1
        if len(tweets) >= max_tweets - 1:         # keep 1 slot for footer/close
            break
    if cur:
        tweets.append(cur)
    if i < len(sents) and tweets:                 # hit the cap: mark truncation
        tweets[-1] = _fit_ellipsis(tweets[-1], budget)

    # footer: append to last tweet if it fits, else its own (budget permitting)
    if x_len(_wjoin(tweets[-1], footer, "\n\n")) <= budget:
        tweets[-1] = _wjoin(tweets[-1], footer, "\n\n")
    elif len(tweets) < max_tweets:
        tweets.append(footer)
    else:
        tweets[-1] = _wjoin(_fit_ellipsis(tweets[-1], budget - x_len(footer) - 2),
                            footer, "\n\n")

    n = len(tweets)
    if n > 1:
        tweets = [f"{t}\n{idx + 1}/{n}" for idx, t in enumerate(tweets)]
    return [t if x_len(t) <= 280 else _fit_ellipsis(t, 280) for t in tweets]


# --------------------------------------------------------------------------- #
#  X POSTING — media upload (v2 chunked: initialize/append/finalize, with a
#  legacy single-shot fallback) + create_tweet, long→short auto-degrade.
# --------------------------------------------------------------------------- #
def _oauth1():
    from requests_oauthlib import OAuth1
    return OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                  os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_TOKEN_SECRET"])


def upload_media(png_path: str, alt_text: str) -> str:
    """Returns media_id string, or '' on any failure (non-fatal)."""
    try:
        auth = _oauth1()
        data = open(png_path, "rb").read()

        def _mid(j):
            return str((j.get("data") or {}).get("id")
                       or j.get("media_id_string") or j.get("media_id") or "")

        try:  # v2 chunked flow
            r = requests.post(f"{X_API}/2/media/upload/initialize", auth=auth,
                              json={"media_type": "image/png",
                                    "total_bytes": len(data),
                                    "media_category": "tweet_image"}, timeout=30)
            r.raise_for_status()
            mid = _mid(r.json())
            if not mid:
                raise RuntimeError(f"no id in init: {r.text[:120]}")
            r = requests.post(f"{X_API}/2/media/upload/{mid}/append", auth=auth,
                              data={"segment_index": 0},
                              files={"media": ("gauge.png", data, "image/png")},
                              timeout=60)
            r.raise_for_status()
            r = requests.post(f"{X_API}/2/media/upload/{mid}/finalize",
                              auth=auth, timeout=30)
            r.raise_for_status()
            print(f"[media] v2 chunked upload ok — id {mid}")
        except Exception as e1:  # legacy single-shot fallback
            print(f"[media] chunked flow failed ({e1.__class__.__name__}: {e1}) "
                  f"— trying single-shot")
            r = requests.post(f"{X_API}/2/media/upload", auth=auth,
                              data={"media_category": "tweet_image"},
                              files={"media": ("gauge.png", data, "image/png")},
                              timeout=60)
            r.raise_for_status()
            mid = _mid(r.json())
            if not mid:
                raise RuntimeError(f"no id: {r.text[:120]}")
            print(f"[media] single-shot upload ok — id {mid}")

        try:  # alt text: best effort
            requests.post(f"{X_API}/2/media/metadata", auth=auth,
                          json={"id": mid,
                                "metadata": {"alt_text": {"text": alt_text[:990]}}},
                          timeout=15)
        except Exception:
            pass
        return mid
    except Exception as e:
        print(f"[media] upload failed entirely ({e.__class__.__name__}: {e}) "
              f"— posting without image")
        return ""


def _tweepy_client():
    import tweepy
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"])


def post_thread(tweets: list, media_id: str):
    """Post a reply chain. Image + no reply target on tweet 1. Returns (ok, posted, total).
    ok is True if at least the anchor (tweet 1) went out."""
    import tweepy
    client = _tweepy_client()
    prev, posted = None, 0
    for idx, t in enumerate(tweets):
        kwargs = {"text": t}
        if idx == 0 and media_id:
            kwargs["media_ids"] = [media_id]
        if prev:
            kwargs["in_reply_to_tweet_id"] = prev
        try:
            resp = client.create_tweet(**kwargs)
            prev = resp.data.get("id")
            posted += 1
            print(f"[post] thread {idx + 1}/{len(tweets)} ok — id {prev}")
        except tweepy.Forbidden as e:
            if "duplicate" in str(e).lower():
                print(f"[post] thread {idx + 1} duplicate — skipping this one")
                continue
            print(f"[post] thread {idx + 1} forbidden ({e}) — stopping chain")
            break
        except Exception as e:
            print(f"[post] thread {idx + 1} failed ({e.__class__.__name__}: {e}) "
                  f"— stopping chain")
            break
    if 0 < posted < len(tweets):
        print(f"[post] WARNING: partial thread ({posted}/{len(tweets)} posted); "
              f"the anchor is live")
    return posted >= 1, posted, len(tweets)


def post_tweet(text: str, media_id: str):
    """Returns (ok, needs_short_fallback)."""
    import tweepy
    client = _tweepy_client()
    kwargs = {"text": text}
    if media_id:
        kwargs["media_ids"] = [media_id]
    try:
        resp = client.create_tweet(**kwargs)
        print(f"[post] ok — tweet id {resp.data.get('id')}")
        return True, False
    except tweepy.Forbidden as e:
        msg = str(e).lower()
        if "duplicate" in msg:
            print("[post] duplicate — treating as non-fatal")
            return True, False
        if len(text) > 280:
            print(f"[post] long post rejected ({e}) — the bot account likely "
                  f"lacks X Premium; will retry with the short format")
            return False, True
        print(f"[post] forbidden: {e}")
        return False, False
    except Exception as e:
        if len(text) > 280 and ("length" in str(e).lower() or "too long" in str(e).lower()):
            print(f"[post] length rejected ({e}) — retrying short")
            return False, True
        print(f"[post] failed: {e.__class__.__name__}: {e}")
        return False, False


def write_summary(mode, tweet, extra):
    path = env("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"## Daily risk post — {mode}\n\n```\n{tweet}\n```\n\n")
            for k, v in extra.items():
                f.write(f"- {k}: {v}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  BODY GENERATION (shared by long + thread modes)
# --------------------------------------------------------------------------- #
def generate_body(risk, level, hist, ddate, today, min_w, max_w):
    """Return (body, used_llm). body='' means unusable → caller degrades."""
    sweet = min(max(min_w + 100, 220), max_w)
    sp = LONG_SYSTEM_PROMPT.format(min_w=min_w, max_w=max_w, sweet=sweet)
    headlines = fetch_headlines()
    grok = "api.x.ai" in env("LLM_BASE_URL", DEFAULT_LLM_BASE) \
        and env("GROK_SEARCH", "on").lower() != "off"
    umsg = build_long_user(risk, level, hist, ddate, headlines, grok)
    fewshots = [("user", LONG_FEWSHOT_USER), ("assistant", LONG_FEWSHOT_ASSISTANT)]

    body = sanitize_long(call_llm(sp, fewshots, umsg, 2200, today))
    problem = long_text_ok(body, min_w)
    if problem and "too short" in problem:
        print(f"[llm] {problem} — retrying once with expand instruction")
        retry_msg = (umsg + f"\n\nYour previous draft had {word_count(body)} "
                     f"words. Write a NEW version between {min_w} and {max_w} "
                     f"words with more depth. Same rules.")
        body2 = sanitize_long(call_llm(sp, fewshots, retry_msg, 2600, today))
        if not long_text_ok(body2, min_w):
            body, problem = body2, ""
        elif word_count(body2) > word_count(body):
            body, problem = body2, long_text_ok(body2, min_w)
    floor = max(80, min_w // 2)
    if problem and "too short" in problem and word_count(body) >= floor:
        print(f"[llm] still short ({word_count(body)}w ≥ {floor}) — using anyway")
        problem = ""
    if not problem and body:
        print(f"[bot] body: {word_count(body)} words")
        return body, True
    print(f"[llm] body unusable ({problem or 'no output'})")
    return "", False


# --------------------------------------------------------------------------- #
#  MAIN
# --------------------------------------------------------------------------- #
def main() -> int:
    dry = env("BOT_DRY_RUN", "true").lower() != "false"
    today = dt.datetime.now(dt.timezone.utc).date()
    mode = env("TWEET_MODE", "long").lower()
    print(f"[bot] {today.isoformat()} UTC — mode {mode.upper()} — "
          f"{'DRY-RUN' if dry else 'LIVE'}")

    try:
        data = validate_data(load_data())
    except Exception as e:
        print(f"[bot] FATAL: cannot load canonical data: {e}")
        return 1
    risk, ddate = data["risk"], data["date"]
    stale = staleness_days(ddate)
    print(f"[bot] risk={risk:.4f} lastDate={ddate} (staleness {stale}d; 1d normal)")
    if stale > int(env("MAX_STALE_DAYS", "3")):
        if dry:
            print("[bot] WARNING: stale data (continuing: DRY-RUN)")
        else:
            print("[bot] FATAL: refusing to post stale numbers")
            return 1

    level = level_word(risk)
    hist = history_line(data["r"], risk)

    # ---- generate body + assemble ------------------------------------------
    # fmt: text (single post) OR thread (list of posts). Exactly one is set.
    text, thread, used_llm, fmt = "", None, False, "short"
    l1, l2 = header_lines(risk, ddate, hist)
    header = f"{l1}\n{l2}"

    if mode == "thread":
        min_w = int(env("LONG_MIN_WORDS", "150"))
        max_w = int(env("LONG_MAX_WORDS", "250"))
        max_tweets = max(2, int(env("THREAD_MAX_TWEETS", "8")))
        body, used_llm = generate_body(risk, level, hist, ddate, today, min_w, max_w)
        if body:
            if re.search(r"https?://|\b[\w-]+\.(?:com|net|org|io|xyz|co|ai)\b",
                         body, re.I):
                print("[bot] FATAL: URL/domain in generated body — aborting")
                return 1
            thread = build_thread_tweets(header, body, FOOTER, max_tweets)
            fmt = "thread"
        else:
            print("[llm] thread unusable — degrading to short single post")

    elif mode == "long":
        min_w = int(env("LONG_MIN_WORDS", "250"))
        max_w = int(env("LONG_MAX_WORDS", "750"))
        body, used_llm = generate_body(risk, level, hist, ddate, today, min_w, max_w)
        if body:
            text, fmt = build_long(risk, ddate, hist, body), "long"
        else:
            print("[llm] long unusable — degrading to short single post")

    if not text and not thread:                    # short mode, or any degrade
        text, fmt = build_short(risk, ddate, hist, today), "short"

    # URL guard on the final post TEXT (single) — image text is exempt & free
    if text and re.search(r"https?://|\b[\w-]+\.(?:com|net|org|io|xyz|co|ai)\b",
                          text, re.I):
        print("[bot] FATAL: URL/domain in post text — aborting")
        return 1
    if text and len(text) > 24000:
        print("[bot] FATAL: text over X's 25k limit?!")
        return 1

    # ---- gauge image ---------------------------------------------------------
    media_path = ""
    if env("ATTACH_GAUGE", "true").lower() != "false":
        try:
            from render_gauge import render
            media_path = render(risk, level, hist, ddate, "/tmp/gauge_today.png")
        except Exception as e:
            print(f"[gauge] render failed ({e.__class__.__name__}: {e}) — "
                  f"posting without image")

    # ---- preview -------------------------------------------------------------
    print("─" * 62)
    if thread:
        for i, t in enumerate(thread):
            print(t)
            print(f"    ┈┈ tweet {i + 1}/{len(thread)} · {x_len(t)}/280 weighted ┈┈")
    else:
        print(text)
    print("─" * 62)
    if thread:
        words = sum(word_count(t) for t in thread)
        print(f"[bot] THREAD {len(thread)} tweets · {words}w total · "
              f"LLM {'✓' if used_llm else 'fallback'} · image "
              f"{'✓' if media_path else '—'} · est ${0.015 * len(thread):.3f}")
    else:
        print(f"[bot] {fmt.upper()} "
              f"{word_count(text) if len(text) > 280 else str(x_len(text)) + '/280'}"
              f" · LLM {'✓' if used_llm else 'fallback'} · image "
              f"{'✓' if media_path else '—'}")

    extra = {"data date": ddate, "risk": f"{risk:.4f}", "level": level,
             "format": fmt, "LLM": "used" if used_llm else "fallback",
             "gauge": "attached" if media_path else "none"}
    if thread:
        extra["tweets"] = len(thread)
        extra["est cost"] = f"${0.015 * len(thread):.3f}"

    preview = ("\n\n— — —\n\n".join(thread)) if thread else text

    if dry:
        write_summary("DRY-RUN (not posted)", preview, extra)
        if media_path:
            print(f"[bot] gauge image at {media_path} (not uploaded in dry-run)")
        print("[bot] DRY-RUN — nothing posted.")
        return 0

    missing = [k for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
                           "X_ACCESS_TOKEN_SECRET") if not env(k)]
    if missing:
        print(f"[bot] FATAL: missing X secrets: {', '.join(missing)}")
        return 1

    alt = f"Gauge: Bitcoin risk {risk:.2f} ({level}), {hist}, as of {ddate}."
    media_id = upload_media(media_path, alt) if media_path else ""

    if thread:
        ok, posted, total = post_thread(thread, media_id)
        extra["posted"] = f"{posted}/{total}"
        write_summary("POSTED" if ok else "POST FAILED", preview, extra)
        return 0 if ok else 1

    ok, retry_short = post_tweet(text, media_id)
    if retry_short:                                # long post + no Premium
        text = build_short(risk, ddate, hist, today)
        extra["format"] = "short (Premium fallback)"
        ok, _ = post_tweet(text, media_id)
    write_summary("POSTED" if ok else "POST FAILED", text, extra)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

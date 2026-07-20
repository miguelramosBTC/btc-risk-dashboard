#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# repo path: bot/test_assembly.py
"""
Offline tests for post_risk_update.py v2 + render_gauge.py — no secrets,
no live LLM/X calls. Run:  python3 bot/test_assembly.py
"""
import datetime as dt
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import post_risk_update as bot

FAILS = []
def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

# ---- 1. neutral levels (no recommendations anywhere) ------------------------
print("[1] neutral level words + emoji")
for r, lvl, em in ((0.05, "LOW", "🟢"), (0.2999, "LOW", "🟢"), (0.30, "MODERATE", "🟡"),
                   (0.5999, "MODERATE", "🟡"), (0.60, "ELEVATED", "🟠"),
                   (0.7999, "ELEVATED", "🟠"), (0.80, "HIGH", "🔴"), (1.0, "HIGH", "🔴")):
    check(f"r={r}", bot.level_word(r) == lvl and bot.level_emoji(r) == em,
          f"{bot.level_word(r)}/{bot.level_emoji(r)}")
banned = ("ACCUMULATE", "DISTRIBUTE", "HOLD", "base", "stack", "Buy", "Sell")
hdr = "\n".join(bot.header_lines(0.28, "2026-07-06", "Lower than 78% of the last 365 days"))
check("header carries no regime/advice words",
      not any(b in hdr for b in banned), hdr)

# ---- 2. 365-day history ------------------------------------------------------
print("[2] history_line (365d)")
rs = [0.50] * 400 + [0.20] * 60 + [0.35]           # 0.35: above last 60, below the 400 before
h = bot.history_line(rs, 0.35)
check("365d percentile phrasing", "last 365 days" in h and h.startswith(("Higher", "Lower")), h)
rs = [0.30] * 400 + [0.61]
check("long streak → highest-in-N", bot.history_line(rs, 0.61) == "Highest reading in 365 days",
      bot.history_line(rs, 0.61))
rs = [0.30] * 50 + [0.61]                           # streak < 90 → percentile, small window note
h = bot.history_line(rs, 0.61)
check("short series uses actual span", "last 50 days" in h, h)
rs = ([0.20, 0.80] * 60) + [0.55]                   # 30-89 day extremes no longer trigger streak
h = bot.history_line(rs, 0.55)
check("sub-90 extreme → percentile", "% of the last" in h, h)

# ---- 3. long sanitizer -------------------------------------------------------
print("[3] sanitize_long")
raw = ("**Bold** take:\n\nVisit https://x.com and bitcoinrisk.net now [1].\n\n\n\n"
       "Ask @powell about it #btc.\n\nSources: reuters. com stuff")
t = bot.sanitize_long(raw)
check("markdown stripped", "**" not in t)
check("urls+domains stripped", "http" not in t and ".net" not in t and "x.com" not in t)
check("refs stripped", "[1]" not in t)
check("mentions/hashtags de-fanged", "@" not in t and "#" not in t and "powell" in t)
check("sources block removed", "Sources" not in t and "reuters" not in t.lower())
check("paragraphs preserved (max 1 blank line)", "\n\n" in t and "\n\n\n" not in t)
os.environ["MAX_LONG_CHARS"] = "300"
long_in = "para one word " * 20 + "\n\n" + "para two word " * 20 + "\n\n" + "para three word " * 20
t = bot.sanitize_long(long_in)
check("hard cap trims at paragraph", len(t) <= 300, str(len(t)))
del os.environ["MAX_LONG_CHARS"]

# ---- 4. advice guard ---------------------------------------------------------
print("[4] advice guard")
good = "The model reads 0.41 today, higher than 58% of the last 365 days. " * 12
check("clean 40+ word text passes", bot.long_text_ok(good, 40) == "")
for bad in ("Honestly it's time to buy here.", "You should sell before Friday.",
            "Consider buying the dip today.", "Just leverage up and enjoy."):
    res = bot.long_text_ok(good + " " + bad, 40)
    check(f"rejects: {bad[:28]}…", res == "contains investment advice", res)
check("politicians 'buy votes' is fine",
      bot.long_text_ok(good + " Politicians buy votes with printed money.", 40) == "")
check("too-short flagged", "too short" in bot.long_text_ok("word " * 60, 250))

# ---- 4b. discovery tags -------------------------------------------------------
print("[4b] compose_tags")
import datetime as _dt
_d = _dt.date(2026, 7, 10)
os.environ.pop("HEADER_TAGS", None); os.environ.pop("DYNAMIC_TAGS", None)
os.environ.pop("MAX_TAGS", None)
check("off + no manual → empty", bot.compose_tags("story about the Fed", _d) == "")
os.environ["DYNAMIC_TAGS"] = "on"
t = bot.compose_tags("Powell and the Fed held interest rates steady again", _d)
check("dynamic: $BTC + topical", t == "$BTC #Fed", t)
t = bot.compose_tags("EU ministers advanced sweeping regulation of wallets", _d)
check("dynamic: regulation match", t == "$BTC #Regulation", t)
t1 = bot.compose_tags("", _d)
t2 = bot.compose_tags("", _d + _dt.timedelta(days=1))
check("no topic → generic rotates daily", t1 != t2 and t1.startswith("$BTC #"),
      f"{t1} / {t2}")
check("cap default = 2 tags", len(bot.compose_tags("fed etf mining story", _d).split()) == 2)
os.environ["HEADER_TAGS"] = "$BTC"
check("manual $BTC dedupes with dynamic",
      bot.compose_tags("the Fed again", _d) == "$BTC #Fed",
      bot.compose_tags("the Fed again", _d))
os.environ["MAX_TAGS"] = "5"
check("MAX_TAGS hard-clamped ≤3",
      len(bot.compose_tags("fed inflation etf halving", _d).split()) <= 3)
os.environ.pop("HEADER_TAGS"); os.environ.pop("DYNAMIC_TAGS"); os.environ.pop("MAX_TAGS")
hdr_tagged = bot.header_lines(0.28, "2026-07-08", "x", "$BTC #Fed")[0]
check("tags land on header line 1", hdr_tagged.endswith("$BTC #Fed"), hdr_tagged)

# ---- 5. assembly (long + short fallback) --------------------------------------
print("[5] assembly")
body = "Hook line.\n\nParagraph one about the thing.\n\nCloser."
full = bot.build_long(0.28, "2026-07-06", "Lower than 78% of the last 365 days", body)
check("long: header first", full.startswith("🟢 BTC Risk 0.28 — LOW"))
check("long: 365 line + as-of", "last 365 days · as of Jul 6" in full)
check("long: footer last", full.rstrip().endswith("full model → link in bio"))
short = bot.build_short(0.91, "2026-07-06", "Highest reading in 365 days",
                        dt.date(2026, 7, 6))
check("short: fits 280 weighted", bot.x_len(short) <= 276, str(bot.x_len(short)))
check("short: no advice words", not any(b in short for b in banned), short)
check("word_count sane", bot.word_count("one two, three-four five") == 5,
      str(bot.word_count("one two, three-four five")))

# ---- 6. gauge render + pixel validation ---------------------------------------
print("[6] render_gauge")
import render_gauge as rg
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "g.png")
    rg.render(0.62, "ELEVATED", "Higher than 81% of the last 365 days",
              "2026-07-06", p)
    from PIL import Image
    im = Image.open(p).convert("RGB")
    check("size 1200x675", im.size == (1200, 675), str(im.size))
    check("file > 20KB", os.path.getsize(p) > 20000)
    px = im.load()
    def near(c1, c2, tol=60): return all(abs(a-b) <= tol for a, b in zip(c1, c2))
    def pol(cx, cy, r, deg):
        a = math.radians(deg); return int(cx + r*math.cos(a)), int(cy + r*math.sin(a))
    check("ring v=0 blue", near(px[pol(400, 352, 232, 135)], (30, 111, 235)))
    check("ring v=1 red", near(px[pol(400, 352, 232, 405)], (200, 46, 52)))
    ang = 135 + 270 * 0.62
    hits = sum(near(px[pol(400, 352, r, ang)], (244, 247, 251)) for r in (130, 160, 190, 215))
    check("needle at 0.62", hits >= 3, f"{hits}/4")
    edge = all(near(px[x, y], (11, 17, 26), 30)
               for x in range(1188, 1200, 3) for y in range(300, 560, 8))
    check("no right-edge overflow", edge)

# ---- 6c. gauge extreme treatment + brand relocation --------------------------
print("[6c] gauge extreme + brand relocation")
from PIL import Image as _Img
def _near(a, b, t=45): return all(abs(x - y) <= t for x, y in zip(a, b))
def _isbg(c, t=20):
    return (all(abs(a - b) <= t for a, b in zip(c, (11, 17, 26)))
            or all(abs(a - b) <= t for a, b in zip(c, (16, 23, 34))))
check("date formatter → 'Jul 7'", rg._fmt_date("2026-07-07") == "Jul 7",
      rg._fmt_date("2026-07-07"))
GOLD = (232, 200, 74)
with tempfile.TemporaryDirectory() as td:
    pe, pn = os.path.join(td, "e.png"), os.path.join(td, "n.png")
    rg.render(0.08, "LOW", "Lowest reading in 300 days", "2026-07-07", pe)
    rg.render(0.62, "ELEVATED", "Higher than 74% of the last 365 days",
              "2026-07-07", pn)
    ie = _Img.open(pe).convert("RGB").load()
    ino = _Img.open(pn).convert("RGB").load()
    check("brand top-left (extreme card)",
          sum(_near(ie[x, y], GOLD, 70) for x in range(34, 440, 6)
              for y in range(30, 80, 4)) >= 8)
    check("brand top-left (normal card)",
          sum(_near(ino[x, y], GOLD, 70) for x in range(34, 440, 6)
              for y in range(30, 80, 4)) >= 8)
    check("brand no longer bottom-right",
          sum(_near(ino[x, y], GOLD, 70) for x in range(980, 1170, 6)
              for y in range(600, 650, 4)) <= 2)
    lc = rg.risk_color(0.08)
    check("extreme edge glow in level color",
          sum(1 for x in range(2, 28, 2)
              if ie[x, 338][2] > 55 and ie[x, 338][2] > ie[x, 338][0] + 12) >= 5)
    check("extreme badge pill top-right",
          sum(_near(ie[x, y], lc, 55) for x in range(1050, 1180, 4)
              for y in range(30, 80, 4)) >= 15)
    check("normal card: no edge glow", _isbg(ino[5, 338]), str(ino[5, 338]))
    check("normal card: no badge",
          sum(_near(ino[x, y], rg.risk_color(0.62), 45)
              for x in range(1050, 1180, 5) for y in range(30, 80, 5)) <= 3)
with tempfile.TemporaryDirectory() as td:                # kill-switch works
    os.environ["EXTREME_STYLE"] = "off"
    po = os.path.join(td, "o.png")
    rg.render(0.08, "LOW", "Lowest reading in 300 days", "2026-07-07", po)
    check("EXTREME_STYLE=off disables glow",
          _isbg(_Img.open(po).convert("RGB").load()[5, 338]))
    del os.environ["EXTREME_STYLE"]

# ---- 6b. thread splitter ------------------------------------------------------
print("[6b] build_thread_tweets")
hdr = "\n".join(bot.header_lines(0.28, "2026-07-06", "Lower than 78% of the last 365 days"))
body250 = (
    "Two hundred euros is now the threshold at which your money becomes state business. "
    "Per the wires, ministers advanced a plan to trace transfers above that line, "
    "self-custody included. The people drafting it have never waited for a Friday wire to clear. "
    "The stated goal is fighting crime; the historical record of these thresholds is that "
    "they only ever move down. Yesterday ten thousand, today two hundred, tomorrow lower. "
    "Meanwhile the asset they cannot trace by committee kept doing what it does, which is "
    "nothing anyone can vote on. Bitcoin does not care who chairs the working group. "
    "There is no threshold to lower because there is nobody with the authority to lower it. "
    "For the record-keepers, the model reads a number today, a fact and not a nudge. "
    "They can trace two hundred euros. They still cannot print a single sat, and that is "
    "the whole point of the exercise nobody in the room wanted to admit out loud today.")
tw = bot.build_thread_tweets(hdr, body250, bot.FOOTER, 8)
check("thread produced ≥2 tweets", len(tw) >= 2, str(len(tw)))
check("thread ≤ max_tweets", len(tw) <= 8, str(len(tw)))
check("every tweet ≤280 weighted", all(bot.x_len(t) <= 280 for t in tw),
      str([bot.x_len(t) for t in tw]))
check("tweet 1 starts with risk header", tw[0].startswith("🟢 BTC Risk 0.28 — LOW"))
check("footer present in last tweet", "not financial advice" in tw[-1])
check("numbering i/N present", all(f"/{len(tw)}" in t for t in tw))
check("no advice words across thread", not any(w in " ".join(tw)
      for w in ("ACCUMULATE", "you should buy", "leverage up")))
cap = bot.build_thread_tweets(hdr, body250 * 3, bot.FOOTER, 4)   # force the cap
check("respects max_tweets cap", len(cap) <= 4, str(len(cap)))
check("capped thread still ≤280", all(bot.x_len(t) <= 280 for t in cap))
check("capped thread ends with ellipsis or footer",
      cap[-1].rstrip().endswith(("bio", "…")) or "not financial advice" in cap[-1])

# ---- 7. e2e dry runs -----------------------------------------------------------
print("[7] end-to-end dry runs (fake LLM, no network)")
series_r = [round(0.30 + 0.25 * ((i % 200) / 200), 4) for i in range(520)]
series_r[-1] = 0.28
today = dt.datetime.now(dt.timezone.utc).date()
data = {"model": {"lastRisk": 0.2800,
                  "lastDate": (today - dt.timedelta(days=1)).isoformat()},
        "series": {"t": ["x"] * len(series_r), "p": [1] * len(series_r),
                   "r": series_r, "c": [8] * len(series_r)}}
fake_body = ("Two hundred euros is the new crime scene.\n\n" +
             ("Per the wires, a committee met and decided your coffee money needs "
              "supervision, which is the kind of sentence that writes its own satire. "
              "The people drafting it have never waited for a Friday wire to clear. ")
             * 5 +
             "\n\nBitcoin held its reading and asked nobody's permission.\n\n"
             "The model prints 0.28 — LOW, lower than most of the past year. "
             "A number, not a nudge.")
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "data.json")
    json.dump(data, open(p, "w"))
    os.environ.update({"DATA_FILE": p, "BOT_DRY_RUN": "true",
                       "NEWS_FEEDS": "http://127.0.0.1:1/none",
                       "TWEET_MODE": "long", "ATTACH_GAUGE": "true",
                       "LONG_MIN_WORDS": "80",
                       "BOT_FAKE_LLM_TEXT": fake_body})
    check("long dry-run rc==0", bot.main() == 0)

    os.environ["BOT_FAKE_LLM_TEXT"] = "You should buy now. " * 30   # advice → degrade
    check("advice output degrades to short, rc==0", bot.main() == 0)

    os.environ["TWEET_MODE"] = "thread"                            # thread dry-run
    os.environ["LONG_MIN_WORDS"] = "60"
    os.environ["BOT_FAKE_LLM_TEXT"] = fake_body
    check("thread dry-run rc==0", bot.main() == 0)
    os.environ["BOT_FAKE_LLM_TEXT"] = ""                           # no LLM → degrade
    check("thread w/o LLM degrades to short, rc==0", bot.main() == 0)
    os.environ["BOT_FAKE_LLM_TEXT"] = fake_body
    os.environ["TWEET_MODE"] = "long"

    os.environ["BOT_FAKE_LLM_TEXT"] = ""                            # no LLM → short
    os.environ["TWEET_MODE"] = "short"
    os.environ["ATTACH_GAUGE"] = "false"
    check("short mode rc==0", bot.main() == 0)

    data["model"]["lastDate"] = (today - dt.timedelta(days=9)).isoformat()
    json.dump(data, open(p, "w"))
    os.environ["BOT_DRY_RUN"] = "false"
    check("stale LIVE aborts rc==1", bot.main() == 1)
    os.environ["BOT_DRY_RUN"] = "true"

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("All checks passed.")

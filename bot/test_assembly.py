#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# repo path: bot/test_assembly.py
"""
Offline tests for post_risk_update.py — no network, no secrets needed.
Run:  python3 bot/test_assembly.py
Covers: actionFor parity at every band boundary (must stay identical to the
dashboard), X weighted length, sanitizer, LLM JSON parsing, the trim ladder,
history-stat selection, and a full dry-run of main() on synthetic data.
"""
import json, os, sys, tempfile, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import post_risk_update as bot

FAILS = []
def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

# ---- 1. actionFor parity: exact dashboard boundaries ----------------------
print("[1] actionFor boundaries (verbatim vs dashboard)")
cases = [
    (0.0999, "buy", 5, "ACCUMULATE — MAX"), (0.10, "buy", 4, "ACCUMULATE — HIGH"),
    (0.1999, "buy", 4, "ACCUMULATE — HIGH"), (0.20, "buy", 3, "ACCUMULATE"),
    (0.2999, "buy", 3, "ACCUMULATE"),        (0.30, "hold", 0, "HOLD"),
    (0.5999, "hold", 0, "HOLD"),             (0.60, "sell", 2, "DISTRIBUTE"),
    (0.6999, "sell", 2, "DISTRIBUTE"),       (0.70, "sell", 3, "DISTRIBUTE"),
    (0.7999, "sell", 3, "DISTRIBUTE"),       (0.80, "sell", 4, "DISTRIBUTE — HEAVY"),
    (0.8999, "sell", 4, "DISTRIBUTE — HEAVY"), (0.90, "sell", 5, "DISTRIBUTE — MAX"),
    (1.0,    "sell", 5, "DISTRIBUTE — MAX"),
]
for r, kind, amt, regime in cases:
    a = bot.action_for(r)
    got = (a["kind"], a.get("mult", a.get("parts", 0)), a["regime"])
    check(f"r={r}", got == (kind, amt, regime), f"got {got}")

check("action_line buy",  bot.action_line({"kind": "buy", "mult": 3}) == "Buy 3× your base")
check("action_line hold", bot.action_line({"kind": "hold"}) == "Hold — neither buy nor sell")
check("action_line sell", bot.action_line({"kind": "sell", "parts": 4}) == "Sell 4/15 of your stack")

# ---- 2. X weighted length ---------------------------------------------------
print("[2] x_len weighted counting")
check("ascii", bot.x_len("abc def") == 7)
check("emoji=2", bot.x_len("🟠") == 2)
check("em-dash=1 (U+2014 in 8208-8223)", bot.x_len("—") == 1)
check("arrow=2 (U+2192 outside w1 ranges)", bot.x_len("→") == 2)
check("newline=1", bot.x_len("a\nb") == 3)
check("mixed", bot.x_len("🟡 BTC Risk 0.43 — HOLD") == 23)  # emoji(2) + 20 ascii/space + em-dash(1)

# ---- 3. sanitizer -----------------------------------------------------------
print("[3] sanitize")
check("strips urls", "http" not in bot.sanitize("go to https://x.com now", 90))
check("strips bare domains", ".net" not in bot.sanitize("visit bitcoinrisk.net today", 90))
check("de-fangs @/#", bot.sanitize("ask @elon about #btc", 90) == "ask elon about btc")
check("collapses ws + quotes", bot.sanitize('  "hello   world"  ', 90) == "hello world")
long = bot.sanitize("word " * 40, 50)
check("truncates at word + ellipsis", len(long) <= 50 and long.endswith("…"), long)

# ---- 4. LLM JSON parsing ----------------------------------------------------
print("[4] parse_llm_json")
ok = bot.parse_llm_json('{"interpretation":"a","humor":"b"}')
check("clean json", ok == ("a", "b"))
ok = bot.parse_llm_json('```json\n{"interpretation":"a","humor":"b"}\n```')
check("fenced json", ok == ("a", "b"))
ok = bot.parse_llm_json('Sure! Here you go: {"interpretation":"a","humor":"b"} hope it helps')
check("chatty wrapper", ok == ("a", "b"))
check("garbage -> None", bot.parse_llm_json("no json here") is None)
check("missing key -> None", bot.parse_llm_json('{"interpretation":"a"}') is None)
check("None -> None", bot.parse_llm_json(None) is None)

# ---- 5. history stats -------------------------------------------------------
print("[5] history_line")
rs = [0.30] * 100 + [0.50]                     # current 0.50 above the prior 100
check("N-day high", bot.history_line(rs, 0.50) == "📊 Highest reading in 101 days",
      bot.history_line(rs, 0.50))
rs = [0.60] * 100 + [0.20]
check("N-day low", bot.history_line(rs, 0.20) == "📊 Lowest reading in 101 days",
      bot.history_line(rs, 0.20))
rs = ([0.20, 0.80] * 60) + [0.55]              # alternating -> no 30-day streak
h = bot.history_line(rs, 0.55)
check("percentile phrasing", h.startswith("📊 Higher than") and "% of the last 90 days" in h, h)

# ---- 6. trim ladder ---------------------------------------------------------
print("[6] assemble trim ladder")
score = "🟠 BTC Risk 0.68 — DISTRIBUTE"
tail = "as of Jul 4 · full model → link in bio"
t = bot.assemble(score, "I" * 80, "📊 " + "H" * 40, "F" * 80, tail)
check("fits budget", bot.x_len(t) <= 276, str(bot.x_len(t)))
check("keeps all 5 lines when they fit", t.count("\n") == 4)
t = bot.assemble(score, "I" * 92, "📊 " + "H" * 56, "F" * 92, tail)
check("over-budget input trimmed", bot.x_len(t) <= 276, str(bot.x_len(t)))
check("score line survives trimming", t.startswith(score))
check("tail survives trimming", t.rstrip().endswith("bio"))

# ---- 7. full dry-run on synthetic data.json ---------------------------------
print("[7] end-to-end dry run (synthetic data, fake LLM, no network)")
series_r = [round(0.30 + 0.25 * ((i % 200) / 200), 4) for i in range(520)]
series_r[-1] = 0.61
today = dt.datetime.now(dt.timezone.utc).date()
data = {
    "model": {"lastRisk": 0.6100, "lastDate": (today - dt.timedelta(days=1)).isoformat()},
    "series": {
        "t": [(today - dt.timedelta(days=len(series_r) - i)).isoformat() for i in range(len(series_r))],
        "p": [50000.0] * len(series_r),
        "r": series_r,
        "c": [8] * len(series_r),
    },
}
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "data.json")
    json.dump(data, open(p, "w"))
    os.environ["DATA_FILE"] = p
    os.environ["BOT_DRY_RUN"] = "true"
    os.environ["NEWS_FEEDS"] = "http://127.0.0.1:1/none"   # force offline path fast
    os.environ["BOT_FAKE_LLM_JSON"] = json.dumps({
        "interpretation": "Distribution starts here: 2/15 off. Leverage is how you get carried out.",
        "humor": "New ATH and funding through the roof. Nothing bad ever follows that.",
    })
    rc = bot.main()
    check("main() dry-run rc==0", rc == 0)

    # contradiction tripwire: sell regime + 'buy more' interpretation -> fallback
    os.environ["BOT_FAKE_LLM_JSON"] = json.dumps({
        "interpretation": "Great time to buy more and leverage up!",
        "humor": "something witty"})
    rc = bot.main()
    check("contradiction swapped, still rc==0", rc == 0)

    # stale data in LIVE mode must abort (no X secrets present anyway)
    data["model"]["lastDate"] = (today - dt.timedelta(days=9)).isoformat()
    json.dump(data, open(p, "w"))
    os.environ["BOT_DRY_RUN"] = "false"
    rc = bot.main()
    check("stale LIVE aborts rc==1", rc == 1)
    os.environ["BOT_DRY_RUN"] = "true"

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s): {FAILS}")
    sys.exit(1)
print("All checks passed.")

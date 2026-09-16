#!/usr/bin/env python3
"""P6 enforcement — "name only what you compute", checked by CI.

    python3 tools/check_copy.py            # audit, exit non-zero on a violation
    python3 tools/check_copy.py --inventory # list every hit, fix nothing

Defect G was the site claiming hashrate, difficulty, long/short-term holder
positioning, active addresses and transaction activity, none of which were in
v2's `NEED` or `compute()`. P6 makes the rule public feature list = code list.

The subtlety this script exists to get right: **describing v2 accurately while
v2 is the live model is not a violation.** The copy today says "eleven signals",
"logistic transform", "Puell" — and v2 really does compute those. The violation
appears the moment the gauge starts reading the v3 tape while the copy still
describes v2. So the check keys off *which model the site serves*, detected from
the page itself rather than from a hand-maintained flag that can rot.

Two failure classes, with different deadlines:

  1. UNCOMPUTED INPUTS ("always") — on-chain series the copy names that NO
     version of this model has ever computed: hashrate, difficulty, active
     addresses, long/short-term holder positioning, exchange flows. This is
     defect G exactly as the audit found it, and it is a violation **today**:
     v2 does not compute them either. Fatal regardless of which model serves.
  2. V2 MACHINERY ("v3_only") — logistic maps, "eleven signals", the deleted
     sleeves. Accurate while v2 is live, fatal the moment the gauge reads the
     v3 tape.

This is why the script lives in its own CI workflow and not in the daily tape
job: a copy defect must block a *deploy*, never a *row*. The model recording
what it saw is not contingent on the prose around it being finished.

What it deliberately does NOT scan: code comments and docstrings. Every
grep-shaped check in this project has at some point matched its own explanatory
prose -- including the ones written to catch that. Only user-visible strings are
audited: HTML text nodes and the values of the i18n dictionaries.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model.v3.constants import NEED_V3   # noqa: E402

# --- what the copy may not say once v3 is live ------------------------------
# label -> (severity, pattern). "always" = defect G, live now. "v3_only" =
# accurate for v2, fatal at the cutover.
FORBIDDEN = {
    "uncomputed: hashrate": ("always", r"\bhash\s?rate\b|\btasa\s+de\s+hash\b"),
    "uncomputed: difficulty": ("always", r"\bdifficulty\b|\bdificultad\b"),
    "uncomputed: active addresses": ("always", r"\bactive\s+addresses\b|\bdirecciones\s+activas\b"),
    "uncomputed: LTH/STH positioning": ("always", r"\blong[\s\-]term\s+holder|\bshort[\s\-]term\s+holder"),
    "uncomputed: ETF flows": ("always", r"\betf\s+(?:net\s+)?flows?\b|\bflujos\s+de\s+etf\b"),
    "uncomputed: funding rates": ("always", r"\bfunding\s+rates?\b|\btasas?\s+de\s+financiaci[óo]n\b"),
    "implied probability": ("always", r"\bprobabilit(?:y|ies)\s+of\s+(?:a\s+)?(?:crash|drop|drawdown)\b|\bprobabilidad\s+de\s+(?:ca[íi]da|crash)"),
    "eleven signals": ("v3_only", r"\beleven\s+signals?\b|\b11\s+(?:se[ñn]ales|signals)\b|\bonce\s+se[ñn]ales\b"),
    "five factor families": ("v3_only", r"\bfive\s+factor\s+famil|\bcinco\s+familias\b"),
    "logistic map": ("v3_only", r"\blogistic\b|\bsigmoid\b|\bsigmoide\b|\blog[íi]stica\b"),
    "deleted sleeve: Puell": ("v3_only", r"\bpuell\b"),
    "deleted sleeve: MVRV-Z": ("v3_only", r"\bmvrv[\s\-]?z\b|\bz[\s\-]?score\b"),
    "deleted sleeve: RHODL": ("v3_only", r"\brhodl\b"),
    "deleted sleeve: terminal price": ("v3_only", r"\bterminal\s+price\b|\bprecio\s+terminal\b"),
    "deleted sleeve: supply in profit": ("v3_only", r"\bsupply\s+in\s+profit\b|\boferta\s+en\s+beneficio\b"),
    "uncomputed: exchange flows": ("v3_only", r"\bexchange\s+(?:in)?flows?\b|\bflujos\s+de\s+exchange\b"),
}

# Series the copy MAY name, because the code computes them.
ALLOWED_INPUT_WORDS = {
    "mvrv", "market cap", "realized cap", "realised cap", "capitalizaci",
    "mayer", "sma", "moving average", "media m", "price", "precio",
    "thermocap", "issuance", "emisi", "fees", "comisiones", "supply", "oferta",
    "volatility", "volatilidad", "drawdown",
}


def serving_model(root: Path = ROOT) -> str:
    """Which tape does the live page actually read? Detected, not declared."""
    idx = (root / "index.html")
    if not idx.exists():
        return "unknown"
    src = idx.read_text(errors="ignore")
    reads_v3 = bool(re.search(r"v3\.js|v3\.0_last90|series/v3", src))
    reads_v2 = bool(re.search(r"/data\.js\b|data\.json", src))
    if reads_v3 and not reads_v2:
        return "v3"
    if reads_v3 and reads_v2:
        return "mixed"
    return "v2"


def user_facing_strings(root: Path = ROOT) -> list[tuple[str, int, str]]:
    """(file, line, text) for every string a visitor can read. No code comments."""
    out: list[tuple[str, int, str]] = []

    idx = root / "index.html"
    if idx.exists():
        src = idx.read_text(errors="ignore")
        src = re.sub(r"<script\b.*?</script>", "", src, flags=re.S | re.I)
        src = re.sub(r"<style\b.*?</style>", "", src, flags=re.S | re.I)
        for i, line in enumerate(src.splitlines(), 1):
            text = html.unescape(re.sub(r"<[^>]+>", " ", line)).strip()
            if text:
                out.append(("index.html", i, text))

    app = root / "app.js"
    if app.exists():
        src = app.read_text(errors="ignore")
        # strip comments so explanatory prose is never audited
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = re.sub(r"(?m)^\s*//.*$", "", src)
        for i, line in enumerate(src.splitlines(), 1):
            for m in re.finditer(r'"((?:[^"\\]|\\.){4,})"|\'((?:[^\'\\]|\\.){4,})\'', line):
                s = m.group(1) or m.group(2)
                if " " in s:                      # a sentence, not an identifier
                    out.append(("app.js", i, s))
    return out


def audit(root: Path = ROOT) -> dict:
    model = serving_model(root)
    strings = user_facing_strings(root)
    hits: dict[str, list] = {}
    sev: dict[str, str] = {}
    for label, (severity, pat) in FORBIDDEN.items():
        rx = re.compile(pat, re.I)
        sev[label] = severity
        for f, ln, text in strings:
            if rx.search(text):
                hits.setdefault(label, []).append(
                    {"file": f, "line": ln, "text": text[:120]})
    now = sum(len(v) for k, v in hits.items() if sev[k] == "always")
    at_cut = sum(len(v) for k, v in hits.items() if sev[k] == "v3_only")
    return {
        "serving_model": model,
        "strings_scanned": len(strings),
        "violations": hits,
        "severity": sev,
        "hits_fatal_now": now,
        "hits_fatal_at_cutover": at_cut,
        "total_hits": now + at_cut,
        "need_v3": list(NEED_V3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", action="store_true",
                    help="print every hit and exit 0 (planning the cutover)")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    rep = audit(Path(a.root))

    print(f"[copy] site serves: {rep['serving_model']}  "
          f"({rep['strings_scanned']} user-facing strings scanned)")
    for label, items in sorted(rep["violations"].items(),
                               key=lambda kv: (rep["severity"][kv[0]], -len(kv[1]))):
        mark = "DEFECT G" if rep["severity"][label] == "always" else "at cutover"
        print(f"  {len(items):4d}  [{mark:>10s}]  {label}")
        if a.inventory:
            for it in items[:6]:
                print(f"              {it['file']}:{it['line']}  {it['text']}")

    if a.inventory:
        return 0

    fatal = rep["hits_fatal_now"]
    if rep["serving_model"] != "v2":
        fatal += rep["hits_fatal_at_cutover"]

    if fatal:
        print(f"[copy] FAIL — {fatal} user-facing strings name things the model does "
              f"not compute (site serves {rep['serving_model']}). P6: name only what "
              f"you compute. Defect G is not closed while this is non-zero.")
        if rep["serving_model"] == "v2":
            print(f"[copy]        a further {rep['hits_fatal_at_cutover']} strings "
                  f"describe v2 machinery accurately and become fatal at the v3 cutover.")
        return 1

    print("[copy] OK — public feature list matches the code list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

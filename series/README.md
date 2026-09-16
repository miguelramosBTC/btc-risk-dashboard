# `series/` — published risk tapes

This directory holds the tapes the dashboard, the API, the email engine and the
X bot are allowed to quote. Every file here is an **append-only or frozen
artifact**. Nothing in this directory is a build output.

| File | Model | Status | Rule |
|---|---|---|---|
| `v2_frozen.json` | v2 (`btc_risk_model_v2.py`) | **frozen 2026-09-11** | never regenerated, never edited |
| `v3.0.jsonl` | v3.0 (`model/v3/`) | **live, append-only** | one row per UTC day; committed rows are never rewritten |
| `v3.0_last90.json` | v3.0 | derived | free-tier window, regenerated from the tape each run |

---

## `v2_frozen.json` — freeze record

**What it is.** A byte-identical copy of `data.json` as committed by the daily
job on 2026-09-11 (`data: daily risk refresh (2026-09-11)`), i.e. the v2 tape
through **2026-09-10**. This is the series audited in
*BTC Risk Model — v2 Audit and v3 Design Roadmap* (11 Sep 2026). Table 1 of that
document reproduces from this file to the digit.

| Field | Value |
|---|---|
| Source commit | `05ede7ea6e6f2a7b2bbdae5b063eb2afe4b04ae6` (main, 2026-09-11 10:16 UTC) |
| Source path | `data.json` |
| SHA-256 | `0de9ddd6fe3f59aabd014042ca642946c725db807bdae47ec833fada1167067c` |
| Size | 170,040 bytes |
| Rows | 5,720 daily points, `2011-01-13` → `2026-09-10`, no gaps |
| Schema | `{"model": {weights, params, athExp, emaSpan, smaMayer, lastDate, lastRisk, lastATH, heldSubs}, "series": {t[], p[], r[], c[]}}` |
| Generator | `btc_risk_model_v2.py` at the same commit (11 logistic-mapped signals, EMA span 3) |
| Inputs | Coin Metrics community CSV + community API top-up (`PriceUSD, CapMVRVCur, CapMrktCurUSD, SplyCur, IssTotUSD, FeeTotNtv, FlowInExNtv`) |

Verify at any time:

```
sha256sum series/v2_frozen.json
# 0de9ddd6fe3f59aabd014042ca642946c725db807bdae47ec833fada1167067c
```

**The rule.** This file is never regenerated, recomputed, extended, reformatted
or "corrected". A recompute of v2 today would not be this tape — Coin Metrics
restates community fields, and the point of freezing is that the numbers users
saw stay the numbers users saw. If a defect in a historical row must be
documented, write a separate `v2_restatements.json`; do not touch this file.

**What it is not.** It is not the live v2 output. `btc_risk_model_v2.py` keeps
running in `.github/workflows/btc-data.yml` and keeps rewriting `data.json` /
`data.js` every morning until the v3.0 cutover; those files continue to move.
The daily job only stages `data.json` and `data.js`, so it cannot touch this
directory. Any workflow that writes here must be reviewed against this README
first.

**Why it exists.** Roadmap §13 Phase 0 and design principle P10: freeze the tape
before changing the compute, so v3 is a parallel series and not a rewrite of
history. Researchers can download it from the site alongside v3; it is the v2
reference for every "v2 said X on date Y" claim.

Derived from Coin Metrics Community Network Data (CC BY-NC 4.0).
Not financial advice.

---

## `v3.0.jsonl` — the official v3 tape

Written only by `etl/daily_v3.py`. One JSON object per line, one UTC day, in
date order. The file is opened in **append mode**; the ETL emits only dates
strictly later than the last committed `asof_date`, and `--bootstrap` refuses to
run if the file already exists.

**Why append-only is not a stylistic choice.** The maps are expanding empirical
CDFs, so recomputing 2017 today would rank it against 2018-2026 and move it. The
row committed on a given morning is what could have been published that morning;
a recompute is a diagnostic (`diagnostic_rank`) and never replaces the tape. If
Coin Metrics restates a field, publish a `v3.0_restatements.json` — do not edit a
committed line.

**Per-row provenance.** `schema_version`, `git_sha` and `input_hash` (SHA-256 of
that day's exact NEED_V3 vector) are mandatory; the writer refuses a row without
them. The growth parameters in force that morning (`a`, `b`, `fit_asof`,
`n_fit`) travel on the row, so any reader can reconstruct pillar G.

**Weights.** `active_weight` 0.90 and `retired_weight` 0.10 are stored on every
row. M was demoted on 2026-09-11 and its budget retired, not recycled; the
combiner divides by the summed weight of the families live that day.

**A version bump is a new file.** A weight change, a new family, a different map
or a changed Nmin ships as `v3.1.jsonl` with its own `schema_version`. Both
series stay queryable. Nothing may edit `v3.0.jsonl`.

**Health, not repair.** A move over 12 points, a stale field or a dark family
raises an alert and exits non-zero. It never triggers a rewrite, a refit or a
weight change.

# Bitcoin Risk

An open, daily Bitcoin risk score for dynamic DCA, published at
[bitcoinrisk.net](https://bitcoinrisk.net).

The score ranks **how extended the market is versus its own causal history**. It
is **not a forecast** — that was measured, not assumed, and the result is in
`CLAUDE.md` and `docs/CHANGELOG_v3.md`.

---

## Two models run in parallel right now

Until the v2 gauge is retired, this repository publishes **two different risk
series**, and they are **different objects on different scales**.

| | v2 | v3.0 |
|---|---|---|
| File | `data.json` / `data.js` | `series/v3.0.jsonl` (+ `series/v3.0_last90.json`, `series/v3.0_chart.json`) |
| Produced by | `btc_risk_model_v2.py` | `model/v3/` via `etl/daily_v3.py` |
| What the number is | a **blend** of mapped signals | a **causal percentile rank** |
| Median over its own history | **0.370** | **0.66** |
| Range ever printed | 0.114 – 0.927 | 0.05 – 1.00 |
| `>= 0.80` occurred on | **2.8%** of days | **29.9%** of days |
| Tape rule | frozen snapshot in `series/v2_frozen.json`; `data.json` still rewritten daily | append-only, committed rows never rewritten |

> **Never compare or quote the two interchangeably.** They share a 0–1 axis and
> nothing else. A v3 reading of 0.80 means *"more extended than 80% of history up
> to that morning"* and happens about three days in ten; a v2 reading of 0.80 was
> a twice-a-cycle event. Plotting them on one chart, diffing them, or carrying a
> threshold from one to the other produces a number that means nothing.

They do not even share the 0–1 axis any more: `series/v3.0_chart.json` publishes
`risk100`, an **integer 0–100 percentile**, while `data.js` publishes a 0–1
blend. `R_MAX` in `app.js` travels with whichever series is loaded so the axis,
the colour ramp, the tooltip and the CSV export can never assume the other
one's scale. The chart's colour stops are placed on the four published quintile
bands (0–20 / 20–60 / 60–80 / 80–100) so the legend agrees with the gauge, the
email and the bot; v2's stops were positioned against v2's distribution and
mean nothing on a ranked scale.

Every consumer states which model served it:

- the API returns `model` and `schema_version` on every response, plus a
  `fallback` object naming the reason whenever it drops to v2;
- the dashboard shows the committed row and falls back to v2 untouched when the
  v3 window is missing or more than 4 days stale;
- the email and the X bot print the model and refuse to apply v2's thresholds to
  a v3 number.

## Layout

| Path | What it is |
|---|---|
| `model/v3/` | the model: 11 modules, 113 tests, and `validate.py` (the ship gates) |
| `etl/daily_v3.py` | the **only** process allowed to write the tape |
| `series/` | published tapes — see `series/README.md` for the rules |
| `tools/check_copy.py` | P6: the site may name only what the model computes |
| `docs/` | changelog, model spec, row schema, gate report, bootstrap runbook |
| `v3.js`, `app.js`, `index.html` | the dashboard |
| `functions/`, `lib/` | the Cloudflare Pages API |
| `send-notifications.mjs`, `btc-risk-weekly.mjs` | the email engine |
| `bot/` | the daily X post |

## Running the checks

```
pip install -r requirements.txt pytest       # numpy + pandas only
python3 -m pytest model/v3/tests -q          # 113 tests
python3 -m model.v3.validate                 # ship gates; exit non-zero on fail
python3 tools/check_copy.py                  # P6; exit non-zero on a violation
python3 etl/daily_v3.py --dry-run            # compute and report, write nothing
```

`model/v3/tests` needs a Coin Metrics snapshot to exercise the model; without it
63 of 116 tests skip and the suite still reports success, so fetch it first:

```
curl -sSL -o btc.csv https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv
```

`btc.csv` is gitignored deliberately — see `CLAUDE.md`.

`scipy` and `statsmodels` are **test-only** and stay behind `importorskip`. The
process that writes the tape runs on numpy and pandas alone, and that is
load-bearing rather than stylistic.

## Before changing anything

Read **`CLAUDE.md`**. It is short, and the ten rules in it are not up for
negotiation — the tape is append-only, a version bump is a new file, and
`etl/daily_v3.py --bootstrap` is the one irreversible action in the repository.

`docs/CHANGELOG_v3.md` is the authoritative record. `docs/MODEL_v3.md` explains
the algorithm end to end, and `docs/SCHEMA_v3.0.md` documents every field on a
tape row.

---

Derived from Coin Metrics Community Network Data (CC BY-NC 4.0).
Educational; not financial advice.

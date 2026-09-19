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

They share the 0–1 **axis** and nothing else. `series/v3.0_chart.json` publishes
`risk100`, the canonical integer the tape, the API and the bot use; the chart
divides it by 100 on load, which is lossless — `risk100/100` equals the tape's
own `risk01` on all 5,307 rows exactly, so the chart and the gauge print the
identical number. `data.js` publishes a 0–1 blend of mapped signals, which is a
different quantity at the same coordinates. That is why the heading, legend,
tooltip and CSV header all change with the model rather than one label covering
both.

Two colour ramps, because they answer different questions:

- **Gauge, matrix and ranges table** use the four published quintile bands
  (0–0.20 / 0.20–0.60 / 0.60–0.80 / 0.80–1.00), so the colour agrees with the
  gauge, the email and the bot.
- **The heat map** reserves its two intense colours for the cycle extremes, at
  both ends and near-symmetrically: **deep marine below 0.12** (6.1% of days)
  and **intense red above 0.95** (7.2%), deepening to navy at 0.00 and to a
  dark red at 1.00. Everything between is stretched so the middle of the range
  actually travels, and it carries **no green and no washed-out neutral** —
  azure 0.12, teal 0.30, deep blue 0.44, dark plum 0.53, bronze 0.63, gold
  0.75, orange 0.86.

  Those two exclusions fight each other. A straight RGB line from a cool to a
  warm has only two routes: keep green high and pass *through* green, or let
  the channels converge and pass through *grey*. The ramp therefore goes round
  the other side of the wheel — blue 225° → plum 340° → bronze 24° — which is
  neither, and which **darkens** through the middle (value dips to 0.41)
  instead of brightening, so the mid-scale recedes and both extremes carry the
  eye. Verified by hue angle rather than by eye: zero samples in the green
  band, minimum saturation 0.21 (no true grey anywhere), and the share below
  saturation 0.30 down from 9.4% of the tape's days to 2.0%.
  Two earlier versions were wrong in the same way at opposite ends: v2's stops
  reddened 15.4% of history, and the first v3 cold end left **0.00–0.40 — 29.0%
  of all history, 1,540 days — as one flat marine blue**, so 0.05 and 0.38
  looked identical while meaning opposite things.

A consequence worth stating: the two most recent dollar all-time highs print
**0.90** (2024-12-17) and **0.80** (2025-10-06), so they come out orange and
amber on the heat map, not red. That is the documented order inversion — higher
high in dollars, lower high on extension and cost basis — showing up in the
picture. Reddening them would mean painting over the finding to make the chart
look like the price chart.

**A reading of 1.00 is not certainty.** Every empirical CDF is clipped to
[0.001, 0.999], and `risk01` is rounded to 2 dp, so any day whose causal rank
reaches 0.995 prints 1.00. Sixty days do, 24 of them at the hard clip. It means
"more extended than at least 99.5% of its own history to that morning", never
"the top" and never a probability.

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

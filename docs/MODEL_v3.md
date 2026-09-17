# Bitcoin Risk Model v3.0 — full specification

This document exists so that the v3 algorithm can be understood **by reading this
repository alone**, without the design roadmap and without the conversation that
produced it.

**Precedence.** Every statement below is derived from a source committed here and
is labelled with it:

| Label | Source |
|---|---|
| *(constitution)* | the Constitution table in `docs/CHANGELOG_v3.md`, locked 2026-09-11 |
| *(decision N)* | numbered entry in the `docs/CHANGELOG_v3.md` decision log |
| *(gates)* | `docs/gate_report_v3.0.txt` — the pre-bootstrap run on a 5,191-row recompute. **Stale**: the live tape is 5,307 rows to 2026-09-16 and `2026-06-30` now prints a rank, not `PENDING`. Re-run `python3 -m model.v3.validate` for current numbers. |
| *(etl)* | `etl/daily_v3.py` |
| *(rules)* | `CLAUDE.md` |
| *(inferred)* | **not stated in any committed source** — flagged inline, verify against `model/v3/` |

Where this document and `model/v3/` ever disagree, **the code and
`docs/CHANGELOG_v3.md` win and this file is the bug.** Nothing here is
normative; it is a reader's guide to the constitution.

> **The modules this document describes are not yet committed.** `model/v3/`
> (the eleven modules and 111 tests) and `series/v3.0.jsonl` were not part of the
> hand-off. See `docs/HANDOFF_STATUS_v3.md` for exactly what is present and what
> is missing. Read this file as the specification of the model, not as a
> description of code you can run today.

---

## 1. What the score is, and what it is not

`risk100` ranks **how extended the market is versus its own causal history**.
`82` means *"more extended than 82 % of history up to that morning"* — nothing
more *(rules)*.

It is **not a forecast**, and this is measured rather than asserted *(decision 24)*:

- conditional forward-outcome probabilities scored **worse than the base rate**:
  Brier 0.2812 vs climatology 0.2485, **skill −0.131** *(gates)*;
- the reliability table is **inverted** — the highest-probability bin (predicted
  0.628) had the *lowest* observed frequency (0.195) *(gates)*;
- realised P(drawdown > 30 % within 180 d) by rank band is **flat**: 0–20 → 0.346,
  20–40 → 0.255, 40–60 → 0.480, 60–80 → 0.235, 80–100 → 0.304 *(decision 24)*;
- corr(rank, forward 180-day return) = **+0.173** — in this sample extension was
  followed by *higher* returns *(decision 24)*.

So: never add implied probabilities, never describe the score as predicting
anything, never tune it against forward returns *(rules)*.

**What it is for** is dynamic DCA — buy more when extension is low. Measured
causally on 4-year windows, weight `$200 × (1 − rank)`, no selling, no
look-ahead *(decision 25)*:

| Strategy | BTC-per-dollar vs flat DCA |
|---|---|
| **v3.0 rank** | **+17.1 %** |
| MVRV percentile alone | +15.9 % |
| v2 | +9.8 % |
| pure-noise control | −0.2 % |

**Describe it as "one dominant factor plus a volatility modifier", never as
multifactor** *(decision 23, rules)*. Effective rank is **1.59 of 4**, with
**78 % of variance in one component** — Bitcoin has one cycle and every
valuation metric is a view of it. This is classified as **structural, not a
defect to be fixed with more indicators**: a candidate flow pillar that was
independent on *changes* (partials −0.001 to 0.07) still moved effective rank
only 1.59 → 1.58, because on *levels* it was another cycle oscillator (0.88 with
V) *(decision 23)*.

---

## 2. Inputs

Coin Metrics community data is the **only** required source *(constitution,
decision 1)*. Seven fields, `NEED_V3`:

```
PriceUSD, CapMVRVCur, CapMrktCurUSD, SplyCur, IssTotUSD, IssTotNtv, FeeTotNtv
```

`IssTotNtv` is **fetched and hashed but not consumed** in v3.0 — it was for the
subsidy-normalized Puell that was dropped *(P9 table)*.

Two transports, same vendor *(etl)*:

1. **Deep history** — the GitHub CSV dump
   `raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv`. It stalls;
   at the time of the hand-off it ended **2026-05-24**.
2. **Recent tail** — the community API
   `community-api.coinmetrics.io/v4/timeseries/asset-metrics`, paged, which
   carries everything after the CSV stall. **This top-up is load-bearing**
   *(decision 1)*.

Both vintages are stored on every row as `csv_vintage` and `api_vintage`, so a
restatement can be traced to the transport that carried it *(decision 22)*.

### Non-voting robustness inputs

| Input | Role |
|---|---|
| `price_alt` — CoinGecko daily close | A second, independent price. Stored, compared, and `price_alt_flag = 1` when the two differ by more than **3 %**. **Never averaged** — that would invent a third price nobody publishes. Null when unreachable, which is honest: no second opinion existed that morning *(decision 22, etl)*. |
| `R_alt` — bitcoin-data.com realized cap | A **restatement detector**, off by default (`--with-rcap-alt`). Never votes, never substitutes *(decision 28)*. |

---

## 3. The maps: empirical CDFs only

Every pillar is a percentile of its own raw series. **No logistic maps in the
official path** *(constitution)* — that was v2's machinery and is one of the
things v3 exists to remove.

```
F(x_t) = ( #{ x_s ≤ x_t , s ∈ W } − 0.5 ) / |W|      clipped to [0.001, 0.999]
```

Two windows *(constitution)*:

| Map | Window |
|---|---|
| `F_exp` | **expanding**, from t₀ = **2010-07-18** (first day with valid `PriceUSD` *and* `CapMVRVCur`) |
| `F_4y` | trailing **1,461-day** window |

**Nmin** — a pillar is inactive (dark) before its minimum sample *(constitution)*:

| Map | Nmin |
|---|---|
| expanding | **400** days |
| 4-year | **200** days |
| growth fit | **1,200** closes |

The expanding CDF is what makes the tape append-only: it is a **time-varying**
map, so a recompute today would move 2017's rank *(rules 1, decision 19)*. Within
a single day's own history it is monotone — so reach and bottom are unaffected —
but **across dates it is not** (cross-date ρ = 0.958), and cross-date statistics
do move *(decision 19)*.

---

## 4. The four voting pillars

> **Weights as shipped.** The constitution locked five pillars at V 0.31 · G 0.24 ·
> T 0.15 · **M 0.10** · Σ 0.20 = 1.00. **M was subsequently demoted**: it is
> published but does not vote, active family weight is **0.90**, and
> `retired_weight` 0.10 is disclosed on every row *(rules 6; gates show four
> families; `etl/daily_v3.py` marks `mctc_mod` "demoted: published, does not
> vote"; the ensemble's weight vectors all sum to 0.90)*. **The decision log
> committed here contains no entry recording that demotion** — see
> `docs/HANDOFF_STATUS_v3.md` §Open.

| Pillar | Weight | Raw series | Map |
|---|---|---|---|
| **V** valuation | 0.31 | `CapMVRVCur` (MVRV) | `0.65·F_exp(MVRV) + 0.35·F_4y(MVRV)` |
| **G** growth residual | 0.24 | `g_t` (below) | `0.70·F_exp(g) + 0.30·F_4y(g)` |
| **T** trend | 0.15 | `P / SMA200` (Mayer) | `F_exp(Mayer)` alone |
| **Σ** volatility regime | 0.20 | `σ30`, `fragility` | `0.40·F_exp(σ30) + 0.60·F_exp(fragility)` |
| ~~M~~ security residual | *0 (retired)* | `mctc_mod` | published on the row; does not vote |

Σ is written `S` in the tape's JSON *(etl)*.

### V — valuation

Raw is `CapMVRVCur` straight from the vendor. Blended 0.65 expanding / 0.35
four-year *(constitution)*.

### G — growth residual

```
g_t = log P_t − ( a_k + b_k · log d_t )        d = days since 2009-01-03 (genesis)
```

`a_k, b_k` come from a **Huber regression** (δ = **1.345**, the textbook default,
*not searched*) of `log P` on `log d`, fitted **only on data ≤ `fit_asof`**
*(constitution)*.

**Fixed 90-day refit-and-hold.** First mark **2013-10-30** (1,201 closes);
parameters go live the same day and are held 90 calendar days; next marks
2014-01-28, 2014-04-28, … The residual at *t* always uses the **last committed
fit**, never a same-day refit. Every row stores `a`, `b`, `fit_asof`, `n_fit`
*(constitution)*.

Quarter-ends were rejected (G would be dark at the 2013-12-04 reach test;
Bitcoin does not live on Gregorian quarters). Daily-fit-with-90-day-lag was
rejected (same 2013 hole, or a second rule for the first fit) *(decision 3)*.

At the first mark, `(a, b) = (−43.2814, 6.4097)` *(decision 30)*.

### T — trend

`F_exp(P/SMA200)` alone. **`T_dd` was deleted 2026-09-11** *(decision 9)* and the
reasoning matters, because it is the model's own failure mode caught before ship:
because `dd ≥ 0` always, `−dd = 0` is the series maximum, so `F_exp(−dd)` took
**the maximum attainable score on every new-ATH day** — 197 days, 3.7 % of the
tape — *regardless of valuation*. That is v2's `ATH**1.5` defect with a smaller
coefficient. Smoothing only postpones the clip.

**The trend budget stays 0.15 and is not recycled** *(decision 9)*. Drawdown is
charted, never mapped *(constitution)*. A future crash bonus must be a new
one-sided key with a ceiling of 0.5 at `dd = 0`, never a revival of `T_dd`.

### Σ — volatility regime, and why quiet is not safe

```
κ         = σ30 / σ365                on daily log returns
fragility = V · ( 1 − F_exp(κ) )
Σ         = 0.40·F_exp(σ30) + 0.60·F_exp(fragility)
```

*(constitution)*

`fragility` is the mechanism by which **an expensive, quiet market scores as
dangerous**: V high and κ falling drives fragility up. The `low-vol rich` ship
gate tests exactly this and measures fragility rising 0.001 → 0.849 as κ falls at
fixed V — while confirming that cheap-and-quiet stays *low* fragility, so the
bottom test is not sacrificed *(gates)*.

Σ takes V as an input by construction; the V–Σ correlation is **measured, not
assumed away** — off-gate, S–V = +0.744, S–G = +0.747, S–T = +0.497 *(gates)*.

### M — retired, still published

```
thermocap  = cumsum( IssTotUSD + FeeTotNtv · P )
mctc_mod   = log( Mcap / thermocap ) − 730-day mean (min_periods 180)
```

*(constitution)*

Last-observation-carried-forward is **bounded to 3 calendar days**, never
`fillna(0)`. Past the bound the series goes dark and `thermo_stale_days` is
stored on the row *(constitution)*. Measured: zero-fill is **3.7×–42× worse**
than bounded LOCF depending on hole length, field and era. A cumsum never fully
forgets — a hole leaves a permanent trace below 1e-3 in mapped M — so staleness
is **disclosed on the row, not erased** *(decision 11)*.

### Not in v3.0, and why

| Excluded | Reason |
|---|---|
| **Puell** | Halving artifact. The literal "×2 for 365 days" rule halves Puell overnight at H+365 (8.33→4.51 in 2013, 2.50→1.03 in 2017, 2.68→1.29 in 2021, 1.06→0.47 in 2025); the subsidy-normalized construction is continuous but **reduces to `P/SMA365`** (Pearson 0.98 on logs, its mapped CDF 0.89 with the Mayer map) — a price multiple in a miner costume *(decision 7)*. |
| **F (holder flow)** | Dark. No public realized-profit/loss series with a fallback exists. Explicitly forbidden as a stand-in: **NUPL** (= 1 − 1/MVRV, a V clone), the v2 `FlowInExNtv` ratio (the fake RHODL), supply-in-profit from a 4-year price percentile *(decision 2)*. **The UI and docs say five pillars, not six** — and four of them vote. |
| **Stablecoin liquidity (L)** | **Rejected**, and instructively: it passed diversity by the widest margin of any candidate (effective rank 1.59 → **2.30**, PC1 77.8 % → 61.5 %) but failed the DCA admission test (+17.1 % → +16.6 %) *and* broke the 2018 bottom gate (0.169 → **0.224**). Diagnosis: through 2020 the denominator is an adoption S-curve, not a liquidity condition — stablecoin supply grew **+4520 % yoy in 2017** — and L prints **0.730 at the December 2018 bottom**. Genuinely diverse and genuinely contaminated. **No weight search was run**: hunting for a weight at which L passes would be the retune this project forbids *(decision 26)*. |
| Hashrate, difficulty, active addresses, tx counts | Collinear with miner revenue, or stale signal (active addresses lost cycle signal after 2018) *(P9 table)*. |
| Google Trends, social volume, Fear & Greed | Fail all three P9 gates *(P9 table)*. |
| Funding, DXY | **reject-or-lab**, explicitly *not* backlog *(constitution)*. |

The full input-exclusion reasoning is the **P9 table** in `docs/CHANGELOG_v3.md`,
which scores each series on three gates: *manipulable* (can a third party move it
without moving BTC economic reality), *strippable* (if the vendor goes dark, is
there a second public construction within 30 days), *fad* (would it mean the same
thing in 2032).

---

## 5. Combining, smoothing, and publishing

### The blend

```
raw_t = Σ w_k Π_k  /  Σ w_k · 1{ pillar k live }
```

*(constitution)* — the denominator is over **live** pillars, so a dark family is
renormalised away rather than scored as zero.

Decision 15 defends this against a fixed 1.00 divisor: the two are an affine
rescaling of the same `Σ w_i s_i`, the relative influence of V, G and T is
identical, and a fixed divisor "buys nothing the gates measure and only breaks
external thresholds". The anti-recycle rule was *"do not pour the retired 0.10
into another family"*, **not** *"the gauge must die at 90"*. There is **no
always-on dummy family at 0.5 to paint a 95**.

### The smoothing

EMA, **span 8** → α = 2/9 ≈ 0.2222, seeded `y₀ = x₀` *(constitution)*.
`smooth01` is on every row. **No dark net in v3.0** — some interaction effects
are left on the table on purpose *(residual limits)*.

### The published score is a rank, not the blend

This is **decision 18**, the most consequential correction in the project:

```
raw01    = Σ w_k Π_k / Σ w_live          (the blend)
smooth01 = EMA_8( raw01 )
risk01   = F_exp( smooth01 )             ← the published score
risk100  = round( 100 · risk01 )
```

**Why.** A blend of four percentiles is not a percentile. The blended composite
**never exceeded 0.890 in fifteen years**, so the claim "82 means more extended
than 82 % of causal history" was *false for the number being published*, and the
reach gate only passed because `validate.py` was re-ranking it behind the scenes.

**Consequences, all measured and accepted** *(decision 18)*:

- the scale reaches its top — **2017-12-17 prints 100**;
- `≥ 80` now fires on **29.9 % of days**, against 4.5 % before — because 30 % of
  Bitcoin's days really have sat in the top quintile of their own history, and
  the old 4.5 % was an artefact of averaging percentiles;
- **policy thresholds must be read on the ranked scale: a moderate book sells at
  90–95, not 80**;
- the tape now starts **2012-03-07** (the 400-observation rank warm-up), which
  also removes every one- and two-family row.

Decision 19 then **withdraws part of decision 18's rationale** rather than
leaving it standing: the expanding rank is time-varying, not a fixed monotone
transform, so cross-date statistics *do* move — the nested-baseline point
estimate fell from **+0.0156 (blend) to −0.0562 (rank)**, below MVRV percentile
alone. The gate still passes on non-inferiority, but "moves no gate" was too
strong and is withdrawn. Accepted because the object is a classifier of present
conditions, not a forecaster, and every one of these correlations is
statistically indistinguishable from zero.

The pre-rank blend stays on every row as `raw01` and `smooth01` *(decision 18)*,
which is what makes the correction auditable.

### The band is the image of the interval

```
risk_lo / risk_hi  =  100 · [ F_exp( smooth01 − s_t ) ,  F_exp( smooth01 + s_t ) ]
```

**not** `rank ± k` *(decision 20)*. `s_t` is the sample standard deviation of the
live pillar scores.

The constitution's §7.5 text — `risk100 ± round(10·s_t)` — **cannot produce the
"±6 to ±15 points" the same paragraph predicts**: `s_t` is the stdev of numbers in
[0, 1], so `10·s_t` can never exceed ±5, and measured ±1. The implemented band
measures **median ±15, p10 ±6**, and is **asymmetric** because the map is
nonlinear. Calibration hook: the next-30-day score falls inside today's band on
**79.5 %** of days *(decision 20)*.

### Confidence

`conf` is 0–10 per spec §7.5 (the roadmap is not committed here). One
implementation correction is recorded: with **one** live family the sample
standard deviation of family scores is **undefined, not zero** — treating it as
zero read as perfect agreement and printed conf 6 on the lone-V rows of 2011.
Undefined agreement now contributes nothing, so a one-family day is carried by
completeness alone and prints conf 1–2. Rows with all four families live are
unchanged and no gate moved *(decision 17)*.

---

## 6. The three disclosure layers

v3 publishes three things *beside* the score that most risk dashboards do not
publish at all. None of them can change the score.

### 6.1 Specification ensemble — `ens_lo` / `ens_hi` *(decision 29)*

The entire causal tape is recomputed under a **pre-registered grid of 33 equally
plausible constitutions**, and the spread is published. Two bands now sit side by
side answering different questions:

- `risk_lo / risk_hi` — how much the **families** disagree today;
- `ens_lo / ens_hi` — how much the answer **depends on choices nobody tested**.

**The grid**, fixed before any output was inspected: EMA span 5/8/13 · Nmin_exp
300/400/500 · Nmin_4y 150/200/300 · 4-year window 1095/1461/1827 · V blend
.50/.65/.80 · G blend .55/.70/.85 · Σ blend .25/.40/.55 · Huber δ 1.0/1.345/2.0 ·
refit cadence 90/180 · four weight vectors (incumbent, equal, V-heavy, Σ-heavy).
One-at-a-time members **plus 12 deterministic multi-axis members** (seed
20260914), because sensitivities interact and OAT alone understates the band.

**Three rules that stop it becoming a tuning device:** no member is ever
selected, and no member is inspected for performance; the grid is pre-registered,
so adding a level after seeing results — or dropping one that widens the band —
is a retune, *and a dishonest one, because it narrows published uncertainty
without changing the model*; every member is a full causal recomputation, not a
perturbation of the incumbent's output.

**Measured:** band width **median 9.7 points**, p90 18.9, max 53.4. The incumbent
sits at the **52nd percentile of its own grid** — so the constitution is not an
outlier of the alternatives it is judged against. ~10 s for 33 members; runs in
the ETL, never in the browser.

**The headline result** *(and it supersedes the "+0.0035 margin" framing)*: at
**2025-10-06 only 52 % of plausible constitutions put the day in the top
quintile** (incumbent 0.803, range 0.696–0.830). The older highs are unanimous —
2013, 2017, 2021-04, 2021-11 and 2024 all at **100 %**. The 2018 bottom is the
weakest low at **88 %**. So the honest statement about the 2025 ATH is not
"passes by +0.0035" but **"roughly half of equally defensible models call it
top-quintile, and half do not"**.

**Most influential single choices** (median |Δ| vs incumbent): Nmin_exp 300 →
1.38 pts, equal weights → 1.34, Σ-heavy → 1.27, Σ blend → 1.12, 4-year window
1095 → 1.05. **No single knob moves the daily number by more than ~1.4 points on
the median day; the width comes from their interaction.**

`validate.py` prints the whole table every run and **does not fail on it**: a
threshold on "share of members agreeing" would have to be invented after seeing
the numbers.

### 6.2 Growth-specification risk — `g_spec_lo` / `g_spec_hi` *(decision 30)*

The specification ensemble varies G's *parameters* but never its *functional
form*, so it was silent about exactly the limitation the residual-limits section
names: *a power law in days-since-genesis is a model.* So G is refitted under
five pre-registered specifications, all causal, all on the same 90-day
refit-and-hold cadence:

| Spec | Form | Note |
|---|---|---|
| **A** | `log P ~ a + b log d`, Huber | **official** |
| **B** | same design, median (L1) regression | the other estimator §7.2 permits |
| **C** | `log P ~ a + b·d`, constant exponential growth | the competing structural story |
| **D** | A on the trailing **8 years** only | does the distant past still deserve to set the slope |
| **E** | continuous hinge at **2024-01-10** (US spot-ETF approval) | the ETF-era slope change; **dark until 400 post-knot days exist** |

**Measured:** spread **median 8.6 pts, p90 16.2, max 63.9**. Correlation with A:
B **0.989**, D 0.988, C 0.980, **E 0.573**. Changing the *estimator* barely
matters; **changing the *shape* matters more** — and E, the ETF hypothesis, is
the one that departs.

**Where it bites is recent, as predicted.** At 2017-12-17 the specifications
differ by **0.8 pts**. At 2025-10-06 by **18.1** (A 0.710, D 0.799, E 0.684,
C 0.619). At 2026-05-23 by **23.6** (A 0.303, D 0.395, E 0.159, C 0.180). *The
trend specification is close to irrelevant for the old cycles and materially
uncertain for the one we are living in.*

**Caveat on E:** with ~250 live days its expanding map has not reached Nmin 400,
so its blend runs on the 4-year component alone. It is a thinner object than the
others and should be read as such until ~2027.

**Disclosure, never a switch.** The official G stays specification A. Switching
to whichever trend currently flatters the score is the purest form of retune;
persistent divergence is evidence for a **version review**, decided deliberately
and shipped as its own tape. **A test asserts that no production module imports
`growth_specs`**, so G cannot be switched by accident.

`huber_fit` was generalised to `huber_fit_design` (arbitrary design matrix, Huber
or L1) and the production fit is **bit-identical**: (a, b) at the first mark is
still (−43.2814, 6.4097).

### 6.3 Outcome layer — built, measured, **ships dark** *(decision 24)*

`model/v3/outcome.py` estimates, causally, the distribution of forward outcomes
on the historical days most similar to today — state = composite rank × vol-compression
rank, k = 250 nearest, horizon 180 days. **Double causality:** an analogue day may
only be used at *t* once its own forward window has closed, so the layer sees
strictly less than the score does.

**Activation was pre-committed before the first measurement**: publish only if the
conditional probabilities beat the causal base rate (`MIN_SKILL = 0.02`,
effective n ≥ 20). **They do not** — skill −0.131; by era, 2013-17 −0.216 and
2018-26 −0.086; interval coverage 68 % against a nominal 80 %.

The effective sample is **~24 independent windows**, so this does not establish
that no such relationship exists — it establishes that **we cannot support one,
which is the same thing for publication purposes**.

**Product consequence:** publish the unconditional **climatology** — the causal
base rate of a >30 % drawdown within 180 days, conditioned on *nothing*,
calibrated by construction — as `clim_p_dd30_180` and `clim_ret_p50_180`. **Never
the conditional probability.** A dark layer does not fail the build, because dark
is a legitimate documented state. **Forbidden: searching outcome definitions,
horizons or thresholds until one passes.**

### 6.4 Second realized cap — a detector, not a fallback *(decision 28)*

`model/v3/rcap_alt.py`, off by default (`--with-rcap-alt`).

**Construction.** Compare **realized caps, not MVRV ratios**. A second vendor's
MVRV brings its own price and supply, so a 3 % gap could be methodology *or* a
different close. `MVRV_alt = CapMrktCurUSD / R_alt` reuses the Coin Metrics
market cap the rest of the tape already trusts, so **any gap is realized-cap
methodology by construction**.

**It adds no precision, by design.** Neither `R_alt` nor any rebuild votes in V,
and the published score does not change. What it adds is **detection**:
`input_hash` catches *"the inputs changed"* but cannot say which side is right;
the gap can. A CM outage still costs pillar V for that day. **A vendor swap
mid-version is forbidden**; it would be v3.1 with its own tape.

**Three statistics, each for a different failure.** Rank co-movement and dlog-R
correlation catch jumps and noise. The **365-day drift of the log gap** catches a
creeping restatement, which the other two cannot: a smooth 60 % divergence over
200 days leaves dlog-R correlation at **0.999** and rank correlation at **0.98** —
*both still look healthy on a dashboard*. A constant methodology gap has zero
drift. **This was found by a test failing, not by design.**

**No alerts until a band exists.** `GAP_BAND_LOG = None` means *publish the gap,
alert on nothing*: the bridge source paywalls the last 7 days and caps free
history at four years, so the normal gap cannot be established for free, and **a
guessed threshold is worse than none**.

**P9 gate 2 is NOT satisfied.** A vendor API is a second phone number, not a
second method. The gate is satisfied only by a realized cap rebuilt from an
indexed UTXO set (`R_self`), which needs a full node. Named as the target,
**recorded as absent, not claimed** *(rules, "Known-open, deliberately")*.

---

## 7. Ship gates

`model/v3/validate.py` exits non-zero on any failure. The full run on the
5,191-row tape is committed verbatim as **`docs/gate_report_v3.0.txt`**;
10/10 pass. Summary:

| Gate | Rule | Result |
|---|---|---|
| **Reach** | every cycle high in the top quintile of its own causal history (≥ 0.800) | PASS — 2013-12-04 0.9407 · 2017-12-17 0.9990 · 2021-04-14 0.9797 · 2021-11-10 0.9180 · 2024-03-13 0.8569 · **2025-10-06 0.8035 (margin +0.0035)** |
| **Order** | 2025-10-06 not below 2024-03-13, or a documented residual | PASS as a **documented inversion** — see below |
| **Bottom** | bear lows in the bottom quintile (≤ 0.200) | PASS — 2015-01-14 0.0835 · 2018-12-15 0.1682 · 2022-11-21 0.0544 |
| **Low-vol rich** | quiet is not safe | PASS — fragility 0.001 → 0.849 as κ falls at fixed V; cheap+quiet stays low |
| **Collinearity** | partial `\|r(Δsᵢ, Δsⱼ \| Δlog P)\| < 0.70` on V/G/T | PASS — max 0.526 (V–T). Levels **disclosed**: V–G 0.846 |
| **Nested baseline** | non-inferior to Mayer and MVRV percentile (90 d) | PASS — v3 −0.0562, Mayer −0.1259, MVRV −0.0348; CIs straddle zero |
| **Rewrite probe** | two computations produce an identical tape | PASS — byte-stable |
| **Outcome layer** | conditional probabilities must beat climatology | PASS *as an activation decision* — skill −0.131 → **DARK** |
| **Specification ensemble** | model uncertainty recorded | PASS (reporting) — 33 members, median 9.7 pts |
| **Growth specification** | functional-form risk recorded | PASS (reporting) — median 8.6 pts |

**Gates read `rank_exact`, never the integer** *(decision 21)* — rounding 0.797 up
to 80 and then calling the day top-quintile is rounding in our own favour. Reach
and bottom print **signed margins**.

### The 2025 order inversion, documented rather than engineered away

2025-10-06 (0.8035) prints **below** 2024-03-13 (0.8569). Registered in
`validate.DOCUMENTED_INVERSIONS`; an **undocumented** inversion still fails the
gate *(decision 12)*.

The higher high in dollars is the **lower high on extension and cost basis**:
Mayer 1.18 vs 1.85, MVRV 2.29 vs 2.75, κ 0.62 vs 1.33. G +0.047 and Σ +0.233 vote
2025; V −0.076 and T −0.306 vote 2024.

Forcing 2025 above 2024 would be the ATH sleeve under a new name — and there is
proof: **`T_dd` printed 0.999 on *both* dates and still left a −0.168 gap.** A
mechanical high-at-highs term cannot manufacture an order the valuation tape does
not support. **No weight change, no version bump.**

### Why the collinearity gate is a partial correlation

V, G and T are monotone maps of a price-like state, so `Δsᵢ ≈ βᵢ·Δlog P + uᵢ` and
`r(ΔV, ΔT) = 0.883` is mostly `r(Δlog P, Δlog P)`. Weekly (0.901) and quarterly
(0.902) figures are *worse* for the same reason: differencing discards the
day-noise and keeps the shared bull/bear tick, isolating the common driver the
families are **allowed** to share. **It does not reveal aliases** *(decision 13)*.

So the gate is three statistics, printed every run, on the Mode A common live
span, pairs in {V, G, T} only:

| Statistic | Role | Fails if |
|---|---|---|
| `r(Δsᵢ, Δsⱼ \| Δlog P)` | **ship blocker** | ≥ 0.70 |
| `r(sᵢ, sⱼ)` levels | disclose only | never |
| `r(Δsᵢ, Δsⱼ)` raw | price-elasticity diagnostic | never |

Σ pairs are **off the gate** — a family is supposed to correlate with the
composite. G and T are **not** demoted, not orthogonalised onto V, and **no
weight is cut to paint a cell**. The control is the same BTC log price that feeds
T, first-differenced and aligned to the score date; swapping in a "smarter"
control (Mayer, or V itself) is a new gate and a new version.

### Why the nested baseline is non-inferiority

Pass rule: the block-bootstrap CI for `s(v3) − s(baseline)` must **not lie
entirely below zero**. **Inconclusive is not a failure** — failing a model on a
statistic that cannot rank the candidates is a gate bug *(decision 14)*.

The 200-week SMA distance (+0.0716) is a **reported comparator, never a
blocker**, and the gate report says so in the output itself: *"Do not insert the
200w SMA into V to win a point estimate."*

### The holdout — complete since 2026-09-17, still design-contaminated

*(decision 16; superseded in part by decisions 33 and 34)*

- Window **2025-09-11 → 2026-09-10**, labelled **"holdout / design-contaminated"**,
  **not "unseen"** — expanding CDFs and pillar Σ were both motivated by v2 dying
  at the 2025 ATH, and a one-year embargo cannot un-know that. **Real
  out-of-sample begins at the next ATH after the tape is frozen** *(rules)*.
  That label has not changed and is not softened by the result below.
- The tape now runs to **2026-09-16 (5,307 rows)**, so the window is complete
  (n = 365) and the `INCOMPLETE` line is gone.
- **2026-06-30** — the first v3 out-of-sample bottom — **landed in the bottom
  quintile: causal rank 0.104.** The holdout trough, 2026-07-01, prints 0.104 on
  the same footing. It was a **named check, never a gate and never a retune
  date**, and it stays that way: the model was not adjusted to produce it.
- It printed `PENDING` for longer than it should have. `validate.py` was scoring
  a CSV recompute that stopped at 2026-05-23 rather than the committed tape, so
  the report kept saying `PENDING` after the low had already landed *(decision
  34)*. Fixed; tape-based gates now read `series/v3.0.jsonl`.

**What this does and does not license.** "Cheap at lows" survived its first
out-of-sample test, on one observation, inside a window the changelog still
calls design-contaminated. It is one bottom, not a distribution. **No sentence
of the form "validated through 2026" is licensed by it**, and no Coinbase spot
may be spliced into V or G.

---

## 8. The tape

`series/v3.0.jsonl` — one row per UTC day, **append-only**, committed rows
byte-stable. The full field list is documented in **`docs/SCHEMA_v3.0.md`**.

`etl/daily_v3.py` is **the only process allowed to write it**, and enforces four
guarantees in order *(etl)*:

1. **No future data.** The *raw* frame is asserted first — `prepare_frame` drops
   rows with no price, so a future-dated row with a missing field would vanish
   silently instead of being reported, and a vendor that starts publishing
   tomorrow's date is exactly the event the assertion exists to catch. Asserted
   again after preparation and again on the computed tape.
2. **Append-only.** The writer opens the file in **append mode, never `"w"`**, and
   only emits dates strictly later than the last committed `asof_date`. The
   workflow additionally refuses to push if `git diff --numstat` shows any line
   was **removed** from `v3.0.jsonl`.
3. **Reproducibility per row.** `input_hash` (SHA-256 of that day's exact input
   vector, formatted at fixed precision so the hash does not move with a numpy
   version), `git_sha`, `schema_version`, and `a`/`b`/`fit_asof`/`n_fit`.
   `_row_to_json` **raises** if provenance is missing: *"a published row must be
   reconstructible."*
4. **Health, not silence.** A jump, a stale feed or a dark family is **reported
   and never repaired**.

### Health checks

| Alert | Trigger |
|---|---|
| jump | `\|Δrisk100\| > 12` — *"possible data hole, not a signal"* |
| stale | any required field older than **36 h** (`input_stale_days`) |
| dark family | `n_live < 4` |
| **page the maintainer** | a required field missing **≥ 7 days** |
| price divergence | `price_alt_flag` — sources differ > 3 % |
| lag | tape more than 2 days behind today |
| structural | `asof_date` non-monotonic, or duplicated |

`stale` is driven by the age of **any** `NEED_V3` field — previously an MVRV
outage darkened V, renormalised the blend and published `stale = 0`
*(decision 22)*.

**The health check runs AFTER the commit, on purpose** *(etl, workflow)*: in CI a
non-zero exit aborts the job before the commit step, so a stale-data day would
compute a row and then throw it away. `--strict-health` is **off by default** for
exactly this reason. Committing the row matters more than surfacing the alert in
that step.

### `map_support` — and defect F

The free window ships a **201-point quantile grid** of each raw series so the
browser can invert the V, G and T maps for the risk-price matrix ("what price
would read risk X today?"). The maps are expanding CDFs over the whole history
and the free window carries only 90 days, so a grid is shipped instead of the
data *(etl)*.

Two traps recorded in the code:

- The grid ships **both** the expanding and the trailing-4-year quantiles,
  because V and G are **blends** of the two maps. Shipping only the expanding
  grid made the browser's pillar a different object from the committed one —
  **measured at 1.6e-2 on G before it was fixed**.
- It also ships a `composite` grid, because the published score is the **rank** of
  the smoothed blend. Without it the browser would return blend-space numbers
  while the gauge shows ranks — *"three-current-risk-objects again, in a new
  costume."*

**The official score is never recomputed in the browser: it is read from the
committed row.** This is **defect F**, the thing the project exists to kill, and
it is also the one **known gap in the staged Phase 5 frontend**: `v3.js` inverts
into *blend* space while the published score is a *rank*, so the matrix widget
must map through the `composite` quantile grid before cutover
*(`docs/BOOTSTRAP_RUNBOOK.md` §6)*.

---

## 9. Versioning discipline

**A version bump is a new file** *(rules 2)*. Any change to weights, a family, a
map, Nmin, the EMA span or the blend ratios ships as `v3.1.jsonl` with its own
`schema_version`. **It never edits `v3.0.jsonl`.**

Requires a bump, never inside v3.0 *(constitution)*:

- any weight, the pillar list, the V/G blend ratios, the fragility formula, the
  CDF continuity correction, Nmin, EMA span, Huber δ;
- refitting G because price moved; skipping a scheduled refit; moving the refit
  clock to halvings or calendar quarters; OLS instead of Huber;
- **moving the sell thresholds of a reference book to "catch" a score that will
  not print — that is a score bug, so it is a score version**;
- reintroducing Puell, MVRV-Z, NUPL, supply-in-profit, an exchange-flow "RHODL",
  or any second valuation alias.

**Not** a bump, and not logged: scheduled adaptation *inside* a version — the
expanding CDFs lengthening, and the 90-day growth refit *(changelog header)*.

**Never retune to pass** *(rules 3)*. If a gate fails, fix the pillar or change
the version. Do not widen Nmin, shrink a weight, drop a date from the reach list,
search for a weight at which a candidate passes, or move a policy threshold to
catch a score that will not print.

---

## 10. Residual limitations

Maintained, not decorative *(constitution, residual limits)*. v3 **cannot** claim:

- Three and a half cycles is a distribution. Under ETFs, fiscal dominance or a
  ban, "rich vs history" may stop describing subsequent returns.
- That `risk100 = 82` means an 82 % chance of anything. **No implied
  probabilities** without a separately calibrated layer with its own Brier score.
- That the frozen tape plus a restatement file *cures* on-chain latency and
  restatement. It is a mitigation.
- That it has picked the true trend. A power law in days-since-genesis is a
  model; a broken log-linear with an ETF-era slope change would mis-rank the next
  ATH. Huber and the 90-day hold reduce drama; they do not pick the trend. G at
  2013-12-04 is an early-sample fit (1,201 closes, one prior cycle).
- Any leverage / liquidity / options surface. Fragility from compressed spot vol
  is a **proxy**; an options-led flush can still surprise Σ.
- Multifactor structure — see §1.
- That Σ is independent of V. Σ takes V as an input by construction.
- Anything from dynamic-DCA path dependence. Books beat or lose to flat DCA
  depending on the path; **that is not evidence about the score**.
- That divergence from a closed competitor is a v3 bug.
- Performance over transparency. There is no dark net in v3.0, and some
  interaction effects are left on the table on purpose.
- That the 2025–26 holdout is unseen. It is design-contaminated.

Plus the two carried openly in `CLAUDE.md`:

- **P9 gate 2 unsatisfied** — `CapMVRVCur` has no second construction.
- **2025-10-06 is a knife-edge** — +0.0035, and only 52 % of equally defensible
  constitutions agree.

---

## 11. P6 — name only what you compute

`tools/check_copy.py` enforces it and **currently fails** *(rules 5, decision 31)*.
Two severity classes with different deadlines:

- **Defect G — fatal now: 39 strings** name hashrate, difficulty, active
  addresses, long/short-term holder positioning and implied crash probabilities.
  **No version of this model has ever computed these, v2 included.** The earlier
  audit filed defect G as "documentation vs code, resolve later"; it is a live
  violation today.
- **Fatal at cutover: 92 strings** describing v2 machinery — eleven signals,
  logistic maps, Puell, MVRV-Z, RHODL, terminal price, supply-in-profit. Those
  are *accurate while v2 is the live model*, so the check keys off **which tape
  the page actually reads**, detected from the page rather than a hand-maintained
  flag.

It audits only what a **visitor** can read — HTML text nodes and i18n dictionary
values, with code comments and docstrings **stripped first**, because *every
grep-shaped check in this project has at some point matched its own explanatory
prose*. A test asserts that property directly.

**It gates a deploy, never a row.** Deliberately separate from
`btc-data-v3.yml`: a copy defect must not stop an append-only series from
recording what the model saw that morning.

**No waiver list. The fix is to change the sentences.** A test asserts the live
site currently fails, so the check cannot be weakened instead of the copy being
fixed.

---

## 12. Reading order

| # | File | What it gives you |
|---|---|---|
| 1 | `CLAUDE.md` | the ten non-negotiable rules, and the one irreversible action |
| 2 | **this file** | the algorithm, end to end |
| 3 | `docs/SCHEMA_v3.0.md` | every field on a tape row |
| 4 | `docs/CHANGELOG_v3.md` | the authoritative record — constitution, 33 numbered decisions, P9 table, residual limits |
| 5 | `docs/gate_report_v3.0.txt` | the actual measured output of all ten gates |
| 6 | `docs/BOOTSTRAP_RUNBOOK.md` | the one-time sequence only the maintainer may run |
| 7 | `docs/HANDOFF_STATUS_v3.md` | what is committed, what is missing, what cannot run yet |
| 8 | `etl/daily_v3.py` | the only writer of the tape |

`docs/CHANGELOG_v3.md` is authoritative. This file is the short version of the
*model*; `CLAUDE.md` is the short version of the *rules*.

# `series/v3.0.jsonl` — row schema

One JSON object per line, one line per UTC day, **append-only**. Written only by
`etl/daily_v3.py`; committed rows are never rewritten, re-ordered or
re-serialised.

This document is derived from `_row_to_json()` in `etl/daily_v3.py`, which is the
authoritative definition — it is the function that emits the line. Where a field's
*meaning* comes from elsewhere, the source is labelled as in `docs/MODEL_v3.md`
(*(constitution)*, *(decision N)*, *(gates)*, *(inferred)*).

Serialisation: `json.dumps(row, separators=(",", ":"))` — no spaces, key order as
listed below, one line, newline-terminated.

**`null` is meaningful.** It means *"this quantity did not exist for this row"* —
a pillar was dark, a source was unreachable, a diagnostic was not requested. It
never means zero, and it is never backfilled.

---

## Provenance — the row must be reconstructible

`_row_to_json` **raises** rather than emitting a row missing any of these:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | The constitution this row was computed under. A change to any constitution item is a new `schema_version` **and a new tape file** *(rules 2)*. |
| `git_sha` | string | `GITHUB_SHA` of the Action that wrote the row, or `"local"`. |
| `input_hash` | string | SHA-256 of that day's exact input vector: `"PriceUSD=…\|CapMVRVCur=…\|…"` over `NEED_V3` in order, each value formatted `%.10g` (or `NA`). Fixed precision on purpose — float repr differs across numpy versions, and **a hash that changes when the runtime changes is worthless**. |

Detecting a restatement: the same `asof_date` recomputed later with a different
`input_hash` means the vendor changed history. `input_hash` catches *that the
inputs changed*; it cannot say which side is right — that is what `mvrv_gap` is
for *(decision 28)*.

---

## The published score

| Field | Type | Round | Meaning |
|---|---|---|---|
| `asof_date` | string | — | `YYYY-MM-DD`, UTC. Strictly increasing across the file; duplicates are a health alert. |
| `price_usd` | float | 2 | Coin Metrics `PriceUSD`. |
| `risk100` | int | — | **The published score**, 0–100. `round(100 · risk01)`. |
| `risk01` | float | 4 | `F_exp(smooth01)` — the **causal rank** of the smoothed blend *(decision 18)*. The dashboard keeps the 0–1 scale for continuity *(decision 5)*. |
| `rank_exact` | float | 6 | The unrounded rank. **Gates read this, never the integer** — rounding 0.797 up to 80 and then calling the day top-quintile is rounding in our own favour *(decision 21)*. |
| `risk_lo` | int | — | Band low: `100 · F_exp(smooth01 − spread)` *(decision 20)*. |
| `risk_hi` | int | — | Band high: `100 · F_exp(smooth01 + spread)`. **Asymmetric about `risk100`**, because the map is nonlinear. Measured median ±15, p10 ±6. |
| `conf` | int | — | Confidence 0–10. Completeness plus family agreement; **undefined agreement contributes nothing**, so a one-family day prints 1–2 rather than 6 *(decision 17)*. |

> **`risk100 ≥ 80` fires on 29.9 % of days**, not 4.5 % — because the score is now
> a rank of history rather than an average of percentiles. **Policy thresholds
> must be read on the ranked scale: a moderate book sells at 90–95, not 80**
> *(decision 18)*.

### Pre-rank intermediates

Kept on every row so the decision-18 correction stays auditable.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `raw01` | float | 6 | The blend: `Σ wₖ Πₖ / Σ w_live`. **Never exceeded 0.890 in fifteen years** — which is why publishing it as a percentile was false *(decision 18)*. |
| `smooth01` | float | 6 | `EMA(raw01)`, span 8, α = 2/9, seeded `y₀ = x₀` *(constitution)*. |
| `spread` | float | 6 | The family-disagreement statistic `s_t` — sample standard deviation of the live pillar scores. The band is its image under `F_exp` *(decision 20)*. *(inferred: this is the only per-row scalar the band can be built from; confirm the exact definition against `model/v3/compute.py`.)* |

---

## Pillar scores

Mapped to [0.001, 0.999]. `null` when the pillar is dark (below its Nmin, or its
input is stale past the bound).

| Field | Weight | Construction |
|---|---|---|
| `V` | 0.31 | `0.65·F_exp(mvrv) + 0.35·F_4y(mvrv)` |
| `G` | 0.24 | `0.70·F_exp(g_raw) + 0.30·F_4y(g_raw)` |
| `T` | 0.15 | `F_exp(mayer)` |
| `S` | 0.20 | `0.40·F_exp(sigma30) + 0.60·F_exp(fragility)` — this is Σ |

Active family weight **0.90**; the retired 0.10 stays out of every family weight
table *(rules 6, decision 15)*.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `n_live` | int | — | Number of live voting families. **`< 4` is a health alert** — a family is dark. |
| `active_weight` | float | 4 | Σ of weights that voted on this row. Disclosed on every row *(rules 6)*. |
| `retired_weight` | float | 4 | 0.10 — M's retired budget. **Retired weight is retired**, not recycled. |
| `stale` | int | — | 1 when **any** `NEED_V3` field is older than 36 h. Previously only thermocap's age drove this, so an MVRV outage darkened V, renormalised the blend and still published `stale = 0` *(decision 22)*. |
| `input_stale_days` | int | — | Age in days of the oldest required field. **≥ 7 pages the maintainer** *(decision 22)*. |

---

## Raw series behind the pillars

Published so a reader can see the inputs, not just the percentiles — and so the
browser can invert the maps for the risk-price matrix.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `mvrv` | float | 6 | `CapMVRVCur`. Raw behind **V**. |
| `mayer` | float | 6 | `P / SMA200`. Raw behind **T**. |
| `g_raw` | float | 6 | `log P_t − (a + b·log d_t)`, `d` = days since genesis (2009-01-03). Raw behind **G**. |
| `sigma30` | float | 8 | 30-day realised vol of daily log returns. |
| `sigma365` | float | 8 | 365-day realised vol. |
| `kappa` | float | 6 | `σ30 / σ365` — volatility compression. |
| `fragility` | float | 6 | `V · (1 − F_exp(κ))`. **The mechanism by which an expensive, quiet market scores as dangerous.** |
| `mctc_mod` | float | 6 | `log(Mcap/thermocap) − 730-day mean (min_periods 180)`. **Demoted: published, does not vote** *(etl)*. |

### Growth-fit parameters in force that morning

| Field | Type | Round | Meaning |
|---|---|---|---|
| `a` | float | 6 | Huber intercept. At the first mark: **−43.2814**. |
| `b` | float | 6 | Huber slope on `log d`. At the first mark: **6.4097**. |
| `fit_asof` | string | — | `YYYY-MM-DD` — the last date the fit was allowed to see. **The residual at *t* always uses the last committed fit, never a same-day refit** *(constitution)*. |
| `n_fit` | int | — | Closes in that fit. First mark 2013-10-30: **1,201**. |

Fixed 90-day refit-and-hold: 2013-10-30, 2014-01-28, 2014-04-28, … Refitting G
because price moved, or skipping a scheduled refit, **requires a version bump**
*(constitution)*.

---

## Disclosure layers

None of these can change `risk100`.

### Model uncertainty — the specification ensemble *(decision 29)*

**Scale gotcha:** `ens_lo`/`ens_hi` are integers on the **0–100** scale;
`ens_p10`/`ens_p90` are floats on the **0–1** scale *(etl)*.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `ens_lo` | int | — | `round(100 · min)` across ensemble members. |
| `ens_hi` | int | — | `round(100 · max)`. |
| `ens_p10` | float | 4 | 10th percentile across members, 0–1. |
| `ens_p90` | float | 4 | 90th percentile, 0–1. |
| `ens_n` | int | — | Members that produced a value — **33** on a full row. |

`risk_lo/risk_hi` = how much the **families** disagree today. `ens_lo/ens_hi` =
how much the answer **depends on choices nobody tested**. Band width median 9.7
points, p90 18.9, max 53.4.

### Growth-specification risk *(decision 30)*

0–1 scale. The official G is **always specification A**; the spread is
disclosure, **never a switch**.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `g_spec_lo` | float | 4 | Min mapped G across the five pre-registered trend specifications. |
| `g_spec_hi` | float | 4 | Max. |
| `g_spec_n` | int | — | Specifications live that day — 4 before 2025-09-16, 5 after (E is dark until 400 post-knot days exist). |

Spread median 8.6 pts, but **0.8 pts at 2017-12-17 against 23.6 at 2026-05-23**:
the trend specification is nearly irrelevant for the old cycles and materially
uncertain for the current one.

### Climatology — what is published *instead of* a probability *(decision 24)*

The conditional outcome layer was built, measured at **skill −0.131**, and
**ships dark**. What the row carries is the base rate, conditioned on **nothing**,
calibrated by construction.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `clim_p_dd30_180` | float | 4 | Causal unconditional P(drawdown > 30 % within 180 d). |
| `clim_ret_p50_180` | float | 4 | Causal unconditional median 180-day forward return. |

**These are not conditioned on `risk100` and must never be presented as if they
were.** Realised P(dd>30 %) by rank band is flat (0.346 / 0.255 / 0.480 / 0.235 /
0.304), and the conditional layer's reliability table was *inverted*.

### Restatement detector — realized cap *(decision 28)*

`null` unless `etl/daily_v3.py --with-rcap-alt` ran. **Never votes, never
substitutes.** Unverified from the build sandbox — must be exercised once in
Actions before anything depends on it.

| Field | Type | Round | Meaning |
|---|---|---|---|
| `R_alt` | float | 2 | Second-source realized cap. |
| `MVRV_alt` | float | 6 | `CapMrktCurUSD / R_alt` — **reuses the Coin Metrics market cap on purpose**, so any gap is realized-cap *methodology*, not a different close. |
| `mvrv_gap` | float | 6 | `CapMVRVCur / MVRV_alt − 1`. **Published; alerts on nothing** — `GAP_BAND_LOG = None`, because a guessed threshold is worse than none. |
| `rcap_alt_hash` | string | — | Provenance of the alternative construction, beside `input_hash`. |

The statistic that matters is the **365-day drift of the log gap**: a smooth 60 %
divergence over 200 days leaves dlog-R correlation at 0.999 and rank correlation
at 0.98 — both still look healthy on a dashboard.

---

## Source provenance

| Field | Type | Round | Meaning |
|---|---|---|---|
| `csv_vintage` | string | — | Last date the GitHub CSV dump carried. |
| `api_vintage` | string | — | Last date the community API top-up carried. **These differ on a healthy row**; if `api_vintage` is null the top-up failed and only the lagging CSV was read. |
| `price_alt` | float | 2 | A second, independent daily close (CoinGecko). **Last row only** — that is the only day a live quote exists for; older rows carry null rather than a backfill. |
| `price_alt_flag` | int | — | 1 when the two prices differ by more than **3 %**. **The two are never averaged** — that would invent a third price nobody publishes. Defaults to 0, including when the source is unreachable. |

---

## Field order

`_row_to_json` emits keys in this order (53 fields):

```
asof_date, price_usd, risk100, risk01, risk_lo, risk_hi, conf, n_live, stale,
spread, V, G, T, S, mvrv, mayer, g_raw, fragility, sigma30, sigma365, kappa,
mctc_mod, raw01, smooth01, rank_exact, input_stale_days, price_alt,
price_alt_flag, clim_p_dd30_180, clim_ret_p50_180, g_spec_lo, g_spec_hi,
g_spec_n, ens_lo, ens_hi, ens_p10, ens_p90, ens_n, R_alt, MVRV_alt, mvrv_gap,
rcap_alt_hash, csv_vintage, api_vintage, a, b, fit_asof, n_fit, active_weight,
retired_weight, schema_version, git_sha, input_hash
```

Only `risk100`, `risk01`, `risk_lo`, `risk_hi`, `conf`, `n_live`, `stale`,
`spread`, `V`, `G`, `T`, `S`, `price_usd`, `active_weight`, `retired_weight` and
the three provenance fields are required. Everything else goes through `opt()`,
so **a v3.1 frame need not carry every diagnostic** *(etl)*.

---

## Deviations from the constitution's Row line

The constitution *(`docs/CHANGELOG_v3.md`, Row)* specifies:

```
asof_date, price_usd, risk100, risk01, risk_lo, risk_hi, conf, n_live, stale,
{V,G,T,M,Σ}_raw, {V,G,T,M,Σ}, a, b, fit_asof, n_fit, schema_version, git_sha,
input_hash
```

The shipped row differs in four ways. Three are documented later decisions; the
fourth is unexplained in the material committed here.

1. **No `M`, and no `{…}_raw` naming.** M was demoted; the raw series are carried
   under their own names (`mvrv`, `g_raw`, `mayer`, `mctc_mod`, `fragility`,
   `sigma30`, `sigma365`, `kappa`) rather than as `{X}_raw`. Σ is `S`.
2. **`risk01` is no longer `risk100/100`** — it is `F_exp(smooth01)`
   *(decision 18)*.
3. **`risk_lo/hi` is no longer `risk100 ± round(10·s_t)`** — it is the image of
   the disagreement interval *(decision 20)*.
4. **`thermo_stale_days` is computed but not emitted.** `build_tape` joins it
   into `extras`, and the constitution says it "is stored on the row", but
   `_row_to_json` writes `input_stale_days` only. Either the constitution's
   sentence is stale or the emitter drops a field — **resolve against
   `model/v3/compute.py` before the bootstrap**, because the tape is append-only
   and a field omitted on day one can never be added to the committed rows.

---

## `series/v3.0_chart.json` — the public chart series

**Derived from the tape, never recomputed** *(etl.write_chart)*. Full published
history as four parallel arrays, so the page can plot v3 without shipping the
4.9 MB tape as a browser asset. ~135 KB, against the 166 KB `data.js` already
served publicly, so it costs nothing and **does not move the free/Pro line** —
full rows, pillars, bands and diagnostics stay in the tape and the window.

| Key | Meaning |
|---|---|
| `schema_version` | Same constitution identifier as the rows. |
| `generated_utc` | `YYYY-MM-DDTHH:MM:SSZ` of the write. |
| `note` / `scale` | Name the scale explicitly — see the warning below. |
| `n` | Row count; all four arrays are this long. |
| `t[]` | `asof_date`, ascending. |
| `p[]` | `price_usd`. |
| `r[]` | **`risk100`: an integer 0–100 percentile.** |
| `c[]` | `conf`, 0–10. |

> **`r` is not v2's `DATA.r`.** v2 publishes a 0–1 blend; this publishes an
> integer 0–100 rank. Plotting one on the other's axis is wrong by a factor of
> 100 and looks entirely plausible — which is exactly what happened: the chart
> drew v2's 0.556 for 2025-10-06 underneath a v3 gauge reading 80. `R_MAX` in
> `app.js` carries the scale with the data so no consumer can assume the wrong
> one, and `model/v3/tests/test_phase4.py` asserts the type and range rather
> than trusting the column name.

**The file stays canonical; the display converts.** `r` remains `risk100`
because that is what the tape, `/api/risk` and the bot all carry. `app.js`
divides by 100 when it loads the series so the chart axis reads 0–1 like the
gauge. The conversion is lossless: `risk100/100` equals the tape's `risk01` on
every row, max difference 0.0e+00 across 5,307 rows. Do not "simplify" this by
publishing 0–1 in the file — the integer is the canonical form and three other
consumers read it.

`risk01` and `risk100` both saturate at 1.00 / 100 for any day whose causal rank
reaches **0.995**, because the CDF is clipped to [0.001, 0.999] and `risk01` is
rounded to 2 dp. Sixty days in the tape print the maximum (55 in 2017, 3 in
2021, 2 in 2013); 24 of them sit at the hard clip. The chart's y2 axis carries
3% headroom above 1 so a day at the maximum does not draw on the frame and read
as if it had exceeded it.

Why the chart may not recompute: an expanding CDF means a recompute today moves
2017's rank (rule 1). A browser-side history would therefore disagree with the
tape printed above it. The risk line stops at the last committed row and the
price line continues live; the gap is the honest answer, not a provisional rank.

---

## `series/v3.0_last90.json` — the free window

**Derived from the tape, never recomputed** *(etl)*.

| Key | Meaning |
|---|---|
| `schema_version` | Same constitution identifier as the rows. |
| `generated_utc` | `YYYY-MM-DDTHH:MM:SSZ` of the write. |
| `window_days` | 90. |
| `note` | Points at the full tape and restates that committed rows are never rewritten. |
| `rows` | The last 90 tape rows, **verbatim**. |
| `map_support` | Quantile grids for the risk-price matrix widget — below. |

### `map_support`

Support for the **inversion widget only**. The official score is never
recomputed in the browser: **it is read from the committed row.**

| Key | Meaning |
|---|---|
| `n` | Rows in the full tape. |
| `quantile_levels` | 201 evenly spaced levels, 0.0 → 1.0. |
| `mvrv`, `g_raw`, `mayer` | 201-point **expanding** quantile grid of each raw series. Omitted if fewer than 400 values. |
| `mvrv_4y`, `g_raw_4y`, `mayer_4y` | Same on the trailing 1,460 days. Omitted if fewer than 200 values. **Both grids ship because V and G are blends of the two maps** — shipping only the expanding grid made the browser's pillar a different object from the committed one, measured at 1.6e-2 on G. |
| `composite` | 201-point grid of `smooth01`. **Required**, because the published score is the *rank* of the smoothed blend: without it the browser returns blend-space numbers while the gauge shows ranks. |
| `blend` | `{"V": [0.65, 0.35], "G": [0.70, 0.30]}`. |
| `realized_price` | `price_usd / mvrv` on the last row — lets the browser recompute MVRV at a hypothetical price without shipping realized cap. |
| `weights` | `compute.WEIGHTS`. |
| `retired_weight` | 0.10. |
| `genesis` | `"2009-01-03"` — the origin of `d` in pillar G. |
| `last` | Last row's `asof_date, price_usd, a, b, S, V, G, T, risk100, risk01, risk_lo, risk_hi, conf, n_live, stale, spread`. |

**Known gap before cutover:** the staged `v3.js` inverts into *blend* space while
the published score is a *rank*, so the matrix widget must map through the
`composite` grid. Fixing that is a precondition of the Phase 5 cutover — otherwise
the site has two different current-risk objects again, which is **defect F**, the
thing this project exists to kill *(`docs/BOOTSTRAP_RUNBOOK.md` §6)*.

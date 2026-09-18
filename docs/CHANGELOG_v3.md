# CHANGELOG — Bitcoin Risk Model v3

Version bumps only. Scheduled adaptation inside a version (expanding CDFs
lengthening, the 90-day growth refit) is **not** a bump and is not logged here.
A change to anything under *Constitution* is a new `schema_version` and a new
tape file; it never rewrites a committed row.

Spec: *BTC Risk Model — v2 Audit and v3 Design Roadmap* (11 Sep 2026).
Where the spec and this file differ, this file records the decision taken and
the date; the spec remains the rationale.

---

## v3.0 — unreleased (in development)

**Status 2026-09-11.** Phase 0 complete: v2 tape frozen as
`series/v2_frozen.json` (SHA-256 `0de9ddd6…67067c`, 2011-01-13 → 2026-09-10).
No v3 compute exists yet. v2 remains the public gauge.

### Constitution (locked 2026-09-11)

| Item | Rule |
|---|---|
| Families (4 live) | **V** valuation · **G** growth residual · **T** trend · **Σ** volatility regime. **M** (security residual) was **demoted 2026-09-11** — `mctc_mod` is still computed and published but does not vote (r(V,M)=0.932). **F** (holder flow) is **inactive**: no public realized-profit/loss series with a fallback exists. See decisions 0, 26 and A/B of 2026-09-11. |
| Weights | **V 0.31 · G 0.24 · T 0.15 · Σ 0.20 — active weight 0.90.** M's 0.10 is **retired, not recycled**. Renormalized over the families live that day. (An earlier revision of this row showed the pre-demotion five-pillar weights; `model/v3/constants.py` is the binding source and a test asserts this exact dict.) |
| Maps | Empirical CDFs only. `F_exp` expanding from t₀ = 2010-07-18 (first day with valid `PriceUSD` and `CapMVRVCur`); `F_4y` on a trailing 1,461-day window. `F(x_t) = (#{x_s ≤ x_t} − 0.5) / \|W\|`, clipped to [0.001, 0.999]. No logistic maps in the official path. |
| Nmin | 400 days (expanding) · 200 days (4-year) · 1,200 closes (growth fit). A pillar is inactive before its Nmin. |
| V | `0.65·F_exp(MVRV) + 0.35·F_4y(MVRV)`, raw = `CapMVRVCur`. |
| G | `0.70·F_exp(g) + 0.30·F_4y(g)`, `g_t = log P_t − (a_k + b_k·log d_t)`, `d` = days since 2009-01-03. |
| G fit | Huber regression (δ = 1.345, textbook default, not searched) of `log P` on `log d`, on data ≤ `fit_asof` only. **Fixed 90-day refit-and-hold**: first mark `2013-10-30` (1,201 closes), parameters live the same day, held 90 calendar days; next marks `2014-01-28`, `2014-04-28`, … Every row stores `a`, `b`, `fit_asof`, `n_fit`. The residual at *t* always uses the last committed fit, never a same-day refit. |
| T | `F_exp(P/SMA200)` alone. **T_dd deleted 2026-09-11** (decision 9); `dd` is charted, never mapped. |
| M | `F_exp(mctc_mod)` only. `mctc_mod = log(Mcap/thermocap) − 730-day mean (min_periods 180)`; thermocap = cumsum(`IssTotUSD` + `FeeTotNtv`·P) with last-observation-carried-forward **bounded to 3 calendar days**, never `fillna(0)`; past the bound M goes dark and `thermo_stale_days` is stored on the row. **Puell is not in v3.0** (see decision log). |
| Σ | `0.40·F_exp(σ30) + 0.60·F_exp(fragility)`, `κ = σ30/σ365` on daily log returns, `fragility = V·(1 − F_exp(κ))`. |
| Combiner | `raw_t = Σ w_k Π_k / Σ w_k·1{live}`; then EMA span 8 (α = 2/9 ≈ 0.222, `y₀ = x₀`). No net in v3.0. |
| Output | `risk100` = round(100·ema) clipped to [0,100]; `risk01 = risk100/100` (the dashboard keeps 0–1 for continuity); `risk_lo/hi = risk100 ± round(10·s_t)`, `s_t` = sample stdev of live pillar scores; `conf` 0–10 per spec §7.5. |
| Row | `asof_date, price_usd, risk100, risk01, risk_lo, risk_hi, conf, n_live, stale, {V,G,T,M,Σ}_raw, {V,G,T,M,Σ}, a, b, fit_asof, n_fit, schema_version, git_sha, input_hash`. |
| Inputs (NEED_V3) | Coin Metrics community only: `PriceUSD, CapMVRVCur, CapMrktCurUSD, SplyCur, IssTotUSD, IssTotNtv, FeeTotNtv`. GitHub CSV for history, community API after the CSV stalls. No other vendor in the daily job. |
| Tape | `series/v3.0.jsonl`, append-only, one row per UTC day, committed rows byte-stable. A recompute is `diagnostic_rank`, never the tape. |
| Satellites | None in v3.0. The only series that may be designed on paper for v3.1 is US spot-ETF 20-day net flow, weight cap 0.03, after 400 live days and an ablation. Funding and DXY are documented rejects-or-lab, not backlog. |

### Requires a version bump (never inside v3.0)

- Any weight, the pillar list, the V/G blend ratios, the fragility formula, the CDF continuity correction, Nmin, EMA span, Huber δ.
- Refitting G because price moved; skipping a scheduled refit; moving the refit clock to halvings or calendar quarters; OLS instead of Huber.
- Moving the sell thresholds of a reference book to "catch" a score that will not print — that is a score bug, so it is a score version.
- Reintroducing Puell, MVRV-Z, NUPL, supply-in-profit, an exchange-flow "RHODL", or any second valuation alias.

### Decision log — 2026-09-11 (Miguel, on the section-0 questions)

0. **Weights after dropping Puell (decided after Phase 0).** The dark-F defaults V 0.31 · G 0.20 · T 0.15 · M 0.18 · Σ 0.16 are replaced by **V 0.31 · G 0.24 · T 0.15 · M 0.10 · Σ 0.20**. Rationale: 0.18 was a family budget for two miner ideas (Puell 0.09 + mctc_mod 0.09); one mapped residual does not keep both seats. M at 0.18 would have made a single thermocap detrend heavier than trend and almost as heavy as growth, and would re-open the v2 failure where mctc stayed elevated after the 2026 slide while valuation had already cheapened. `mctc_mod` stays in the official combiner at 0.10.

1. **Inputs.** Coin Metrics community is the only required source for v3.0 (all seven NEED_V3 fields verified present in the CSV; CSV tail stalled at 2026-05-24, API top-up remains load-bearing). Other free, reliable sources may be used later for research, not in the v3.0 daily job.
2. **F dark.** No stand-in. Explicitly forbidden as "F": NUPL (= 1 − 1/MVRV, a V clone), the v2 `FlowInExNtv` ratio (the fake RHODL), supply-in-profit from a 4-year price percentile (the old `sip`). UI and docs say five pillars, not six; no SOPR / LTH / realized P&L in the about text.
3. **Growth cadence.** Fixed 90-day refit-and-hold from 2013-10-30, first parameters live immediately. Quarter-ends rejected (G would be dark at the 2013-12-04 reach test; Bitcoin does not live on Gregorian quarters). Daily-fit-with-90-day-lag rejected (same 2013 hole or a second rule for the first fit; tape harder to rebuild). Expectation at 2013-12-04: G live but early-sample; V, T, M carry that reach test; a wild G there is a Huber/sample-length note, not a reason to change cadence.
4. **Gauge.** v2 stays on the public gauge until v3.0 passes the §12.1 gates.
5. **Display.** Keep 0–1 on the dashboard; `risk100` and the band are stored on every row regardless.
6. **Satellites.** Zero in v3.0; P9 table committed below.
7. **Puell dropped.** Both §7.2 halving rules were measured and both are new defects: the literal "×2 for 365 days" rule halves Puell overnight at H+365 (8.33→4.51 in 2013, 2.50→1.03 in 2017, 2.68→1.29 in 2021, 1.06→0.47 in 2025); the subsidy-normalized construction is continuous but reduces to `P/SMA365(P)` (Pearson 0.98 on logs, Spearman 0.97; its expanding-CDF map correlates 0.89 with the Mayer map) — a price multiple in a miner costume. `HALVING_DATES` stay in the repo for charts and later experiments; they are not an input to `compute_v3`. Phase 2 must report raw Puell vs Mayer vs P/SMA365, mctc_mod vs V and vs T on mapped CDFs, and the |r| ≤ 0.80 matrix on the five live mapped pillars with Puell absent.
8. **Embargo.** Binds only the knobs still turnable (Huber δ, Nmin, cadence, EMA span, blend ratios, fragility formula, continuity correction); none may change after looking at 2025-10-06 or 2026-06-30, and 2025 may not be dropped from the reach list. Holdout window **2025-09-11 → 2026-09-10** inclusive. Expanding CDFs are *not* frozen on 2025-09-10 (the 2025-10-06 print uses history ≤ that day: causal production, not leakage). Holdout reach/order use the committed-as-of-that-morning rank; a today's-CDF number is `diagnostic_rank` only. Holdout bottom = the June 2026 region in the bottom quintile of the tape as it existed then; 2015/2018/2022 stay on the full-sample bottom test. The holdout sheet is labelled **"holdout / design-contaminated"**, not "unseen" — expanding CDFs and Σ were motivated by v2 dying at the 2025 ATH; real out-of-sample is the next ATH after v3.0 is frozen.

### Decision log — 2026-09-11 (second round)

9. **T_dd deleted; T = Mayer alone.** Because `dd ≥ 0` always, `−dd = 0` is the series maximum, so `F_exp(−dd)` took the maximum attainable score on **every** new-ATH day (197 days, 3.7 % of the tape) regardless of valuation — v2's `ATH**1.5` defect (§3.4) with a smaller coefficient. Smoothing only postpones the clip. Drawdown's unique information is the left tail; the right tail is already covered by Mayer (extension vs the 200-day) and by MVRV/mctc_mod (expensive vs cost basis). **The trend budget stays 0.15 and is not recycled** into another pillar. A future crash bonus must be a new one-sided key with a ceiling of 0.5 at `dd = 0`, never a revival of T_dd.
10. **Roadmap Table 2 — header corrected, code frozen.** The table carries four values under three headers. Correct order: `Date | MVRV (level) | z_v2 = (M−R)/std_exp(M) | z_alt = (M−R)/std_exp(M−R) | s = σ(z_v2; 3.20, 1.60)`. At 2013-12-04: 4.72 · 7.70 · 9.88 · 0.94, all four reproduced from the community CSV. Caption: *column 2 is the z the code uses; it is not MVRV.* No model change. A test asserts `z_v2 ≠ MVRV` on that fixture so nobody later "aligns" them.
11. **LOCF is a vendor-hole guard, not a restatement.** On the 2026-09-10 production extract, `IssTotUSD` and `FeeTotNtv` have no holes, so LOCF and `fillna(0)` are bit-identical and the published series does not move. LOCF is bounded to ≤ 3 calendar days; past that, M is NaN and drops out of the renormalised blend rather than writing an invented contribution into the cumsum. Measured: zero-fill is 3.7×–42× worse than bounded LOCF depending on hole length, field and era. A cumsum never fully forgets — a hole leaves a permanent trace below 1e-3 in mapped M — so staleness is disclosed on the row, not erased. No backfill, no version bump of historical `r_t`.

### Decision log — 2026-09-11 (third round, Phase 3 gate rulings)

12. **Order — the 2025 inversion is a documented §4 residual, not a defect.** 2025-10-06 prints 66 against 2024-03-13 at 70: the higher high in dollars is the lower high on extension and cost basis (Mayer 1.18 vs 1.85, MVRV 2.29 vs 2.75, κ 0.62 vs 1.33). G +0.047 and Σ +0.233 vote 2025; V −0.076 and T −0.306 vote 2024. Forcing 2025 above 2024 would be the ATH sleeve under a new name — T_dd printed 0.999 on *both* dates and still left a −0.168 gap, which is proof that a mechanical high-at-highs term cannot manufacture an order the valuation tape does not support. Registered in `validate.DOCUMENTED_INVERSIONS`; an **undocumented** inversion still fails the gate. No weight change, no version bump.
13. **Collinearity — the partial is the blocker; raw differences were the wrong instrument.** V, G and T are monotone maps of a price-like state, so `Δs_i ≈ β_i·Δlog P + u_i` and `r(ΔV,ΔT) = 0.883` is mostly `r(Δlog P, Δlog P)`. Weekly (0.901) and quarterly (0.902) figures are worse for the same reason: differencing discards the day-noise and keeps the shared bull/bear tick, isolating the common driver the families are *allowed* to share. It does not reveal aliases. The gate is now three statistics, printed every run, on the Mode A common live span, pairs in {V, G, T} only:

    | Statistic | Role | Fails if |
    |---|---|---|
    | `r(Δs_i, Δs_j \| Δlog P)` | ship blocker | ≥ 0.70 |
    | `r(s_i, s_j)` levels | disclose only | never |
    | `r(Δs_i, Δs_j)` raw | price-elasticity diagnostic | never |

    Current cells: V–G 0.334 / 0.846 / 0.797 · V–T 0.526 / 0.784 / 0.883 · G–T 0.316 / 0.584 / 0.778. Σ pairs are off the gate — a family is supposed to correlate with the composite. G and T are **not** demoted, not orthogonalised onto V, and no weight is cut to paint a cell. The control is the same BTC log price that feeds T, first-differenced and aligned to the score date, Pearson on the residuals of Δs on Δlog P; swapping in a "smarter" control (Mayer, or V itself) is a new gate and a new version.
14. **Nested baseline — non-inferiority to the two named baselines.** v3 must not lose to Mayer or MVRV percentile; the 200-week SMA distance is a reported comparator, never a ship blocker. Pass rule: the block-bootstrap CI for `s(v3) − s(baseline)` must not lie entirely below zero. Common sample (n = 3,737, 2015-06-20 → 2025-09-11): v3 +0.0160, Mayer −0.1259, MVRV −0.0348, 200w SMA +0.0716; CI v3−Mayer [−0.006, +0.275], v3−MVRV [−0.014, +0.117], v3−200w [−0.171, +0.054]. Inconclusive is not a failure — failing a model on a statistic that cannot rank the candidates is a gate bug. The 200-week SMA is never inserted into V to win a point estimate, and the window, horizon and sample are frozen in `docs/phase3_appendix.md`.

### Decision log — 2026-09-11 (fourth round, Phase 4)

15. **Combiner keeps the live-weight denominator.** `risk = Σ w_i s_i / Σ w_live`. The two combiners are an affine rescaling of the same `Σ w_i s_i`; relative influence of V, G and T is identical, so a fixed 1.00 divisor buys nothing the gates measure and only breaks external thresholds. The anti-recycle rule was "do not pour the retired 0.10 into another family", not "the gauge must die at 90". The retired 0.10 stays out of every family weight table. Max on the current tape is 89 either way, so a raw sell-at-95 fires under neither reading: the reference books must bind their sell to something scale-free (e.g. score at or above the 85th percentile of the last eight years, or score ≥ 80 **and** κ above its own threshold), which is invariant to this argument. No always-on dummy family at 0.5 to paint a 95.
16. **The tape stays INCOMPLETE and the holdout stays UNSIGNED.** A holdout that misses the June 2026 low is not merely a short year — that low is the first v3 out-of-sample bottom, and calling the gate a PASS on 255/365 days would hide the only observation that can falsify "cheap at lows". The gate report keeps its `INCOMPLETE` line. **No sentence of the form "validated through 2026" may be written, and no Coinbase spot may be spliced into V or G to fill the hole.** 2026-06-30 is registered in `validate.NAMED_CHECKS` and prints `PENDING` until the tape reaches it — a named check, never a gate and never a retune date. The official tape end stays 2026-05-23 until the ETL is re-run on the production path that reaches the Coin Metrics community API (the same Action that builds v2 `data.json` through 2026-09-10).
17. **Warm-up confidence corrected (implementation, not constitution).** With one live family the sample standard deviation of the family scores is *undefined*, not zero; treating it as zero read as perfect agreement and printed conf 6 on the lone-V rows of 2011. Undefined agreement now contributes nothing, so a one-family day is carried by completeness alone and prints conf 1-2. Rows with all four families live are unchanged, and every gate is unaffected.

### Decision log — 2026-09-12 (post-audit corrections, pre-bootstrap)

18. **The published score is now the causal rank of the smoothed blend.** `risk01 = F_exp(EMA(raw))`. A blend of four percentiles is not a percentile: the blended composite never exceeded 0.890 in fifteen years, so §14's "82 means more extended than 82 % of causal history" was false for the number being published, and the reach gate only passed because `validate.py` re-ranked it behind the scenes. The pre-rank blend stays on every row as `raw01` and `smooth01`. Consequences, all measured and accepted: the scale reaches its top (2017-12-17 prints 100); ≥ 80 now fires on 29.9 % of days, against 4.5 % before — because 30 % of Bitcoin's days really have sat in the top quintile of their own history, and the old 4.5 % was an artefact of averaging percentiles. **Policy thresholds must be read on the ranked scale: a moderate book sells at 90–95, not 80.** The tape now starts 2012-03-07 (400-observation rank warm-up), which also removes every one- and two-family row.
19. **Correction to the rationale in decision 18.** The expanding rank is a *time-varying* map, not a fixed monotone transform. Within a day's own history it is monotone, so reach and bottom are unaffected; **across dates it is not** (cross-date ρ = 0.958), so cross-date statistics do move. Measured: the nested-baseline point estimate falls from **+0.0156 (blend) to −0.0562 (rank)**, below MVRV percentile alone (−0.0348). The gate still passes on non-inferiority — CI vs Mayer [−0.069, +0.198], vs MVRV [−0.101, +0.065] — but the claim "moves no gate" was too strong and is withdrawn. Accepted because the object is a classifier of present conditions, not a forecaster, and every one of these correlations is statistically indistinguishable from zero.
20. **Band = the image of the disagreement interval.** `[F_exp(smooth − s_t), F_exp(smooth + s_t)]`, not `rank ± k`. The §7.5 text "risk100 ± 10 s_t" cannot produce the "±6 to ±15 points" the same paragraph predicts — `s_t` is the standard deviation of numbers in [0, 1], so `10 s_t` can never exceed ±5 and measured ±1. The implemented band measures median ±15, p10 ±6, and is asymmetric because the map is nonlinear. Calibration hook (§12.3): the next-30-day score falls inside today's band on **79.5 %** of days.
21. **Gates read `rank_exact`, never the integer.** Rounding 0.797 up to 80 and then calling the day top-quintile is rounding in our own favour. The reach and bottom gates now print signed margins.
22. **§9 operational gaps closed.** `stale` is now driven by the age of **any** NEED_V3 field (`input_stale_days` on the row) — previously an MVRV outage darkened V, renormalised the blend and published `stale = 0`. Added: `csv_vintage` and `api_vintage` per row; `price_alt` from a second public daily close with `price_alt_flag` when the two differ by more than 3 % (never averaged; null when the source is unreachable); a 7-day escalation in the health check that names the maintainer page and forbids rewrite/refit/reweight as the response.
23. **Defect B is reclassified as structural, not fixable by indicators.** A candidate pillar F derivable from existing inputs — net realised P/L ≈ ΔRealizedCap − issuance, over realised cap — is independent on *changes* (partials −0.001 to 0.07 against V/G/T/Σ) yet moves effective rank from **1.59 to 1.58**, because on levels it is another cycle oscillator (0.88 with V). Bitcoin has one cycle and every valuation indicator is a view of it. v3.0 is therefore documented as **one dominant factor plus a volatility modifier**, never as "multifactor". Real level-diversity needs a non-cycle dimension (leverage, liquidity, options) and is a v3.2 research question.

### Decision log — 2026-09-14 (v3.1 Step 1: the outcome layer)

24. **The outcome layer is built, measured, and SHIPS DARK.** `model/v3/outcome.py` estimates, causally, the distribution of forward outcomes on the historical days most similar to today (state = composite rank × volatility-compression rank, k = 250 nearest, horizon 180 days). Double causality: an analogue day may only be used at *t* once its own forward window has closed, so the layer sees strictly less than the score does.

    Activation was pre-committed before the first measurement: publish only if the conditional probabilities beat the causal base rate (`MIN_SKILL = 0.02`, effective n ≥ 20). **They do not.** Brier conditional 0.2812 vs climatology 0.2485 → **skill −0.131**; by era, 2013-17 −0.216 and 2018-26 −0.086. The reliability table is *inverted*: the highest-probability bin (predicted 0.628) had the **lowest** observed frequency (0.195), and the lowest-probability bin (0.156) the highest (0.486). Interval coverage 68 % against a nominal 80 %.

    The raw association says the same thing without any model: realised P(drawdown > 30 % within 180 d) by rank band — 0–20: **0.346**, 20–40: 0.255, 40–60: 0.480, 60–80: 0.235, 80–100: **0.304**. No monotone relationship. corr(rank, forward 180-day return) = **+0.173**, i.e. in this sample extension was followed by *higher* returns, not lower.

    **What this means, and it is the most important empirical finding of the v3 work:** the score ranks how extended the market is; it does not forecast drawdowns at a 180-day horizon, and a probability layer built on it would have told holders that expensive is safe. The effective sample is ~24 independent windows, so this does not establish that no such relationship exists — it establishes that we cannot support one, which is the same thing for publication purposes.

    **Product consequence:** publish the unconditional **climatology** (the causal base rate, calibrated by construction and conditioned on nothing) as context, never the conditional probability. `validate.py` gains a reporting gate that prints the full calibration table every run and states LIVE or DARK; a dark layer does not fail the build, because dark is a legitimate documented state. Forbidden: searching outcome definitions, horizons or thresholds until one passes.

### Decision log — 2026-09-14 (v3.1 Steps 3 and 6)

25. **Family admission rule, fixed before any candidate was measured.** A new family enters the combiner only if, on the causal tape: **(a)** the 4-year dynamic-DCA advantage does not fall below the incumbent +17.1 % (weight = $200 × (1 − rank), no selling, no look-ahead — §12.2 diagnostic used as an *admission* test, never a ship gate); **(b)** reach, bottom and the partial-correlation gate still pass; **(c)** effective rank rises; **(d)** the score becomes less sensitive to a single-input outage. Passing (b)–(d) but failing (a) makes a series a *robustness input* (fallback or flag, like `price_alt`), not a voting family. Baseline for comparison, measured causally: v3.0 rank **+17.1 %** over flat DCA on 4-year windows, against MVRV percentile alone +15.9 %, v2 +9.8 %, and a pure-noise control −0.2 %.
26. **Stablecoin liquidity (L) — REJECTED as a voting family.** Raw: `log(BTC mcap / (USDT + USDC supply))`, detrended by a 730-day mean exactly as `mctc_mod` (a raw level would repeat defect D — the ratio drifts 15.8 log units across the sample). Tested at weight 0.10 funded from the retired budget, leaving V/G/T/Σ untouched.
    - **(c) PASS, and by the widest margin of any candidate measured:** effective rank **1.59 → 2.30**, PC1 77.8 % → 61.5 %; partial correlations with V/G/T of −0.025, −0.102, +0.013.
    - **(a) FAIL:** DCA advantage **+17.1 % → +16.6 %**.
    - **(b) FAIL:** the 2018 bottom test breaks, 0.169 → **0.224**, outside the bottom quintile.
    - **Diagnosis:** through 2020 the denominator is an adoption S-curve, not a liquidity condition — stablecoin supply grew **+4520 % yoy in 2017** and +1157 % in 2018, and no 730-day detrend separates "more dry powder" from "stablecoins being adopted". L prints **0.730 at the December 2018 bottom**, which is what breaks the gate. The series only means what it claims after ~2021.
    - **Verdict:** genuinely diverse and genuinely contaminated. Not admitted. Revisit as a v3.2 candidate when roughly two cycles of mature (post-2021) stablecoin history exist. **No weight search was run**: hunting for a weight at which L passes would be the retune this project forbids.
27. **Second MVRV source (P9 gate 2) — BLOCKED, not rejected.** Every candidate endpoint is unreachable from the build environment (403: blockchain.info, bitcoin-data.com, glassnode); only `raw.githubusercontent.com` is allowlisted, which is the incumbent vendor. So the claim "if Coin Metrics goes dark there is a second public construction within 30 days" **remains unverified**, and `CapMVRVCur` stays a single point of failure for pillar V. The `price_alt` mechanism covers price only. Needs a nominated source and a run with open network before anything can be said.

### Decision log — 2026-09-14 (Step 6: second realized-cap construction)

28. **A second realized cap ships as a RESTATEMENT DETECTOR, not as a fallback, and P9 gate 2 stays unverified.** `model/v3/rcap_alt.py`, off by default (`etl/daily_v3.py --with-rcap-alt`).
    - **Construction.** Compare realized caps, not MVRV ratios. A second vendor's MVRV brings its own price and supply, so a 3 % gap could be methodology or a different close. `MVRV_alt = CapMrktCurUSD / R_alt` reuses the Coin Metrics market cap the rest of the tape already trusts, so any gap is realized-cap methodology by construction.
    - **It adds no precision, by design.** Neither `R_alt` nor any rebuild votes in V, and the published score does not change. What it adds is *detection*: `input_hash` catches "the inputs changed" but cannot say which side is right; the gap can. A CM outage still costs pillar V for that day — V goes dark and the combiner renormalises. **A vendor swap mid-version is forbidden; it would be v3.1 with its own tape.**
    - **Three statistics, each for a different failure.** Rank co-movement and dlog-R correlation catch jumps and noise. The 365-day **drift of the log gap** catches a creeping restatement, which the other two cannot: a smooth 60 % divergence over 200 days leaves dlog-R correlation at **0.999** and rank correlation at **0.98** — both still look healthy on a dashboard. A constant methodology gap has zero drift. This was found by a test failing, not by design.
    - **No alerts until a band exists.** The bridge source paywalls the last 7 days and caps free history at four years, so the normal CM-vs-alt gap cannot be established for free. `GAP_BAND_LOG = None` means publish the gap, alert on nothing: a guessed threshold is worse than none.
    - **Rules, same family as LOCF and the tape:** no mixed clocks (MVRV_alt only on dates both series carry); level equality is never the test; `rcap_alt_hash` on the row beside `input_hash`; the detector never rewrites `v3.0.jsonl`.
    - **P9 gate 2 is NOT satisfied.** A vendor API is a second phone number, not a second method. The gate is satisfied only by a realized cap rebuilt from an indexed UTXO set (`R_self`), which needs a full node — out of reach for a browser-only maintainer on GitHub Actions. Named as the target in the constitution, recorded as absent, not claimed.
    - **Unverified from the build sandbox** (403 on every external host). It must be exercised once in Actions before the daily job is allowed to depend on it, and the numerator-clock assumption — that the alternative realized cap uses the same daily close as CM's market cap — must be checked on real data at that point.

### Decision log — 2026-09-14 (Step 2: specification ensemble)

29. **Model uncertainty is now published: `ens_lo` / `ens_hi` on every row.** `model/v3/ensemble.py` recomputes the entire causal tape under a **pre-registered** grid of 33 equally plausible constitutions and publishes the spread. Two bands now sit side by side and answer different questions: `risk_lo/risk_hi` = how much the **families** disagree today; `ens_lo/ens_hi` = how much the **answer depends on choices nobody tested**.
    - **Grid** (fixed before any output was inspected): EMA span 5/8/13 · Nmin_exp 300/400/500 · Nmin_4y 150/200/300 · 4-year window 1095/1461/1827 · V blend .50/.65/.80 · G blend .55/.70/.85 · Σ blend .25/.40/.55 · Huber δ 1.0/1.345/2.0 · refit cadence 90/180 · four weight vectors (incumbent, equal, V-heavy, Σ-heavy). One-at-a-time members plus 12 deterministic multi-axis members (seed 20260914), because sensitivities interact and OAT alone understates the band.
    - **Three rules that stop it becoming a tuning device:** no member is ever selected (the official row is always the constitution, and no member is inspected for performance); the grid is pre-registered, so adding a level after seeing results — or dropping one that widens the band — is a retune, and a dishonest one because it narrows published uncertainty without changing the model; every member is a full causal recomputation, not a perturbation of the incumbent's output.
    - **Measured:** band width **median 9.7 points**, p90 18.9, max 53.4. The incumbent sits at the **52nd percentile** of its own grid, so the constitution is not an outlier of the alternatives it is being judged against. Cost ~10 s for 33 members; runs in the ETL, never in the browser.
    - **The headline result, which supersedes the ±0.003 framing of decision 21:** at **2025-10-06 only 52 % of plausible constitutions put the day in the top quintile** (incumbent 0.803, range 0.696–0.830). The older highs are unanimous — 2013, 2017, 2021-04, 2021-11 and 2024 all at **100 %**. The 2018 bottom is the weakest of the lows at **88 %**. So the honest statement about the 2025 ATH is not "passes by +0.0035" but "roughly half of equally defensible models call it top-quintile, and half do not".
    - **Most influential single choices** (median |Δ| vs incumbent): Nmin_exp 300 → 1.38 pts, equal weights → 1.34, Σ-heavy weights → 1.27, Σ blend → 1.12, 4-year window 1095 → 1.05. No single knob moves the daily number by more than ~1.4 points on the median day; the width comes from their interaction.
    - `validate.py` gains a reporting gate that prints the whole table every run. It **does not fail**: a threshold on "share of members agreeing" would have to be invented after seeing the numbers.

### Decision log — 2026-09-14 (Step 4: growth-path specification risk)

30. **Pillar G is now refitted under five trend specifications and the disagreement is published (`g_spec_lo` / `g_spec_hi`).** `model/v3/growth_specs.py`. The specification ensemble varies G's *parameters* — δ, cadence, blend, Nmin — but never its *functional form*, so the model-uncertainty band was silent about exactly the limitation §14 names: *"a power law in days-since-genesis is a model… the 90-day freeze and Huber loss reduce drama; they do not pick the true trend."*
    - **Pre-registered specifications** (fixed before any output was seen), all causal and on the same 90-day refit-and-hold cadence: **A** `log P ~ a + b log d`, Huber — **official**; **B** same design, median (L1) regression — the other estimator §7.2 permits; **C** `log P ~ a + b·d`, constant exponential growth — the competing structural story; **D** A fitted on the trailing 8 years only — does the distant past still deserve to set the slope; **E** continuous hinge at **2024-01-10** (US spot ETF approval), i.e. §14's "broken log-linear with ETF-era slope change", dark until 400 post-knot days exist so the hinge is not fitted on nothing.
    - **Measured:** spread **median 8.6 pts, p90 16.2, max 63.9**. Correlation with A: B **0.989**, D 0.988, C 0.980, E 0.573. Changing the *estimator* barely matters; changing the *shape* matters more — and E, the ETF hypothesis, is the one that departs.
    - **Where it bites is recent, as predicted.** At 2017-12-17 the specifications differ by 0.8 pts. At **2025-10-06 they differ by 18.1** (A 0.710, D 0.799, E 0.684, C 0.619) and at 2026-05-23 by **23.6** (A 0.303, D 0.395, E 0.159, C 0.180). The trend specification is close to irrelevant for the old cycles and materially uncertain for the one we are living in.
    - **Caveat on E:** with 250 live days its expanding map has not reached Nmin 400, so its blend runs on the 4-year component alone. It is a thinner object than the others and should be read as such until ~2027.
    - **Disclosure, never a switch.** The official G stays specification A. Switching to whichever trend currently flatters the score is the purest form of retune; persistent divergence is evidence for a **version review** under §9.4, decided deliberately and shipped as its own tape. A test asserts no production module imports `growth_specs`, so G cannot be switched by accident.
    - `huber_fit` was generalised to `huber_fit_design` (arbitrary design matrix, Huber or L1). The production fit is **bit-identical**: (a, b) at the first mark is still (−43.2814, 6.4097).

### Decision log — 2026-09-14 (Step 5: P6 in CI, and the bootstrap runbook)

31. **P6 is enforced by CI, in its own workflow.** `tools/check_copy.py` + `.github/workflows/copy-audit.yml`. It audits only what a visitor can read — HTML text nodes and i18n dictionary values, with code comments and docstrings stripped first, because every grep-shaped check in this project has at some point matched its own explanatory prose. A test asserts that property directly.
    - **Two severity classes with different deadlines.** *Defect G, fatal now:* **39 strings** name hashrate, difficulty, active addresses, long/short-term holder positioning and implied crash probabilities — series **no version of this model has ever computed, v2 included**. The earlier audit filed defect G as "documentation vs code, resolve later"; it is a live violation today and the check now says so. *Fatal at cutover:* **92 strings** describing v2 machinery — eleven signals, logistic maps, Puell, MVRV-Z, RHODL, terminal price, supply-in-profit. Those are *accurate* while v2 is the live model, so the check keys off which tape the page actually reads, detected from the page rather than a hand-maintained flag.
    - **It gates a deploy, never a row.** Deliberately separate from `btc-data-v3.yml`: a copy defect must not stop an append-only series from recording what the model saw that morning. Wiring it into the tape job would make an unfinished sentence block the data.
    - **No waiver list.** The fix is to change the sentences. A test asserts the live site currently fails, so the check cannot be weakened instead of the copy being fixed.
32. **`docs/BOOTSTRAP_RUNBOOK.md`** — the sequence only the maintainer can execute, since the Coin Metrics API, CoinGecko and bitcoin-data.com are all unreachable from the build sandbox. It states the abort condition explicitly: if the dry run still reports **5,191 rows through 2026-05-23** the API top-up failed silently, and bootstrapping on that would freeze a truncated tape permanently — `--bootstrap` refuses to run twice and append-only means it could never be repaired. Until the runbook is followed the official tape end stays 2026-05-23, the holdout stays **UNSIGNED**, and 2026-06-30 prints `PENDING`.

### Decision log — 2026-09-17 (first production run; a gate defect it exposed)

33. **The tape is live.** Bootstrapped on the runner: **5,307 causal rows through 2026-09-16**, API top-up `2026-05-24 → 2026-09-16`, health ok, `n_live=4`, `stale=0`. First committed reading: **risk100 = 32**, band 24–44, conf 7; ensemble 27–34; G across five specifications 0.228–0.477.
34. **`validate.py` was scoring a recompute, not the committed tape — fixed.** §12.1 says reach, order, bottom and the holdout are *"evaluated on the committed tape, not on a diagnostic recompute"*, and it was doing the opposite. The consequence was not cosmetic: the ETL tops up from the Coin Metrics API, while `validate` read the published CSV dump alone, so the first production gate report scored **5,191 rows to 2026-05-23** while the committed tape held **5,307 to 2026-09-16** — and went on reporting the June 2026 low as `PENDING` after it had already landed. Tape-based gates now read `series/v3.0.jsonl`; pillar-based gates still recompute (they need the raw series) and the header prints both spans plus a NOTE when they differ. A missing tape is labelled `DIAGNOSTIC RECOMPUTE — not evidence about the published series`, never silently substituted. Two tests lock it.
35. **Known-stale in production:** the workflow on `main` predates rev 3, so the test gate runs without `btc.csv` — **51 passed, 65 skipped**. The step goes green having exercised under half the suite. Not a data risk (the ETL fetches its own inputs) but "a broken model must not write a row" is doing about 44 % of its job until rev 3 lands.

36. **The CI test gate is real, and the gate report cannot go stale.** The curl step landed, so the daily job's test gate went from **51 passed / 65 skipped** in a few seconds to **115 passed, 3 skipped in 106 s** — 118 collected, 0 failed, and all three skips named (`statsmodels` absent, no single-family rows in this tape, `scipy` absent for the `_spearman`-vs-scipy equivalence check). The ship-gates step now tees `model.v3.validate` into `docs/gate_report_v3.0.txt` and the commit step stages it with the tape, under `shell: bash -eo pipefail {0}`. GitHub's default is `bash -e {0}`, where a pipeline takes `tee`'s exit code — a **failing** gate report would have been committed under a **green** step. Measured: `with pipefail: exit=1 / without pipefail: exit=0`. The first regenerated report changed 8 lines and 5 removals: the header, and the holdout footer going from `n=255 / INCOMPLETE` to `n=365` with 2026-06-30 at rank 0.104. **Every gate number was byte-identical** between the 5,191-row recompute and the 5,307-row committed tape, the 2025-10-06 knife-edge included — append-only visible in a diff.

37. **Collinearity now reads the committed pillars; only the alternative-rebuilding gates recompute.** The Coin Metrics CSV dump has been frozen at 2026-05-24 since May and only the API top-up reaches the present, so *every* pillar-based gate was scored four months behind the tape — a **standing condition**, not one-off staleness, and one that would have widened indefinitely. Collinearity needed no recompute at all: `V`, `G`, `T`, `S` and `price_usd` travel on every committed row, and reading them is more faithful to §12.1 because it measures the pillars that were **published**. Verified the same quantity first: max |difference| against the recompute over the 4,390-row overlap is **5e-7**, exactly the half-ulp of the 6-decimal rounding `_row_to_json` applies. Numbers moved as expected when the span extended from 4,390 to 4,506 rows and nothing flipped:

    | statistic | CSV recompute (→2026-05-23) | committed tape (→2026-09-16) |
    |---|---|---|
    | V-G partial / level / raw | 0.334 / 0.846 / 0.797 | 0.337 / 0.850 / 0.798 |
    | V-T partial / level / raw | 0.526 / 0.784 / 0.883 | 0.526 / 0.786 / 0.883 |
    | G-T partial / level / raw | 0.316 / 0.584 / 0.778 | 0.328 / 0.588 / 0.782 |
    | max \|partial\| (blocker, < 0.70) | 0.526 | **0.526** |
    | Σ off-gate S-V / S-G / S-T | +0.744 / +0.747 / +0.497 | +0.751 / +0.757 / +0.502 |

    Largest move 0.012 (G-T partial). The threshold did not move, the pair set did not move, the decision did not move — this is a change of *which* pillars are measured, never of *how*, and the gate prints `pillars read from:` every run so a silent swap is visible. Ensemble, growth-specs and the outcome layer genuinely cannot read the tape (they rebuild alternatives from raw series) and now take the same topped-up frame the ETL uses.

38. **One input loader, in `etl/inputs.py`.** `validate.py` had a second path — a bare `pd.read_csv` — and it read a staler source than the tape. That is the class of bug a duplicated loader produces, so there is now one loader with one behaviour. Layering: `etl/inputs.py` imports only stdlib, pandas and `model.v3.constants` (itself import-free), and never a compute module, which keeps `model.v3.validate → etl.inputs` cycle-free. `model/v3/` still must not import from `etl/`; `validate.py` is the single exception and only at the CLI edge, **lazily inside `main()`**, so `import model.v3.validate` pulls no ETL code into the tests. If that import fails the report degrades to a bare CSV read and **says so in its header** — a silent downgrade to staler inputs is the bug being fixed. The loader's stdout is redirected to stderr inside `validate`, because `validate`'s stdout *is* the committed report. `build()` now recomputes from one already-loaded frame: the rewrite probe calls it twice, and re-fetching would have made a determinism gate depend on two API responses agreeing.

39. **The "price and risk since 2011" chart was still v2, under a v3 gauge.** It plotted `DATA.r` from `data.js`: **2025-10-06 drew 0.556 against v3's 80/100, and 2024-03-13 drew 0.676 against 86/100.** v2's central failure — a compressed-multiple all-time high printing mid-cycle — was being shown as history underneath the number that exists to correct it. The site had no v3 history to plot: the 90-day window is too short and the tape is 4.9 MB. So the ETL now writes `series/v3.0_chart.json` beside the window each run, **derived from the committed tape, never recomputed** — 135 KB against the 166 KB `data.js` already served publicly, so the free/Pro split does not move. Two scale hazards were handled rather than inherited:

    - **`r` is `risk100`, an integer 0–100 percentile, not v2's 0–1 blend.** `R_MAX` travels with whichever series loaded, so the y2 axis (`[0,100]`, integer ticks), the colour ramp, the tooltip (`Extension 80/100`, not `Risk 0.803`) and the CSV export (`risk100_percentile` vs `risk_v2_blend`) cannot assume the other one's scale. Tests assert the type and range rather than trusting the column name.
    - **The colour stops were repositioned onto the published quintile bands** (0–20 / 20–60 / 60–80 / 80–100, occupancy 13 / 31 / 27 / 29 %). v2's stops were placed against v2's distribution, and carried onto a ranked scale the same positions catch a completely different share of history: `≥0.48` goes from 30.5 % of days to 65.1 %, `≥0.70` from 7.9 % to **43.0 %**, `≥0.88` from 0.8 % to 15.4 %. A chart that looked mostly blue would have come back mostly hot without a single number changing. This is not a new choice — it is the already-approved quintile wording propagated, because a legend that disagrees with the email is the P6 problem in visual form.

    The risk line stops at the last committed row and the price line continues live: there is no v3 rank for a day the tape has not published, and deriving one in the browser is the recompute rule 1 forbids. The swap is gated on `V3.active` **and** the chart series loading; either missing leaves the chart on v2 with v2's own copy and legend, never a mix of the two scales.

40. **The chart is on 0–1, and it should have been from the start.** Decision 5 already says *"the dashboard keeps the 0–1 scale for continuity"*, and `docs/SCHEMA_v3.0.md` records it against `risk01`. Shipping the chart's axis on 0–100 in decision 39 was the deviation, not the correction of one. The published file still carries `risk100` — the canonical integer the tape, `/api/risk` and the bot all read — and `app.js` divides by 100 on load. **The conversion is lossless: `risk100/100` equals the tape's own `risk01` on all 5,307 rows, max difference 0.0e+00**, so the chart axis and the gauge needle now print the identical number. Do not "simplify" by publishing 0–1 in the file; three other consumers read the integer.

41. **Why some readings print the maximum, and why that is not "above 100".** Asked whether the 2017 and 2021 tops exceeded 100. They do not: the published series runs **5 to 100 inclusive, with zero values above it**. What those tops do is *saturate*. Every empirical CDF is clipped to **[0.001, 0.999]** so no day is ever ranked as literally 0 % or 100 % of history, and `risk01` is then rounded to 2 dp — so any day whose causal rank reaches **0.995** prints `1.00` / `100`. Sixty days do: 55 in 2017, 3 in 2021, 2 in 2013. Twenty-four of those sit at the hard clip itself. `1.00` therefore means *"more extended than at least 99.5 % of its own causal history to that morning"* — never "the top", and never a probability.

    The *appearance* of exceeding the maximum was a plotting artefact: on a hard `[0, 1]` range a line at exactly 1.00 draws on the frame's top pixel and reads as though it had spilled past it. The y2 axis now carries **3 % headroom** with `dtick 0.2`, so a saturated day sits visibly inside the frame and the tick labels still read 0.0 … 1.0.

42. **The heat map gets its own ramp; intense red is now the cycle extremes only.** v2's stops reddened **15.4 %** of all history — a colour nothing rare can stand out from. The heat map's job is different from the gauge's: it colours a fifteen-year price line, so what it must do is make the handful of genuine extremes findable. Red therefore opens at **0.95** (7.2 % of days) and deepens to 1.00 (1.1 %, 60 days), which is roughly a fortnight of colour per cycle. This also lines up with decision 18's own guidance that *"a moderate book sells at 90–95, not 80"*.

    Measured against every running-ATH peak in the tape:

    | peak | price | extension |
    |---|---|---|
    | 2012-12-13 | $14 | 0.76 |
    | 2013-12-04 | $1,135 | 0.94 |
    | 2017-12-16 | $19,641 | **1.00** |
    | 2020-12-31 | $29,023 | 0.98 |
    | 2021-11-08 | $67,542 | 0.91 |
    | 2024-12-17 | $106,116 | 0.90 |
    | 2025-10-06 | $124,824 | **0.80** |

    **The visible consequence, stated rather than hidden:** the two most recent dollar all-time highs come out orange (2024-12-17) and amber (2025-10-06), **not red**. That is the documented order inversion — higher high in dollars, lower high on extension and cost basis — showing up in the picture. Reddening them would mean painting over the model's actual finding to make the chart look like the price chart, which is the same error as retuning a gate to pass.

    There are now **two ramps on one page**, deliberately. The gauge, the risk-price matrix and the ranges table stay on the four published quintile bands so their colour agrees with the email and the bot; the heat map uses the peak-emphasising ramp and the heat strip is built from those same stops in `app.js` rather than hard-coded in CSS, so the legend cannot drift out of sync with the colours it describes.

43. **The X bot's generated body could restate the reading wrongly, and nothing checked.** Found in a failing run's log on 2026-09-17: the body read *"the model sits at 0.32 today, below mid and more extended than 32 percent of its history to **2026-03-14**"* against a committed row dated **2026-09-16**. The value and the percentage were right; the date was wrong.

    It was not a hallucination. `2026-03-14` is the **few-shot example's own date**, present twice in the prompt (`LONG_FEWSHOT_USER`'s `Date:` line and its hist string). The model pattern-matched and copied it. `long_text_ok` validated length, refusal-shape and investment-advice patterns from the start — **the numbers were never validated**, which is the wrong way round for a model whose entire claim is that the number means what it says. Nothing shipped only because the X API returned `402 credits depleted` and killed tweet 1 of the chain. It would have gone out on the first run with credits. (The 402 is an account-billing matter and predates this work: the bot has failed daily since 2026-09-14.)

    Fixed on both sides, because a fact the generator cannot corrupt beats a fact it is asked not to:

    - **The date is withheld.** `meaning_line(..., with_date=False)` feeds the LLM; the dated variant goes to `header_lines`, which already prints `· as of <date>` from the committed row and cannot get it wrong. `Date:` is gone from the user message, replaced by a coarse `It is September 2026.` anchor that keeps a topical column oriented without handing over a precise date to restate. The few-shot's date is gone from both halves of the example, and the system prompt now states *"NEVER write a calendar date"* and *"state the reading EXACTLY as given"*.
    - **The output is validated.** `facts_ok(body, risk, pct)` rejects any ISO date, any 2-decimal reading that is not the handed-in one, and any percentage tied to *extended* / *history* / *percentile* language that is not the handed-in percentile. A rejection triggers one retry naming the specific problem, then degrades to the non-LLM short post. **A facts failure is never waived** — the pre-existing "short but usable" tolerance explicitly re-checks the facts, because a duller post is always cheaper than a wrong one.

    One negative result from building it, recorded because it shaped the design: a fixed ±90-character context window **does not work**. The reading tie-in paragraph sits next to the macro paragraph, so the window around *"0.25 percentage points"* reached back into *"...more extended than 32% of its own history"* and condemned a central-bank rate move as a mis-stated reading. My own test caught it. The unit that actually decides whether a number is presented *as the reading* is the **sentence**, so `_sentence_around` scopes the check that way; unrelated decimals and percentages in neighbouring sentences now pass.

    Verified end to end by injecting the real body through `BOT_FAKE_LLM_TEXT`: the correct version passes with `facts ✓`, and the version carrying `2026-03-14` is rejected, retried once, and refused — `body unusable ... — degrading`. Ten new checks in `bot/test_assembly.py`, including the exact regression string and an assertion that the waiver path re-checks facts. Not validated, and stated as a limitation: the **band word** (`BELOW MID` and friends) is handed to the LLM and is not checked against `level_word`.

44. **The heat map's cold end was as wrong as v2's hot end had been, and by the same mistake.** Decision 42 tightened the red to the top 7% and left the blue alone. The blue then opened at azure and reached teal only at 0.45, so **0.00–0.40 — 29.0% of all history, 1,540 days — rendered as one flat marine blue**: 0.05 and 0.38 looked identical while meaning opposite things. Reserving one end for the extremes and not the other was an inconsistency, not a design.

    Deep marine is now the mirror of deep red, sized to match:

    | intense colour | band | share of the 5,307-day tape |
    |---|---|---|
    | deep marine | ≤ 0.12 | **6.09%** |
    | intense red | ≥ 0.95 | **7.22%** |

    Measured against the lows the bottom gate names, so the extremes keep the strongest colour: 2022-11-21 (0.050) and 2015-01-14 (0.080) are deep marine, 2026-06-30 (0.100) marine/azure, 2018-12-15 (0.170) blue-teal. Between the two ends the ramp is stretched — azure 0.12, teal 0.30, green 0.50, yellow 0.68, orange 0.84 — and stays monotonic cool-to-hot so warmth still reads as extension.

    Measured effect on the band that prompted it, as total RGB path length travelled:

    | span | before | after |
    |---|---|---|
    | 0.00–0.40 | 117 | **292** |
    | full 0–1 | 562 | **747** |

    The ten 26ths-buckets inside 0.00–0.40 now span an endpoint separation of 137 against 66 before.

45. **Near-1 readings really were falling outside the plot, and the 3% headroom only fixed one end.** The maintainer's original ">100" report was right about the symptom and I was only half right about the cause: it is a rendering artefact, but the fix in decision 41 padded the top only. The bottom had the identical problem and it matters more — the cycle lows are the most interesting readings on the chart and six days print 0.05, which drew on the frame's own floor. The y2 range is now **[-0.03, 1.03]** with `tick0 0, dtick 0.2`, so 1.00 sits at **97.2%** of plot height and 0.05 at **7.5%**, both clear of the frame, while the labels still read 0.0 … 1.0 and no tick is invented. **The tape was never wrong**: the series runs 5 to 100 inclusive with zero values above, as decision 41 records.

46. **The price line is white by default.** It was `#5b8def` — the same blue family the heat map uses for its cold end — so a blue price line and a blue "cheap" reading competed for one meaning, and under the heat map the eye had to separate two blues that meant different things. White belongs to neither end of the risk ramp, which is exactly why it suits the series that is not a risk reading. The legend swatch moved with it; `COL.blue` stays because other widgets use it.

47. **Both y axes now follow the visible window.** They were scaled to the whole series and pinned with `fixedrange`, so every short view was drawn against fifteen years of range. Measured, as decades of log price actually used by the axis:

    | window | price in window | axis before | axis now | decades of axis the data uses |
    |---|---|---|---|---|
    | All | $5 – $124,824 | $4 – $136,059 | $4 – $136,059 | 4.51 |
    | 5Y | $15,758 – $124,824 | $4 – $136,059 | $14,498 – $136,059 | 0.97 |
    | 1Y | $58,525 – $114,557 | $4 – $136,059 | $53,843 – $124,867 | 0.37 |
    | 6M | $58,525 – $82,257 | $4 – $136,059 | $53,843 – $89,660 | 0.22 |
    | 3M | $62,752 – $81,243 | $4 – $136,059 | $57,732 – $88,555 | 0.19 |

    On 6M the price occupied 0.22 of a 4.51-decade axis — under 5% of the panel height — which is why it drew as a straight line. The data was right and the picture said nothing.

    **The two axes are not treated the same, deliberately.** A price has no fixed meaning, so the visible range *is* the useful range and it auto-fits without limit. The extension axis is a **percentile**: `0.80` is supposed to mean "more extended than 80% of history" regardless of what else is on screen, and auto-fitting it without limit would let a quiet stretch between 0.28 and 0.40 fill the panel and read, at a glance, as a swing from calm to extreme. So it is widened to at least **`R_MIN_SPAN` = 0.35 of the scale**, about its own midpoint, then clamped inside [0, 1]; ticks always print real values and the step is chosen from the span on screen. Verified at all four positions: a 0.30–0.34 window opens to [0.145, 0.495]; 0.05–0.09 clamps to [0, 0.35]; 0.94–0.99 clamps to [0.65, 1.00]; a genuinely wide 0.10–0.90 window is left alone.

    This is a presentation choice with a real cost, so it is stated rather than buried: on a short window the extension line's *shape* is now readable and its *height* is no longer directly comparable to another window's. The axis labels are the guard. If that trade is ever judged wrong, one constant reverts it — `R_MIN_SPAN = 1` pins the axis back to the full scale.

### Ship gates (spec §12.1) — `model/v3/validate.py` exits non-zero on any failure

Full causal tape: **reach** (2013-12-04, 2017-12-17, 2021-04-14, 2021-11-10, 2024-03-13, 2025-10-06 in the top quintile of the tape up to that day) · **order** (2025-10-06 not below 2024-03-13 without a written G/Σ residual explanation) · **bottom** (2015-01-14, 2018-12-15, 2022-11-21 in the bottom quintile) · **low-vol rich** (synthetic: high V + falling κ does not lower Σ) · **collinearity** (max |r| among mapped pillars ≤ 0.80) · **nested baseline** (walk-forward Spearman of −risk vs next-90-day return, 2014 → embargo, beats Mayer percentile alone, MVRV percentile alone, 200-week-SMA distance; if MVRV alone wins, strip ornament pillars, never raise w_V above 0.40) · **rewrite probe** (compute twice, committed rows byte-stable; a v3.1 weight change does not touch `v3.0.jsonl`). Second table, holdout year only, no parameter chosen from it.

### P9 input-exclusion table (spec §8.4, Table 6)

Gate 1 *manipulable*: can a third party move the series without moving BTC economic reality? Gate 2 *strippable*: if this vendor goes dark, is there a second public construction within 30 days? Gate 3 *fad*: would it exist and mean the same thing if BTC is mainstream in 2032?

| Series | Role | G1 manipulable | G2 strippable | G3 fad | Verdict |
|---|---|---|---|---|---|
| `PriceUSD` | V, G, T, M, Σ | no (composite EOD) | no — Coinbase/Kraken daily close as `price_alt` | no | **required** |
| `CapMVRVCur` | V | no | partial — any UTXO-indexing node can rebuild realized cap; no second *free daily* publisher verified yet | no | **required**; open item: name a second public construction before v3.1 |
| `CapMrktCurUSD`, `SplyCur` | M (thermocap ratio), realized cap = Mcap/MVRV | no | no — trivially rebuildable | no | **required** |
| `IssTotUSD`, `FeeTotNtv` | M (thermocap) | no (protocol issuance; fees are on-chain) | no — any node | no | **required** |
| `IssTotNtv` | none in v3.0 (was for subsidy-normalized Puell) | no | no | no | fetched and hashed per §15.3; **not consumed** — see open question |
| Puell (`IssTotUSD` / 365-day mean) | — | no | no | no | **dropped 2026-09-11**: halving artifact; every "fix" is either a cliff or `P/SMA365` |
| `FlowInExNtv` ratio ("RHODL" proxy) | v2 D | partial | **fails** — depends on Coin Metrics' custodian universe | — | **excluded** |
| Realized profit / loss, SOPR, LTH supply | would be F | no | **fails** — paid vendors only, no public fallback found | no | **not available**; F dark |
| NUPL | — | — | — | — | **excluded**: `1 − 1/MVRV`, a V alias (P1) |
| Supply-in-profit from 4-year price percentile | v2 `sip` | — | — | — | **excluded**: not supply-in-profit |
| Google Trends, social volume, Fear & Greed | — | **fails** | **fails** | **fails** | **excluded** |
| Hashrate, difficulty, active addresses, tx counts | — | mixed | no | active addresses lost cycle signal after 2018 | **excluded** from v3.0 (collinear with miner revenue / stale signal) |
| US spot-ETF 20-day net flow | v3.1 candidate | low | partial — several public trackers, no single canonical source | plausible survivor | **paper only**; cap 0.03, ≥ 400 live days, ablation required |
| Perpetual funding (multi-venue median) | — | venue-specific | partial | leverage regime may change | **reject-or-lab**, not backlog |
| DXY / real-rate 60-day change | — | no | no | no | **reject-or-lab**: macro tilt, easy to overfit; not backlog |

### Residual limitations (spec §14) — v3 still cannot claim

- Three and a half cycles is not a distribution; under ETFs, fiscal dominance or a ban "rich vs history" may stop describing subsequent returns.
- `risk100 = 82` means "more extended than 82 % of causal history", not "82 % chance of a crash". No implied probabilities without a separately calibrated layer with its own Brier score.
- On-chain latency and restatement: the frozen tape plus a restatement file is a mitigation, not a cure.
- Growth-regression specification risk: a power law in days-since-genesis is a model; a broken log-linear with an ETF-era slope change would mis-rank the next ATH. Huber and the 90-day hold reduce drama; they do not pick the true trend. G at 2013-12-04 is an early-sample fit (1,201 closes, one prior cycle).
- No leverage / liquidity / options surface in v3.0; fragility from compressed spot vol is a proxy. An options-led flush can still surprise Σ.
- M is a single series in v3.0 (`mctc_mod`) at weight 0.10.
- Σ takes V as an input by construction; V–Σ correlation is measured in Phase 2, not assumed away.
- Policy-layer path dependence: dynamic-DCA books beat or lose to flat DCA depending on the path. That is not evidence about the score.
- Closed competitors stay closed; live divergence from AlphaSquared is expected and is not by itself a v3 bug.
- Transparency over performance: no dark net in v3.0. Some interaction effects are left on the table on purpose.
- The 2025–26 holdout is design-contaminated. The first genuinely out-of-sample event is the next ATH after freeze.

---

## v2 — frozen 2026-09-11

`btc_risk_model_v2.py`, 11 logistic-mapped signals, EMA span 3. Tape frozen as
`series/v2_frozen.json`; defects documented in the roadmap §3. Still the public
gauge until v3.0 passes the gates. No further changes to v2 compute.

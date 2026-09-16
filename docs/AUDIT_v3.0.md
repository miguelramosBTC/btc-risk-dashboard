# v3.0 internal audit — defects, robustness, principles, spec fidelity

Date: 2026-09-11 · Tape audited: `series/v3.0.jsonl`, 5,590 rows, 2011-02-02 → **2026-05-23**
Gates: 7/7 (`python3 -m model.v3.validate`, exit 0) · Tests: 67/67

**Scope note.** Defect class G (documentation vs code) is deferred by decision and is
*not* closed — see P6 below. The tape ends 2026-05-23 because the sandbox cannot reach
the Coin Metrics API; the holdout stays **unsigned** and no claim below extends past
that date.

---

## 1. Findings that need a decision

### Finding 1 — the band is ±1 point. The spec's own formula cannot produce the range the spec predicts.

§7.5 says *"Publish risk100 ± 10 s_t"* and, three lines later, *"The band 10 s_t will
typically be ±6 to ±15 points."* Those two statements are incompatible. `s_t` is the
sample standard deviation of the family scores, each in [0, 1]; the agreement term in
the same paragraph, `clip(1 − 2 s_t, 0, 1)`, only makes sense in those units. The
standard deviation of numbers in [0, 1] cannot exceed 0.5, so `10 s_t` cannot exceed
±5 and is in practice ±1.

Measured on the 4-family rows:

| Reading | median | p10 | p90 | max |
|---|---|---|---|---|
| `10 · s_t` (implemented, literal) | **±1** | ±1 | ±2 | ±3 |
| `100 · s_t` (one sd in score points) | **±12** | ±7 | ±19 | ±30 |

`100 · s_t` reproduces the spec's stated "±6 to ±15" almost exactly. The literal
reading produces the fake precision §9.5 exists to forbid: a ±1 band on a composite
whose families move by points when a single Coin Metrics field revises.

I implemented the literal text and did **not** change it. The band feeds no ship gate
(reach, order, bottom, low-vol, collinearity and baseline all read `risk01` only), so
correcting it changes no gate result — only the published uncertainty. **Recommend
`100 · s_t`; needs your word before I touch a published output.**

### Finding 2 — §9 operational requirements that are specified and not implemented

| §9 requirement | Status |
|---|---|
| 9.1 `stale = 1` if **any** required field is older than 36 h | **GAP.** `stale` tracks only the thermocap inputs. Injecting a 6-day `CapMVRVCur` hole darkens V (`n_live` 4→3) but leaves `stale = 0`. |
| 9.2 persist CSV vintage **and** API vintage on the row | **MISSING** |
| 9.2 `price_alt` second source, flag if \|Δ\|/price > 3 % | **MISSING** |
| 9.2 page the maintainer after 7 days of a missing field | **PARTIAL** — health check alerts on lag and staleness, no 7-day escalation |
| 9.1 halving constants present, not an input | OK |
| 9.1 no MVRV interpolation | OK — a hole darkens the family instead |
| 9.3 nominal USD only, no `risk_real` | OK |
| 9.4 version bump discipline | OK — changelog + parallel tape |
| 9.5 integer + band stored | OK (display stays 0–1 by decision) |

The staleness gap is the one with teeth: a vendor outage in MVRV currently renormalises
the blend silently and the row does not say the tape was degraded.

---

## 2. Defect closure (classes A–F, H)

### A — incorrect or misnamed constructions: **CLOSED**
`mvrvz`, `term`, `sip`, `rhodl` are absent from the graph and a test asserts the graph
contains only `{price, mvrv, mayer, mctc_mod, thermo_stale_days, dd, sigma30,
sigma365, kappa}`. The `term` identity is now an executable test: residual max
2.2e-5, which is *exactly* the feed's own rounding of `CapMrktCurUSD` against
`P × SplyCur`. `mvrv_z_correct` exists as a diagnostic only and uses `std(MVRV)`.
Table 2 of the roadmap reproduces to the digit (4.72 / 7.70 / 9.88 / 0.94).

### B — collinear linear blend: **IMPROVED, NOT ELIMINATED**
Honest numbers, because this is the defect most easily declared fixed by redefinition:

| Measure | v2 | v3 |
|---|---|---|
| effective rank (participation ratio) | 2–3 of 11 | **1.59 of 4** |
| variance in PC1 | — | **77.8 %** |
| corr(Δrisk, Δlog price) | 0.769 | **0.430** |
| max \|r\| on mapped levels | 0.97 | 0.846 (V–G) |
| max partial \|r(Δs_i, Δs_j \| Δlog P)\| | — | 0.526 (V–T) |

As a *fraction* of its families v3 is more diverse (1.59/4 = 40 % vs ≈ 23 %), and the
price-coupling is nearly halved. But in absolute terms the family vector carries
**fewer** independent dimensions than v2's did, and one component still explains 78 %
of it. That is the honest state: v3 removed a fake diversifier (`rhodl`) rather than
adding a real one, and F is dark. §8.5's literal gate (max mapped \|r\| ≤ 0.80) is
**not** met on levels — it was replaced by the partial gate on 2026-09-11 for reasons
measured at the time (raw Δ is mostly Δlog P). That replacement is approved and
logged, but it should not be described as "v3 passes the roadmap's collinearity gate".

### C — frozen absolute maps: **CLOSED in mechanism, PARTIAL in consequence**
The maps are expanding/4-year empirical CDFs; no logistic or `PARAMS` table survives
anywhere in the official path (AST-checked with docstrings stripped). The 2025 ATH
moves 0.556 → **0.660**, causal rank 0.820.

The audit's measured consequences, rechecked:

| | v2 | v3 |
|---|---|---|
| last day ≥ 0.70 | 2021-05-09 | **2025-07-28** |
| days ≥ 0.70 | 452 (7.9 %) | **765 (13.7 %)** |
| last day ≥ 0.80 | 2021-03-15 | 2021-04-22 |
| days ≥ 0.80 | 162 (2.8 %) | 253 (4.5 %) |
| cycle peaks 2017 → 2024-26 | 0.902 → 0.676 | 0.890 → **0.740** |

The red zone at ≥ 0.70 is revived and reaches into 2025. The ≥ 0.80 zone is still
effectively extinct after April 2021, and printed peaks still decline across cycles
(0.890 → 0.870 → 0.740). Rank-based reach passes at every high; absolute-level
compression is reduced, not removed. A distribution rule written at ≥ 0.80 would still
not have fired in 2025.

Mean risk by fraction-of-trailing-ATH (v2: 0.14 at ≤ 10 %, 0.70 at ATH):

| band | v2 | v3 |
|---|---|---|
| 0–15 % of ATH | 0.139 | 0.220 |
| 85–101 % of ATH | 0.644 | 0.696 |

Still close to monotone in distance-from-ATH. The slope is slightly flatter; the
character is not gone.

### D — era-brittle sleeves: **CLOSED**
Puell dropped entirely (both §7.2 halving rules measured as new defects: the literal
×2 rule halves Puell overnight at H+365; the subsidy-normalised form is `P/SMA365`,
Spearman 0.972). Raw `mctc` level gone, `fees` gone, `ATH^1.5` gone, and `T_dd`
deleted after it was shown to sit on the clip on all 197 new-ATH days.

### E — calibration and validation: **CLOSED in method, INCOMPLETE in evidence**
No `ANCHORS`, no fitted centres. Walk-forward nested baseline on a common sample with
a block bootstrap, embargoed holdout, rewrite probe, all exiting non-zero on failure.
The incompleteness is factual, not methodological: the holdout covers 255 of 365 days,
the June 2026 low is absent, and the year is labelled *design-contaminated* because
expanding CDFs and Σ were motivated by v2's 2025 failure.

### F — three different "current risk" objects: **CLOSED in the model, OPEN in production**
One daily object with `input_hash`, `git_sha` and the growth parameters on every row;
`heldSubs` appears nowhere in `model/` or `etl/`. The live site still serves v2 and
still contains the three-object hack — that is the Phase 5 cutover, deliberately not
merged.

### H — what v2 was not measuring: **PARTIAL, as the spec intends**
Σ adds a realised-volatility regime and the fragility interaction. Leverage, liquidity,
ETF flow and the options surface remain unmeasured; §14 says so and `docs/CHANGELOG_v3.md`
carries it. Nothing here claims otherwise.

---

## 3. Robustness tests (§4)

| Test | Result |
|---|---|
| **Reach** | PASS at all six highs — 2013-12-04 **0.949**, 2017-12-17 **1.000**, 2021-04-14 **0.988**, 2021-11-10 **0.927**, 2024-03-13 **0.865**, 2025-10-06 **0.820** |
| **Order** | 2025-10-06 (0.660) < 2024-03-13 (0.700), accepted as a §4 documented residual: higher high in dollars, lower high on Mayer (1.18 vs 1.85) and MVRV (2.29 vs 2.75); G +0.047 and Σ +0.233 vote 2025, V −0.076 and T −0.306 vote 2024 |
| **Low-vol rich** | PASS — at fixed V, fragility rises 0.001 → 0.849 as κ falls; cheap-and-quiet stays < 0.10 |
| **Bottom** | PASS — 2015-01-14 **0.091**, 2018-12-15 **0.169**, 2022-11-21 **0.061** |

Reach is measured on the committed tape against each day's own causal history, never a
recompute. An *undocumented* order inversion still fails the gate.

---

## 4. Design principles P1–P10

| | Principle | Status |
|---|---|---|
| P1 | one idea, one sleeve | **PASS** — V, G, T carry one raw series each; Σ carries two (σ30, κ). Note: four live families, not five, because F is dark and M was demoted. |
| P2 | causal, era-relative maps | **PASS** — AST check, docstrings stripped: no logistic literal, no `PARAMS` table |
| P3 | rank, not a 2013 unit | **PASS** |
| P4 | quiet is not safe | **PASS** — fragility interaction, low-vol-rich gate |
| P5 | live number = historical number | **PASS in model** — one object, no `heldSubs`; production still v2 |
| P6 | name only what you compute | **FAIL / OPEN** — site copy still asserts eleven signals, five families, logistic transforms, four proxies, hashrate, active addresses (263 matches). Deferred by decision, but this is the one principle currently violated in the product. |
| P7 | policy is not the score | **PASS** — no book, threshold or DCA reference anywhere in `model/` |
| P8 | inspectable combiner | **PASS** — linear; no net, no ML dependency |
| P9 | exclude strippable / fad series | **PASS** — `FlowInExNtv` absent from NEED_V3; P9 table committed |
| P10 | freeze the tape | **PASS** — append-only, bootstrap refuses to overwrite, CI refuses a push that deletes a line |

---

## 5. Fidelity to §7 and §15

**Exact:** V blend 0.65/0.35 · G blend 0.70/0.30 · Huber with (a, b, fit_asof, n_fit)
on the row · Σ = 0.40 F_exp(σ30) + 0.60 F_exp(fragility) with fragility = V·(1 − F_exp(κ))
· F dark · linear renormalised combiner · EMA span 8 with y₀ = x₀ · CDF
`(#{x ≤ x_t} − 0.5)/|W|` clipped to [0.001, 0.999] · Nmin 400/200/1200 · confidence
formula §7.5 · NEED_V3 field list §15.3.

**Approved deviations, each logged in `docs/CHANGELOG_v3.md`:**

| Item | Spec | v3.0 |
|---|---|---|
| T | 0.55 Mayer + 0.45 F_exp(−dd) | Mayer alone |
| M | 0.50 Puell_adj + 0.50 mctc_mod, w 0.16 | demoted; `mctc_mod` published, does not vote |
| Weights | V .31 G .20 T .15 M .18 Σ .16 | V .31 G .24 T .15 Σ .20, sum **0.90**, 0.10 retired |
| G cadence | quarter-end *or* daily + 90-day freeze | 90-day refit-and-hold from 2013-10-30 |
| Collinearity gate | max mapped \|r\| ≤ 0.80 | partial \|r(Δs_i, Δs_j \| Δlog P)\| < 0.70 on {V, G, T} |

**Unapproved additions I made (flagging for the record):** confidence treats agreement
as undefined (contributing 0) when fewer than two families are live — without it the
lone-V rows of 2011 printed conf 6; and `IssTotNtv` is fetched and hashed per §15.3 but
consumed by nothing.

**Literal-but-probably-wrong:** the band, Finding 1.

---

## 6. What this audit does not establish

- Nothing past **2026-05-23**. The holdout is unsigned; 2026-06-30 prints `PENDING`.
- Rank is not probability. `risk100 = 82` means more extended than 82 % of causal
  history, nothing more.
- The 2025–26 window is design-contaminated. The first genuinely out-of-sample event is
  the next ATH after the tape is frozen.
- Four families sharing one dominant component (78 % in PC1) means the "multifactor"
  description is still generous. v3 is a *better-mapped, less duplicated* composite than
  v2, not an independent-factor model.

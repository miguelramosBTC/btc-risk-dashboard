"""v3 constitution — the values that only change with a version bump.

Every number here is a *constitution* item (docs/CHANGELOG_v3.md, "Constitution").
Changing any of them is a new schema_version and a new tape file. Nothing in this
module is fitted; nothing in this module is allowed to be tuned against the
2025-09-11 → 2026-09-10 holdout year.
"""

SCHEMA_VERSION = "v3.0"

# --- calendar ---------------------------------------------------------------
GENESIS = "2009-01-03"            # d_t = days since genesis (growth regression)
T0 = "2010-07-18"                 # first UTC day with valid PriceUSD and CapMVRVCur in the
                                  # Coin Metrics community feed; start of every expanding CDF

# --- required inputs (Coin Metrics community only; spec §15.3) --------------
NEED_V3 = ["PriceUSD", "CapMVRVCur", "CapMrktCurUSD", "SplyCur",
           "IssTotUSD", "IssTotNtv", "FeeTotNtv"]
# IssTotNtv is fetched and hashed per §15.3 but no v3.0 pillar consumes it.
# FlowInExNtv is deliberately absent (P9 gate 2). Never add it back.

# --- maps (spec §7.1, §15.1) -------------------------------------------------
NMIN_EXP = 400                    # expanding CDF inactive before 400 valid observations
NMIN_4Y = 200                     # 4-year CDF inactive before 200 valid observations
WINDOW_4Y_DAYS = 1461             # trailing calendar window [t-1460, t], inclusive of t
CDF_CLIP = (0.001, 0.999)         # clip after the -0.5 continuity correction, before blending

# --- raw series construction -------------------------------------------------
SMA_MAYER = 200                   # full window: a partial SMA is not SMA_200
MCTC_MEAN_DAYS = 730              # mctc_mod = log(M/thermo) - 730-day mean ...
MCTC_MIN_PERIODS = 180            # ... with min_periods 180, exactly as v2
VOL_SHORT = 30                    # sigma30 (Phase 2)
VOL_LONG = 365                    # sigma365 (Phase 2)

# --- pillar blends (spec §7.2) ------------------------------------------------
BLEND_V = (0.65, 0.35)            # F_exp(MVRV), F_4y(MVRV)
BLEND_G = (0.70, 0.30)            # F_exp(g),    F_4y(g)
# T = F_exp(mayer) alone. T_dd deleted 2026-09-11: with dd >= 0 always, -dd = 0 is the
# series maximum, so F_exp(-dd) sat on the 0.999 clip on EVERY new-ATH day (197 days,
# 3.7% of the tape) regardless of valuation -- the ATH**1.5 defect of v2 §3.4 with a
# smaller coefficient. Smoothing dd only postpones the clip. The trend budget stays
# 0.15 and is NOT recycled into another pillar. Any future crash bonus is a NEW key,
# one-sided, ceiling 0.5 at dd = 0 -- never a revival of T_dd.
BLEND_S = (0.40, 0.60)            # F_exp(sigma30), F_exp(fragility)
# M = F_exp(mctc_mod) alone (Puell dropped 2026-09-11)
# T = F_exp(mayer)   alone (T_dd  dropped 2026-09-11)
# mctc_mod is still COMPUTED and published as a diagnostic column; it does not vote.

THERMO_LOCF_MAX_DAYS = 3          # carry a missing fee/issuance print forward at most 3
                                  # calendar days; beyond that pillar M goes dark rather
                                  # than write an invented contribution into the cumsum

# --- combiner (spec §7.4) ----------------------------------------------------
# Families in the official blend. M was demoted on 2026-09-11: r(V,M) = 0.932
# full sample, 0.96 after 2024, 0.87 on first differences -- mctc_mod carries
# market cap in its numerator, so it is a second filter of V, not a fourth
# family. Changing the map cannot fix a shared construction.
#
# The 0.10 family budget is RETIRED, not recycled (same rule as T_dd). It could
# have become a sub-weight inside V, but V's internal blend (0.65/0.35) is frozen
# by the embargo now that 2025-10-06 has been inspected, so the fallback branch
# applies: M is dropped outright and 0.10 stays unallocated.
WEIGHTS = {"V": 0.31, "G": 0.24, "T": 0.15, "S": 0.20}   # F inactive, M demoted
RETIRED_BUDGET = 0.10             # disclosed, never redistributed by editing the table
EMA_SPAN = 8                      # alpha = 2/(8+1) ~ 0.222, y0 = x0

# --- growth regression (spec §7.2; cadence per decision 3 of 2026-09-11) -----
NMIN_GROWTH = 1200                # closes required before the first fit
GROWTH_FIRST_FIT_ASOF = "2013-10-30"   # 1,201 closes; parameters live the same day
GROWTH_REFIT_DAYS = 90            # fixed refit-and-hold cadence, calendar days
HUBER_DELTA = 1.345               # textbook default; not grid-searched (embargo)

# --- reference only: NOT an input to compute_v3 in v3.0 ---------------------
HALVING_DATES = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"]

# --- v2 constructions that must never reappear in the official graph --------
V2_DEFECTS_NOT_IN_V3 = frozenset(
    {"term", "sip", "rhodl", "mctc", "mvrvz", "fees", "ath", "puell", "puell_adj",
     "nupl", "dd", "T_dd"}
)

_w = sum(WEIGHTS.values())
assert abs(_w + RETIRED_BUDGET - 1.0) < 1e-12, \
    f"family weights + retired budget must be 1.0, got {_w + RETIRED_BUDGET}"
assert abs(_w - 0.90) < 1e-12, "active weight is 0.90 and is disclosed as such"
assert all(abs(sum(b) - 1.0) < 1e-12 for b in (BLEND_V, BLEND_G, BLEND_S))

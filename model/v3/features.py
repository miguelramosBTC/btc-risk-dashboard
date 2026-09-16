"""Raw series for v3 — the corrected feature graph. No maps, no weights, no I/O.

This module is the Phase 1 answer to roadmap §6.1 ("kill") and §6.2 ("keep,
corrected"). It builds *only* the raw inputs the five live pillars consume:

    mvrv       CapMVRVCur, unchanged from the feed          -> pillar V
    mayer      P / SMA_200(P), full window                  -> pillar T  (T = Mayer alone)
    mctc_mod   log(Mcap/thermocap) - 730d mean, minp 180    -> pillar M
    sigma30 / sigma365 / kappa                              -> pillar Sigma

`dd` (drawdown from ATH) is computed for charts and diagnostics but is NOT a
pillar input: T_dd was deleted on 2026-09-11 because F_exp(-dd) sits on the
0.999 clip on every new-ATH day, which is v2's ATH**1.5 defect with a smaller
coefficient. See constants.py.

Deleted from v2 and not reconstructible here, by design (§6.1, Table 5):

    mvrvz   (Mcap - Rcap) / std(Mcap)  -- wrong denominator; if ever revived it
            must divide by std(MVRV) or std(M-R), and `mvrv_z_correct` below is
            the only construction allowed.
    term    P / (realized_price * 21e6 / supply) = MVRV * circulating_fraction.
            `term_identity_residual` exists solely so a test can prove the
            identity that v2 missed; it is not a feature.
    sip     4-year close percentile mislabelled supply-in-profit.
    rhodl   30d/365d FlowInExNtv ratio. FlowInExNtv is not even fetched in v3.
    mctc    raw log(Mcap/thermocap) level -- non-stationary; only the detrended
            sibling survives.
    fees    30/365 fee ratio as a pillar.
    ath     (P/cummax)**1.5 at weight 0.18.
    puell   dropped 2026-09-11: every halving fix is either a cliff at H+365 or
            algebraically P/SMA365(P).

Thermocap (§6.2): cumsum(IssTotUSD + FeeTotNtv * P) where a missing fee day is
carried forward from the last valid day, **not** filled with zero. v2's
`fillna(0)` silently treats a data hole as "no fees were paid", which depresses
thermocap forever after (a cumulative sum never forgets) and therefore inflates
log(Mcap/thermocap) for the rest of the series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constants import (MCTC_MEAN_DAYS, MCTC_MIN_PERIODS, NEED_V3, SMA_MAYER,
                        T0, THERMO_LOCF_MAX_DAYS, VOL_LONG, VOL_SHORT)

__all__ = [
    "input_stale_days",
    "prepare_frame",
    "thermocap",
    "build_raw",
    "mvrv_z_correct",
    "term_identity_residual",
]


def prepare_frame(df: pd.DataFrame, t0: str = T0) -> pd.DataFrame:
    """Validate, sort and date-index a Coin Metrics frame; cut to t0 onward.

    Requires every NEED_V3 column to exist (a missing column is a pipeline bug,
    not a hole to paper over) and rejects duplicate or unsorted dates.
    """
    missing = [c for c in NEED_V3 if c not in df.columns]
    if missing:
        raise KeyError(f"input is missing required columns: {missing}")
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"]).dt.tz_localize(None).dt.normalize()
    out = out.sort_values("time")
    if out["time"].duplicated().any():
        raise ValueError("duplicate dates in input frame")
    out = out.set_index("time")
    out = out.loc[out.index >= pd.Timestamp(t0)]
    out = out[out["PriceUSD"].notna()]
    for c in NEED_V3:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out[NEED_V3]


def input_stale_days(df: pd.DataFrame) -> pd.Series:
    """Age, in days, of the OLDEST required field on each date.

    §9.1 says stale = 1 when *any* required on-chain field is older than 36 h.
    Tracking only the thermocap inputs (the pre-2026-09-12 behaviour) meant a
    `CapMVRVCur` outage darkened pillar V, renormalised the blend and published
    the row with stale = 0 -- a degraded tape that did not say so. This counts
    every NEED_V3 field.
    """
    idx = pd.Series(df.index, index=df.index)
    ages = pd.DataFrame(index=df.index)
    for c in NEED_V3:
        # date of the last valid print of this field, carried forward
        last = idx.where(df[c].notna()).ffill()
        ages[c] = (idx - last).dt.days
    return ages.max(axis=1).fillna(0).astype(int)


def thermocap(iss_usd: pd.Series, fee_ntv: pd.Series, price: pd.Series,
              max_locf_days: int = THERMO_LOCF_MAX_DAYS):
    """Cumulative miner revenue in USD, with a bounded last-observation-carry-forward.

    Returns ``(thermo, stale_days)``.

    A cumulative sum cannot skip a day: whatever is written for a missing print is
    carried in the total forever. v2 wrote ``fillna(0)``, i.e. "no fees were paid",
    which depresses thermocap permanently and inflates log(Mcap/thermocap) for the
    rest of the series. v3 instead:

    * carries the last valid print forward for at most ``max_locf_days`` calendar
      days — a short vendor hole is estimated, not invented as zero;
    * keeps counting staleness past that bound, and ``build_raw`` sets pillar M to
      NaN on those days so the signal **drops out of the renormalised blend**
      instead of publishing a number that rests on a guess.

    The running total still uses the carried value beyond the bound (arithmetic has
    to put *something* in the sum, and a zero is the one choice known to be wrong);
    the honest handling is that nothing is published from it while it is stale. If
    the vendor later restates, new rows pick up the corrected series — committed
    rows are never rewritten.

    Leading NaNs before the first valid observation have nothing to carry forward
    and contribute 0 for that day only.
    """
    fee, iss = fee_ntv.ffill(), iss_usd.ffill()
    daily = iss.fillna(0.0) + (fee * price).fillna(0.0)

    missing = iss_usd.isna() | fee_ntv.isna()
    # consecutive-day counter, reset on every valid print
    grp = (~missing).cumsum()
    stale_days = missing.groupby(grp).cumsum().astype(int)
    stale_days[~missing] = 0
    return daily.cumsum(), stale_days


def build_raw(df: pd.DataFrame) -> pd.DataFrame:
    """Every raw series the five v3 pillars consume, and nothing else."""
    P = df["PriceUSD"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["price"] = P

    # --- V ------------------------------------------------------------------
    out["mvrv"] = df["CapMVRVCur"].astype(float)

    # --- T ------------------------------------------------------------------
    # Full 200-day window: a 50-day partial average is not SMA_200, and with an
    # expanding-CDF map a warmup artefact would be ranked as if it were signal.
    out["mayer"] = P / P.rolling(SMA_MAYER, min_periods=SMA_MAYER).mean()

    # --- M ------------------------------------------------------------------
    thermo, stale = thermocap(df["IssTotUSD"].astype(float),
                              df["FeeTotNtv"].astype(float), P)
    mcap = df["CapMrktCurUSD"].astype(float)
    mctc_log = np.log(mcap / thermo)
    mctc_mod = mctc_log - mctc_log.rolling(
        MCTC_MEAN_DAYS, min_periods=MCTC_MIN_PERIODS).mean()
    out["mctc_mod"] = mctc_mod.where(stale <= THERMO_LOCF_MAX_DAYS)
    out["thermo_stale_days"] = stale

    # --- staleness of every required field (§9.1), published on the row -----
    out["input_stale_days"] = input_stale_days(df)

    # --- diagnostics only: dd is charted, never mapped into a pillar --------
    out["dd"] = 1.0 - P / P.cummax()

    # --- Sigma (raw only; fragility needs V and is assembled in compute) -----
    lr = np.log(P).diff()
    out["sigma30"] = lr.rolling(VOL_SHORT, min_periods=VOL_SHORT).std()
    out["sigma365"] = lr.rolling(VOL_LONG, min_periods=VOL_LONG).std()
    out["kappa"] = out["sigma30"] / out["sigma365"]

    return out


# --------------------------------------------------------------------------- #
#  Diagnostics — not features. Nothing below feeds the official score.
# --------------------------------------------------------------------------- #
def mvrv_z_correct(df: pd.DataFrame) -> pd.Series:
    """The MVRV z-score v2 should have computed, if it is ever revived.

    z = (MVRV - expanding mean MVRV) / expanding std MVRV. v2 divided
    (Mcap - Rcap) by the expanding std of *market cap*, so the denominator grew
    with the asset and later cycles were flattered. Kept here as the reference
    construction and as the subject of a unit test; not a v3 feature.
    """
    mvrv = df["CapMVRVCur"].astype(float)
    return (mvrv - mvrv.expanding(min_periods=200).mean()) / \
        mvrv.expanding(min_periods=200).std()


def term_identity_residual(df: pd.DataFrame) -> pd.Series:
    """max |term / (MVRV * circulating_fraction) - 1| material: the v2 bug, exposed.

    v2's "terminal price proxy" was
        term = P / (realized_price * 21e6 / supply)
    with realized_price = (Mcap/MVRV)/supply. Substituting:
        term = P * supply / (realized_price * 21e6)
             = MVRV * (supply / 21e6)
    i.e. MVRV times the circulating fraction (~0.94). This function returns the
    residual of that identity; a test asserts it is ~0, which is the test that
    would have stopped `term` from shipping as an independent signal at weight
    0.03 and correlating 0.89 with the MVRV sub-score.
    """
    P = df["PriceUSD"].astype(float)
    mvrv = df["CapMVRVCur"].astype(float)
    mcap = df["CapMrktCurUSD"].astype(float)
    sply = df["SplyCur"].astype(float)
    rprice = (mcap / mvrv) / sply
    term = P / (rprice * 21e6 / sply)
    return term / (mvrv * (sply / 21e6)) - 1.0

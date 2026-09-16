"""Phase 1 tests — correctness of the maps and the corrected feature graph.

Run:  python3 -m pytest model/v3/tests -q
Real-data tests use the Coin Metrics community CSV; set BTC_CSV to a local
snapshot, or they are skipped (so the suite still runs offline).

What these tests are for, in one line each:

  * causality      — a value at t never changes when the future is truncated
  * CDF convention — §15.1 to the digit, including the -0.5 correction and clip
  * warmup         — a pillar is NaN before Nmin, not quietly computed on 3 points
  * term identity  — the algebraic check that would have caught v2's `term`
  * thermocap LOCF — a fee hole must not permanently depress thermocap
  * deletions      — the killed v2 signals cannot reappear in the graph
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from model.v3 import features, maps
from model.v3.constants import (CDF_CLIP, NMIN_4Y, NMIN_EXP, SMA_MAYER,
                                V2_DEFECTS_NOT_IN_V3, WEIGHTS)

CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV),
                               reason=f"{CSV} not present")


@pytest.fixture(scope="module")
def df():
    raw = pd.read_csv(CSV, parse_dates=["time"])
    return features.prepare_frame(raw)


@pytest.fixture(scope="module")
def raw(df):
    return features.build_raw(df)


def _series(values, start="2011-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(np.asarray(values, dtype=float), index=idx)


# --------------------------------------------------------------------------- #
#  1. CDF convention (§15.1)
# --------------------------------------------------------------------------- #
def test_cdf_point_matches_spec_formula():
    w = [1, 2, 3, 4]
    # x = 3 -> 3 of 4 values <= 3 -> (3 - 0.5)/4 = 0.625
    assert maps.empirical_cdf_point(w, 3) == pytest.approx(0.625)
    # a new lifetime maximum must NOT map to exactly 1.0
    assert maps.empirical_cdf_point(w, 99) == pytest.approx(0.875)
    assert maps.empirical_cdf_point(w, 99) < 1.0


def test_cdf_clipped_to_spec_bounds():
    lo, hi = CDF_CLIP
    big = list(range(5000))
    assert maps.empirical_cdf_point(big, -1) == pytest.approx(lo)
    assert maps.empirical_cdf_point(big, 10**9) == pytest.approx(hi)


def test_new_max_never_saturates_on_short_window():
    x = _series(np.arange(1, NMIN_EXP + 51))      # strictly increasing
    f = maps.expanding_cdf(x)
    live = f.dropna()
    assert len(live) == 51          # live from index NMIN_EXP-1 inclusive
    assert (live < 1.0).all() and (live <= CDF_CLIP[1] + 1e-12).all()


def test_expanding_cdf_matches_reference_implementation():
    rng = np.random.default_rng(7)
    x = _series(rng.normal(size=NMIN_EXP + 300))
    fast = maps.expanding_cdf(x)
    for i in (NMIN_EXP - 1, NMIN_EXP, NMIN_EXP + 5, len(x) - 1):
        ref = maps.empirical_cdf_point(x.to_numpy()[: i + 1], x.iloc[i])
        got = fast.iloc[i]
        if np.isnan(ref):
            assert np.isnan(got)
        else:
            assert got == pytest.approx(ref, abs=1e-12)


def test_ties_counted_as_less_or_equal():
    # five identical values then the same value again: all <= -> (6-0.5)/6
    x = _series([2.0] * (NMIN_EXP + 1))
    f = maps.expanding_cdf(x)
    n = NMIN_EXP
    assert f.iloc[n] == pytest.approx((n + 1 - 0.5) / (n + 1))


# --------------------------------------------------------------------------- #
#  2. Warmup — inactive, not approximated
# --------------------------------------------------------------------------- #
def test_expanding_cdf_inactive_before_nmin():
    x = _series(np.arange(NMIN_EXP + 10))
    f = maps.expanding_cdf(x)
    assert f.iloc[: NMIN_EXP - 1].isna().all()
    assert f.iloc[NMIN_EXP - 1:].notna().all()


def test_rolling_4y_inactive_before_nmin():
    x = _series(np.arange(NMIN_4Y + 10))
    f = maps.rolling_cdf_4y(x)
    assert f.iloc[: NMIN_4Y - 1].isna().all()
    assert f.iloc[NMIN_4Y - 1:].notna().all()


def test_rolling_4y_window_is_calendar_not_row_count():
    """The 4y window is 1461 *dates*, so a gappy series cannot reach further back."""
    # dense series: window must drop exactly the rows older than 1460 days
    idx = pd.date_range("2015-01-01", periods=1600, freq="D")
    rng = np.random.default_rng(19)
    x = pd.Series(rng.normal(size=1600), index=idx)
    f = maps.rolling_cdf_4y(x)
    w = x.loc[x.index[-1] - pd.Timedelta(days=1460):]
    assert len(w) == 1461
    assert f.iloc[-1] == pytest.approx(((w <= x.iloc[-1]).sum() - 0.5) / len(w))

    # gappy series: same 1461-day span, fewer observations in it. A row-count
    # window would silently reach back ~4.5 years here; a calendar one cannot.
    gap = x.drop(x.index[200:700])
    fg = maps.rolling_cdf_4y(gap)
    wg = gap.loc[gap.index[-1] - pd.Timedelta(days=1460):]
    assert len(wg) < 1461
    assert fg.iloc[-1] == pytest.approx(((wg <= gap.iloc[-1]).sum() - 0.5) / len(wg))
    assert gap.index[-1] - wg.index[0] <= pd.Timedelta(days=1460)


def test_nan_maps_to_nan_and_is_excluded_from_window():
    v = np.arange(NMIN_EXP + 10, dtype=float)
    v[NMIN_EXP + 2] = np.nan
    x = _series(v)
    f = maps.expanding_cdf(x)
    assert np.isnan(f.iloc[NMIN_EXP + 2])
    # the NaN did not enter later windows: denominator is count of valid values
    i = NMIN_EXP + 5
    ref = maps.empirical_cdf_point(v[: i + 1], v[i])
    assert f.iloc[i] == pytest.approx(ref, abs=1e-12)


def test_blend_runs_on_expanding_alone_when_4y_dark():
    a = _series([0.8] * 10)
    b = pd.Series([np.nan] * 10, index=a.index)
    out = maps.blend(a, b, (0.65, 0.35))
    assert np.allclose(out.to_numpy(), 0.8)
    both_dark = maps.blend(b, b, (0.65, 0.35))
    assert both_dark.isna().all()


# --------------------------------------------------------------------------- #
#  3. Causality — the check v2 never had
# --------------------------------------------------------------------------- #
def test_no_lookahead_synthetic():
    rng = np.random.default_rng(11)
    x = _series(np.cumsum(rng.normal(size=NMIN_EXP + 800)))
    maps.assert_no_lookahead(maps.expanding_cdf, x)
    maps.assert_no_lookahead(maps.rolling_cdf_4y, x)


def test_lookahead_detector_actually_fires():
    """A deliberately non-causal map must be caught, or the detector is theatre."""
    def full_sample_rank(x: pd.Series) -> pd.Series:
        return x.rank(pct=True)          # uses the whole sample at every date

    rng = np.random.default_rng(3)
    x = _series(np.cumsum(rng.normal(size=600)))
    with pytest.raises(AssertionError):
        maps.assert_no_lookahead(full_sample_rank, x)


@needs_csv
def test_no_lookahead_on_real_series(raw):
    for col in ("mvrv", "mayer", "mctc_mod"):
        s = raw[col].dropna()
        maps.assert_no_lookahead(maps.expanding_cdf, s)
        maps.assert_no_lookahead(maps.rolling_cdf_4y, s)


@needs_csv
def test_mayer_uses_full_window(raw, df):
    first = raw["mayer"].first_valid_index()
    assert first == df.index[SMA_MAYER - 1]


# --------------------------------------------------------------------------- #
#  4. The v2 defects — proven, not asserted in prose
# --------------------------------------------------------------------------- #
@needs_csv
def test_term_is_mvrv_in_costume(df):
    """v2's `term` == MVRV * circulating_fraction. This test would have caught it."""
    resid = features.term_identity_residual(df).dropna()
    # The identity is exact in algebra; the only slack is the feed's own rounding
    # of CapMrktCurUSD against PriceUSD * SplyCur. Assert both, so a future break
    # of the identity cannot hide behind "rounding".
    feed_round = (df["CapMrktCurUSD"] / (df["PriceUSD"] * df["SplyCur"]) - 1).dropna()
    assert np.abs(resid).max() < 1e-4, "term is not an independent signal"
    assert np.abs(resid).max() == pytest.approx(np.abs(feed_round).max(), rel=1e-3)
    assert np.abs(resid).median() < 1e-12


@needs_csv
def test_term_circulating_fraction_is_about_094(df):
    frac = (df["SplyCur"] / 21e6).iloc[-1]
    assert 0.90 < frac < 0.98      # the ~0.94 the audit quotes


@needs_csv
def test_v2_mvrvz_denominator_was_wrong(df):
    """The v2 z-score and the correct one diverge: not a cosmetic difference."""
    mcap = df["CapMrktCurUSD"].astype(float)
    rcap = mcap / df["CapMVRVCur"].astype(float)
    v2_z = (mcap - rcap) / mcap.expanding(min_periods=200).std()
    good = features.mvrv_z_correct(df)
    both = pd.concat([v2_z.rename("v2"), good.rename("ok")], axis=1).dropna()
    assert both["v2"].corr(both["ok"]) < 0.99

    # Roadmap Table 2, 2013-12-04 fixture. Column 2 is the z the code uses; it is
    # NOT raw MVRV (4.72). A later reader must not "align" them.
    mvrv_level = df["CapMVRVCur"].astype(float).loc["2013-12-04"]
    z_v2 = v2_z.loc["2013-12-04"]
    assert mvrv_level == pytest.approx(4.72, abs=0.01)
    assert z_v2 == pytest.approx(7.70, abs=0.01)
    assert z_v2 != pytest.approx(mvrv_level, rel=0.05)
    z_alt = ((mcap - rcap) / (mcap - rcap).expanding(min_periods=200).std()).loc["2013-12-04"]
    assert z_alt == pytest.approx(9.88, abs=0.01)
    mapped = 1.0 / (1.0 + np.exp(-(z_v2 - 3.20) / 1.60))
    assert mapped == pytest.approx(0.94, abs=0.005)      # the published v2 sub-score

    # The defect is the denominator: v2 divides by the scale of market cap, which
    # grows with the asset, so the same economic extremity prints smaller later.
    std_mcap = mcap.expanding(min_periods=200).std()
    std_mvrv = df["CapMVRVCur"].astype(float).expanding(min_periods=200).std()
    assert std_mcap.loc["2025-10-06"] / std_mcap.loc["2013-12-04"] > 100
    assert std_mvrv.loc["2025-10-06"] / std_mvrv.loc["2013-12-04"] < 1

    # Consequence: v2 compresses 2025 against 2013 harder than a scale-consistent
    # z of the same numerator does.
    mr = (mcap - rcap)
    z_mr = mr / mr.expanding(min_periods=200).std()
    r_v2 = v2_z.loc["2025-10-06"] / v2_z.loc["2013-12-04"]
    r_mr = z_mr.loc["2025-10-06"] / z_mr.loc["2013-12-04"]
    assert r_v2 < r_mr


@needs_csv
def test_thermocap_locf_differs_from_fillna_zero(df):
    """A fee hole filled with 0 permanently depresses a cumulative sum."""
    P = df["PriceUSD"].astype(float)
    iss, fee = df["IssTotUSD"].astype(float), df["FeeTotNtv"].astype(float)
    holed = fee.copy()
    holed.iloc[1000:1010] = np.nan          # ten-day vendor hole

    locf, _ = features.thermocap(iss, holed, P)
    zero = (iss.fillna(0) + (holed * P).fillna(0)).cumsum()
    clean, _ = features.thermocap(iss, fee, P)

    # zero-fill is biased low for the rest of the series; LOCF is far closer
    assert (zero.iloc[-1] < clean.iloc[-1])
    assert abs(locf.iloc[-1] - clean.iloc[-1]) < abs(zero.iloc[-1] - clean.iloc[-1])
    # and the bias never heals: the gap persists to the last row
    assert (clean.iloc[-1] - zero.iloc[-1]) > 0


@needs_csv
def test_thermocap_hole_moves_the_mapped_pillar(df):
    """The LOCF fix is material at the pillar level, not just in the cumsum."""
    P = df["PriceUSD"].astype(float)
    iss, fee = df["IssTotUSD"].astype(float), df["FeeTotNtv"].astype(float)
    holed = fee.copy()
    holed.iloc[1000:1010] = np.nan
    mcap = df["CapMrktCurUSD"].astype(float)

    def mctc_mod(thermo):
        lg = np.log(mcap / thermo)
        return lg - lg.rolling(730, min_periods=180).mean()

    good = mctc_mod(features.thermocap(iss, fee, P)[0])
    bad = mctc_mod((iss.fillna(0) + (holed * P).fillna(0)).cumsum())
    diff = (maps.expanding_cdf(good.dropna()) -
            maps.expanding_cdf(bad.dropna())).abs().max()
    assert diff > 0.0


# --------------------------------------------------------------------------- #
#  5. Deletions and constitution
# --------------------------------------------------------------------------- #
@needs_csv
def test_graph_contains_only_v3_features(raw):
    allowed = {"price", "mvrv", "mayer", "mctc_mod", "thermo_stale_days",
               "input_stale_days", "dd", "sigma30", "sigma365", "kappa"}
    assert set(raw.columns) == allowed
    # `dd` is present as a chart diagnostic; it must not be a *pillar* input.
    assert not (set(raw.columns) & (set(V2_DEFECTS_NOT_IN_V3) - {"dd"}))


def test_flow_in_ex_is_not_required():
    from model.v3.constants import NEED_V3
    assert "FlowInExNtv" not in NEED_V3


def test_weights_match_the_constitution():
    """M demoted 2026-09-11; its 0.10 is retired, not recycled."""
    from model.v3.constants import RETIRED_BUDGET
    assert WEIGHTS == {"V": 0.31, "G": 0.24, "T": 0.15, "S": 0.20}
    assert sum(WEIGHTS.values()) == pytest.approx(0.90)
    assert sum(WEIGHTS.values()) + RETIRED_BUDGET == pytest.approx(1.0)


def test_no_logistic_parameters_anywhere_in_package():
    """P2: the official path must not contain a frozen logistic map."""
    import pathlib
    import re

    logistic = re.compile(r"1(\.0)?/\(1(\.0)?\+np\.exp")
    table = re.compile(r"^\s*PARAMS\s*=", re.M)

    # the detectors must fire on a genuine violation, or they are decoration
    assert logistic.search("s=1.0/(1.0+np.exp(-(x-c)/b))")
    assert logistic.search("return 1/(1+np.exp(-(x-c)/b))".replace(" ", ""))
    assert table.search('PARAMS = {"mayer": (1.60, 0.55)}')

    pkg = pathlib.Path(features.__file__).parent
    for py in pkg.glob("*.py"):
        src = py.read_text()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        assert not logistic.search(code.replace(" ", "")), \
            f"{py.name} reintroduces a logistic map"
        assert not table.search(code), f"{py.name} reintroduces a parameter table"


# --------------------------------------------------------------------------- #
#  6. Decisions of 2026-09-11 — T_dd deleted, LOCF bounded
# --------------------------------------------------------------------------- #
def test_t_dd_would_have_sat_on_the_clip_at_every_ath():
    """Why T_dd was deleted, as an executable fact rather than a claim."""
    rng = np.random.default_rng(5)
    steps = np.abs(rng.normal(size=NMIN_EXP + 400)) * 0.01     # monotone rally
    price = _series(100 * np.exp(np.cumsum(steps)))
    dd = 1.0 - price / price.cummax()
    t_dd = maps.expanding_cdf(-dd).dropna()
    assert (dd == 0).all()                    # every day is a new ATH
    # ... and every day takes the maximum attainable score: (n-0.5)/n, clipped.
    n = np.arange(NMIN_EXP, NMIN_EXP + len(t_dd))
    ceiling = np.minimum((n - 0.5) / n, CDF_CLIP[1])
    assert np.allclose(t_dd.to_numpy(), ceiling)
    assert t_dd.min() > 0.99                  # never below the rail, ever


@needs_csv
def test_t_dd_is_not_a_pillar_input(raw):
    """T = Mayer alone. `dd` may be charted; it may not enter the blend."""
    from model.v3 import constants
    assert not hasattr(constants, "BLEND_T")
    assert "T_dd" in constants.V2_DEFECTS_NOT_IN_V3


def test_trend_budget_was_not_recycled():
    """Deleting T_dd must not move weight into another pillar."""
    assert WEIGHTS["T"] == 0.15
    assert WEIGHTS["V"] == 0.31 and WEIGHTS["G"] == 0.24 and WEIGHTS["S"] == 0.20


@needs_csv
def test_locf_bounded_and_pillar_goes_dark(df):
    """A hole longer than the bound darkens M instead of inventing a contribution."""
    from model.v3.constants import THERMO_LOCF_MAX_DAYS
    holed = df.copy()
    lo, hi = 3000, 3010                        # eleven-day vendor hole
    holed.iloc[lo:hi, holed.columns.get_loc("FeeTotNtv")] = np.nan

    raw_h = features.build_raw(holed)
    stale = raw_h["thermo_stale_days"]
    assert stale.iloc[lo:hi].max() == hi - lo
    dark = raw_h["mctc_mod"].iloc[lo:hi].isna()
    assert dark.sum() == (hi - lo) - THERMO_LOCF_MAX_DAYS   # first 3 days carried
    # M is live again once the feed returns ...
    clean = features.build_raw(df)["mctc_mod"]
    assert np.isfinite(raw_h["mctc_mod"].iloc[hi + 1])
    # ... but a cumsum never forgets: the carried estimate leaves a small permanent
    # trace. It is disclosed via thermo_stale_days on the row, not erased, and it is
    # orders of magnitude smaller than the zero-fill bug it replaces.
    trace = (raw_h["mctc_mod"] - clean).abs().dropna()
    assert 0 < trace.max() < 1e-3
    # Measured across hole types/lengths/eras, zero-fill is 3.7x to 42x worse than
    # bounded LOCF (fees 10d ~2019: 1.06e-4 vs 2.84e-5; issuance 10d: 1.09e-2 vs
    # 5.1e-4). Assert the direction, which is what the fix claims; the multiple
    # depends on the hole and is not a stable number to hard-code.
    zero_fill = (iss_fill_bug(df) - clean).abs().dropna()
    assert zero_fill.max() > trace.max()


def iss_fill_bug(df):
    """v2's thermocap under the same hole: fillna(0) instead of bounded LOCF."""
    P = df["PriceUSD"].astype(float)
    iss, fee = df["IssTotUSD"].astype(float), df["FeeTotNtv"].astype(float)
    holed = fee.copy()
    holed.iloc[3000:3011] = np.nan
    thermo = (iss.fillna(0) + (holed * P).fillna(0)).cumsum()
    lg = np.log(df["CapMrktCurUSD"].astype(float) / thermo)
    return lg - lg.rolling(730, min_periods=180).mean()


@needs_csv
def test_production_extract_unchanged_under_locf(df):
    """2026-09-10 production extract: LOCF and fillna(0) are bit-identical."""
    P = df["PriceUSD"].astype(float)
    iss, fee = df["IssTotUSD"].astype(float), df["FeeTotNtv"].astype(float)
    assert iss.isna().sum() == 0 and fee.isna().sum() == 0
    locf, stale = features.thermocap(iss, fee, P)
    zero = (iss.fillna(0) + (fee * P).fillna(0)).cumsum()
    assert stale.max() == 0
    assert np.array_equal(locf.to_numpy(), zero.to_numpy())

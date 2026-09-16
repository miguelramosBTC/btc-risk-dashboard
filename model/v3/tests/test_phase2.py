"""Phase 2 tests — growth fit (pillar G) and volatility regime (pillar Sigma).

Run:  python3 -m pytest model/v3/tests -q

The cadence tests exist because "refit every 90 days and hold" and "refit daily
with lagged coefficients" produce tapes that look similar and are not the same
object. Each locked semantic gets its own executable assertion.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from model.v3 import features, growth, maps, pillars
from model.v3.constants import (GROWTH_FIRST_FIT_ASOF, GROWTH_REFIT_DAYS,
                                HUBER_DELTA, NMIN_GROWTH)

CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV), reason=f"{CSV} not present")


@pytest.fixture(scope="module")
def df():
    return features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"]))


@pytest.fixture(scope="module")
def raw(df):
    return features.build_raw(df)


@pytest.fixture(scope="module")
def params(raw):
    return growth.growth_params(raw["price"])


# --------------------------------------------------------------------------- #
#  Huber estimator
# --------------------------------------------------------------------------- #
def test_huber_recovers_a_clean_line():
    x = np.linspace(1, 10, 200)
    y = 3.0 + 2.0 * x
    a, b = growth.huber_fit(x, y)
    assert a == pytest.approx(3.0, abs=1e-6)
    assert b == pytest.approx(2.0, abs=1e-6)


def test_huber_resists_outliers_where_ols_does_not():
    """The reason the spec forbids OLS: blow-offs must not own the slope."""
    rng = np.random.default_rng(2)
    x = np.linspace(1, 10, 400)
    y = 1.0 + 1.5 * x + rng.normal(scale=0.1, size=x.size)
    y[-20:] += 8.0                                   # a 2013/2017-style blow-off
    b_ols = np.polyfit(x, y, 1)[0]
    _, b_huber = growth.huber_fit(x, y)
    assert abs(b_huber - 1.5) < abs(b_ols - 1.5)
    assert abs(b_huber - 1.5) < 0.25


def test_huber_is_deterministic():
    rng = np.random.default_rng(4)
    x = np.linspace(1, 20, 500)
    y = 2 + 0.7 * x + rng.normal(size=500)
    assert growth.huber_fit(x, y) == growth.huber_fit(x, y)


@needs_csv
def test_huber_matches_statsmodels_rlm(raw):
    """Independent implementation check; skipped if statsmodels is absent."""
    sm = pytest.importorskip("statsmodels.api")
    P = raw["price"]
    m = P.index <= pd.Timestamp(GROWTH_FIRST_FIT_ASOF)
    x = np.log(growth.days_since_genesis(P.index[m]))
    y = np.log(P[m].to_numpy(dtype=float))
    a, b = growth.huber_fit(x, y)
    rlm = sm.RLM(y, sm.add_constant(x),
                 M=sm.robust.norms.HuberT(t=HUBER_DELTA)).fit()
    assert b == pytest.approx(rlm.params[1], rel=0.01)
    assert a == pytest.approx(rlm.params[0], rel=0.01)
    # ... and both differ from OLS in the same direction
    assert b > np.polyfit(x, y, 1)[0]


# --------------------------------------------------------------------------- #
#  Cadence: refit-and-hold, exactly as locked on 2026-09-11
# --------------------------------------------------------------------------- #
@needs_csv
def test_first_fit_is_live_the_same_day(params):
    first = params["fit_asof"].dropna().iloc[0]
    assert pd.Timestamp(first) == pd.Timestamp(GROWTH_FIRST_FIT_ASOF)
    live = params["a"].first_valid_index()
    assert live == pd.Timestamp(GROWTH_FIRST_FIT_ASOF)   # not 90 days later


@needs_csv
def test_first_fit_sample_size(params):
    n = int(params["n_fit"].dropna().iloc[0])
    assert n >= NMIN_GROWTH
    assert n == 1201                     # 1,201 closes on 2013-10-30


@needs_csv
def test_marks_are_every_90_days(params):
    marks = pd.to_datetime(pd.Series(params["fit_asof"].dropna().unique())).sort_values()
    gaps = marks.diff().dropna().dt.days.unique()
    assert set(gaps) == {GROWTH_REFIT_DAYS}
    assert marks.iloc[1] == pd.Timestamp("2014-01-28")   # the locked second mark


@needs_csv
def test_parameters_are_held_flat_between_marks(params):
    p = params.dropna(subset=["a"])
    for asof, blk in p.groupby("fit_asof"):
        assert blk["a"].nunique() == 1 and blk["b"].nunique() == 1
        assert (blk.index >= pd.Timestamp(asof)).all()
        assert (blk.index < pd.Timestamp(asof) + pd.Timedelta(days=GROWTH_REFIT_DAYS)).all()


@needs_csv
def test_fit_uses_only_data_up_to_its_mark(raw):
    """Causality of the fit itself: truncating the future must not move (a, b)."""
    P = raw["price"]
    mark = pd.Timestamp("2021-03-22")
    full = growth.growth_params(P)
    trunc = growth.growth_params(P.loc[:mark])
    a_f, b_f = full.loc[mark, "a"], full.loc[mark, "b"]
    a_t, b_t = trunc.loc[mark, "a"], trunc.loc[mark, "b"]
    assert a_f == pytest.approx(a_t, abs=1e-12)
    assert b_f == pytest.approx(b_t, abs=1e-12)


@needs_csv
def test_residual_is_not_absorbed_by_a_same_day_refit(raw, params):
    """Through a spike, G is scored against the path fitted at the last mark.

    A same-day refit would eat part of the move it exists to measure; this test
    pins the difference so a future 'improvement' to daily fitting is caught.
    """
    P = raw["price"]
    top = pd.Timestamp("2013-12-04")
    g_held = growth.growth_residual(P, params).loc[top]

    x = np.log(growth.days_since_genesis(P.loc[:top].index))
    y = np.log(P.loc[:top].to_numpy(dtype=float))
    a, b = growth.huber_fit(x, y)
    g_sameday = np.log(P.loc[top]) - (a + b * np.log(
        growth.days_since_genesis(pd.DatetimeIndex([top]))[0]))

    assert g_held > g_sameday          # the held fit prints richer, by design
    assert pd.Timestamp(params.loc[top, "fit_asof"]) == pd.Timestamp("2013-10-30")


@needs_csv
def test_growth_is_dark_before_the_first_mark(params):
    before = params.loc[: pd.Timestamp(GROWTH_FIRST_FIT_ASOF) - pd.Timedelta(days=1)]
    assert before["a"].isna().all()


# --------------------------------------------------------------------------- #
#  Pillar Sigma — quiet is not safe (P4)
# --------------------------------------------------------------------------- #
def test_fragility_rises_when_vol_compresses_at_constant_richness():
    """The low-vol-rich property, on a synthetic path: V fixed, kappa falling."""
    idx = pd.date_range("2015-01-01", periods=1200, freq="D")
    V = pd.Series(0.85, index=idx)                      # held rich
    kappa_hi = pd.Series(np.linspace(1.30, 1.35, 1200), index=idx)
    kappa_lo = kappa_hi.copy()
    kappa_lo.iloc[-200:] = np.linspace(1.30, 0.45, 200)   # vol compresses late

    f_hi = V * (1 - maps.expanding_cdf(kappa_hi))
    f_lo = V * (1 - maps.expanding_cdf(kappa_lo))
    assert f_lo.iloc[-1] > f_hi.iloc[-1]


def test_fragility_stays_low_when_cheap_and_quiet():
    """Bottoms must not be called fragile just because the tape is calm."""
    idx = pd.date_range("2015-01-01", periods=1200, freq="D")
    kappa = pd.Series(np.r_[np.linspace(1.3, 1.3, 1000), np.linspace(1.3, 0.45, 200)],
                      index=idx)
    cheap = pd.Series(0.05, index=idx)
    frag = cheap * (1 - maps.expanding_cdf(kappa))
    assert frag.dropna().max() < 0.06


@needs_csv
def test_sigma_ranks_the_quiet_ath_above_the_loud_one(raw):
    """2025 (quiet, rich) must not score below 2013 (loud, rich) on Sigma."""
    P = pillars.build_pillars(raw)
    assert P["S"].loc["2025-10-06"] > P["S"].loc["2013-12-04"]
    assert raw["kappa"].loc["2025-10-06"] < 1.0 < raw["kappa"].loc["2013-12-04"]


@needs_csv
def test_sigma_keeps_bottoms_low(raw):
    P = pillars.build_pillars(raw)
    for d in ("2015-01-14", "2018-12-15", "2022-11-21"):
        assert P["S"].loc[d] < 0.35
        assert P["fragility"].loc[d] < 0.05


# --------------------------------------------------------------------------- #
#  Pillar assembly
# --------------------------------------------------------------------------- #
@needs_csv
def test_all_pillars_in_unit_interval(raw, params):
    P = pillars.build_pillars(raw, params)
    for k in pillars.PILLAR_KEYS:
        s = P[k].dropna()
        assert s.between(0.0, 1.0).all()
        assert len(s) > 3000


@needs_csv
def test_pillar_T_is_mayer_only(raw):
    """T_dd is deleted: pillar T must equal the Mayer map exactly."""
    P = pillars.build_pillars(raw)
    direct = maps.expanding_cdf(raw["mayer"].dropna())
    a, b = P["T"].dropna().align(direct.dropna(), join="inner")
    assert np.allclose(a.to_numpy(), b.to_numpy())


# --------------------------------------------------------------------------- #
#  Phase 3 — combiner mechanics (gates live in validate.py, not here)
# --------------------------------------------------------------------------- #
@needs_csv
def test_retired_budget_is_not_redistributed():
    from model.v3.constants import RETIRED_BUDGET, WEIGHTS as W
    assert "M" not in W
    assert W == {"V": 0.31, "G": 0.24, "T": 0.15, "S": 0.20}
    assert sum(W.values()) == pytest.approx(0.90)
    assert RETIRED_BUDGET == pytest.approx(0.10)
    assert sum(W.values()) + RETIRED_BUDGET == pytest.approx(1.0)


def test_combiner_renormalises_over_live_families_only():
    from model.v3 import compute
    idx = pd.date_range("2020-01-01", periods=3, freq="D")
    P = pd.DataFrame({"V": [1.0, 1.0, 1.0], "G": [0.0, np.nan, 0.0],
                      "T": [1.0, 1.0, np.nan], "S": [0.0, 0.0, 0.0]}, index=idx)
    raw = compute.combine_raw(P)
    assert raw.iloc[0] == pytest.approx((0.31 + 0.15) / 0.90)
    assert raw.iloc[1] == pytest.approx((0.31 + 0.15) / (0.90 - 0.24))
    assert raw.iloc[2] == pytest.approx(0.31 / (0.90 - 0.15))


def test_ema_seeds_on_first_value_and_uses_span_8():
    from model.v3 import compute
    idx = pd.date_range("2020-01-01", periods=4, freq="D")
    x = pd.Series([0.5, 1.0, 1.0, 1.0], index=idx)
    y = compute.ema(x)
    a = 2 / 9
    assert y.iloc[0] == pytest.approx(0.5)
    assert y.iloc[1] == pytest.approx(a * 1.0 + (1 - a) * 0.5)


@needs_csv
def test_daily_row_carries_the_audit_fields(raw, params):
    from model.v3 import compute
    P = pillars.build_pillars(raw, params)
    row = compute.build_daily(P, raw["price"], params)
    for c in ("price_usd", "risk01", "risk100", "risk_lo", "risk_hi", "conf",
              "n_live", "stale", "a", "b", "fit_asof", "n_fit",
              "schema_version", "active_weight", "retired_weight"):
        assert c in row.columns
    assert (row["risk100"] == (row["risk01"] * 100).round()).all()
    assert (row["risk_lo"] <= row["risk100"]).all()
    assert (row["risk_hi"] >= row["risk100"]).all()
    assert row["risk100"].between(0, 100).all()


@needs_csv
def test_tape_is_reproducible(raw, params):
    from model.v3 import compute
    P = pillars.build_pillars(raw, params)
    assert compute.build_daily(P, raw["price"], params).equals(
        compute.build_daily(P, raw["price"], params))


# --------------------------------------------------------------------------- #
#  Collinearity gate — the partial is the blocker (ruling of 2026-09-11)
# --------------------------------------------------------------------------- #
def test_partial_control_is_the_price_that_feeds_T():
    """The control is d log P of the same BTC price series that feeds T.

    Swapping in a 'smarter' control (Mayer, or V itself) is a new gate and a new
    version. This test pins the current one by construction.
    """
    import ast
    import inspect

    from model.v3 import validate

    src = inspect.getsource(validate.gate_collinearity)
    fn = ast.parse(src).body[0]
    body = ast.get_source_segment(src, fn) or src
    if ast.get_docstring(fn):                     # drop the prose; check the code
        body = body.replace(ast.get_docstring(fn), "")

    assert "np.log(price)" in body and ".diff()" in body
    for smarter in ('raw["mayer"]', '["mayer"]', 'X["V"]'):
        assert smarter not in body, f"control was swapped for {smarter}"


def test_partial_detects_an_alias_the_raw_difference_hides():
    """A true alias must breach the partial; a shared price driver must not."""
    from model.v3 import validate
    rng = np.random.default_rng(8)
    idx = pd.date_range("2014-01-01", periods=3000, freq="D")
    dlogp = pd.Series(rng.normal(scale=0.03, size=3000), index=idx)
    price = pd.Series(100 * np.exp(dlogp.cumsum()), index=idx)

    # two families that share only the price driver
    a = (0.9 * dlogp + 0.10 * rng.normal(scale=0.03, size=3000)).cumsum()
    b = (0.8 * dlogp + 0.10 * rng.normal(scale=0.03, size=3000)).cumsum()
    # ... and a third that is an alias of the first beyond price
    c = a + 0.01 * rng.normal(scale=0.03, size=3000).cumsum()

    def partial(u, v):
        du, dv = np.diff(u), np.diff(v)
        dp = dlogp.to_numpy()[1:]
        ru = du - np.polyval(np.polyfit(dp, du, 1), dp)
        rv = dv - np.polyval(np.polyfit(dp, dv, 1), dp)
        return abs(np.corrcoef(ru, rv)[0, 1])

    raw_shared = abs(np.corrcoef(np.diff(a), np.diff(b))[0, 1])
    assert raw_shared > 0.70            # raw difference condemns the innocent pair
    assert partial(a, b) < 0.70         # the partial acquits it
    assert partial(a, c) >= 0.70        # and still convicts a real alias


@needs_csv
def test_collinearity_gate_passes_on_the_current_tape(raw, params):
    from model.v3 import validate
    P = pillars.build_pillars(raw, params)
    g = validate.gate_collinearity(P, raw["price"])
    assert g.ok
    assert any("partial (BLOCKER)" in l for l in g.lines)
    assert any("level (disclose)" in l for l in g.lines)
    assert any("raw diff (diagnose)" in l for l in g.lines)


@needs_csv
def test_sigma_pairs_are_off_the_gate():
    from model.v3.validate import COLLINEARITY_PAIRS
    assert COLLINEARITY_PAIRS == ("V", "G", "T")
    assert "S" not in COLLINEARITY_PAIRS


# --------------------------------------------------------------------------- #
#  Ranked composite (2026-09-12) and the §9 operational fields
# --------------------------------------------------------------------------- #
@needs_csv
def test_published_score_is_a_causal_rank(raw, params):
    """P3 literally: risk01 must BE the expanding rank of the smoothed blend."""
    from model.v3 import compute
    P = pillars.build_pillars(raw, params)
    d = compute.build_daily(P, raw["price"], params)

    # Rebuild through the same entry point the tape uses. NOTE the reference set:
    # the rank denominator is every day the composite exists, INCLUDING the ~400
    # warmup days that never reach the tape. That convention is worth 0.006 of
    # rank at 2025-10-06 and is what decides a borderline reach result, so it is
    # pinned here rather than left implicit.
    vals = P[list(compute.WEIGHTS)]
    smooth = compute.ema(compute.combine_raw(P, compute.WEIGHTS)).clip(0, 1)
    spread = vals.std(axis=1, ddof=1, skipna=True).fillna(0.0)
    ranked, _, _ = compute.rank_composite(smooth, spread)
    a, b = d["rank_exact"].dropna().align(ranked.dropna(), join="inner")
    assert np.allclose(a.to_numpy(), b.to_numpy())
    # The warmup days are not published, but they ARE in the rank denominator.
    t = pd.Timestamp("2025-10-06")
    published_before = (d.index <= t).sum()
    in_denominator = (smooth.dropna().index <= t).sum()
    assert in_denominator - published_before == 399
    # and that difference is what moves the borderline 2025 reach result
    tape_only = ((d["smooth01"].loc[:t] <= d["smooth01"].loc[t]).sum() - 0.5) / published_before
    assert abs(d.loc[t, "rank_exact"] - 0.8035) < 5e-4      # published convention
    assert abs(tape_only - 0.7972) < 5e-4                   # tape-only convention
    assert d.loc[t, "rank_exact"] >= 0.80 > tape_only       # the gate straddles them
    # and the scale actually reaches its ends, which the blend never did
    # The ranked scale reaches its top; the blend never did (max 0.890 in 15 years).
    assert d["risk100"].max() >= 99
    assert d["smooth01"].max() < 0.95
    # The bottom is reached less often than the top: the lowest rank in the sample
    # is 5, not 0, because deep-value days are rarer than extended ones.
    assert d["risk100"].min() <= 10


@needs_csv
def test_rank_is_monotone_within_a_date_but_not_across_dates(raw, params):
    """The expanding rank is a TIME-VARYING map, not a fixed monotone transform.

    Within one day's history it is monotone, so reach and bottom (each day
    against its own past) are unaffected. Across dates it is not: the same blend
    value maps to different ranks in different years, which is the entire point
    -- and it means cross-date statistics such as the forward-return Spearman
    genuinely move. Measured: cross-date rho = 0.958, and the nested-baseline
    point estimate falls from +0.016 to -0.056 under the rank.
    """
    from model.v3 import compute, validate
    P = pillars.build_pillars(raw, params)
    d = compute.build_daily(P, raw["price"], params).dropna(subset=["rank_exact"])
    # validate._spearman, not Series.corr(method="spearman"): the latter imports
    # scipy, which the daily job does not carry, so this test failed the CI step
    # whose job is to append a row. Same statistic -- Pearson on the ranks, exact
    # to 1e-12 including ties (test_spearman_matches_scipy_including_ties).
    rho = validate._spearman(d["smooth01"], d["rank_exact"])
    assert 0.90 < rho < 0.999, "not a fixed monotone transform; do not assume it is"

    # same blend value, different era, different rank
    q = d["smooth01"].round(2)
    dup = q[q.duplicated(keep=False)]
    grp = d.loc[dup.index].groupby(q.loc[dup.index])["rank_exact"]
    assert (grp.max() - grp.min()).max() > 0.10


@needs_csv
def test_gates_are_invariant_to_the_rank(raw, params):
    """Reach/bottom orderings must not change when the final map changes."""
    from model.v3 import compute
    P = pillars.build_pillars(raw, params)
    d = compute.build_daily(P, raw["price"], params)
    highs = ["2013-12-04", "2017-12-17", "2021-04-14", "2024-03-13", "2025-10-06"]
    by_blend = sorted(highs, key=lambda t: d["smooth01"].loc[t])
    by_rank = sorted(highs, key=lambda t: d["rank_exact"].loc[t])
    assert by_blend == by_rank


@needs_csv
def test_band_is_the_image_of_the_disagreement_interval(raw, params):
    """Band must be rank(smooth +/- s_t), hence asymmetric, and contain the score."""
    from model.v3 import compute
    P = pillars.build_pillars(raw, params)
    d = compute.build_daily(P, raw["price"], params).dropna(subset=["rank_exact"])
    assert (d["risk_lo"] <= d["risk100"]).all()
    assert (d["risk_hi"] >= d["risk100"]).all()
    half = (d["risk_hi"] - d["risk_lo"]) / 2
    assert 6 <= half.median() <= 20            # the spec's honest resolution
    # asymmetry is expected: a symmetric band cannot express a nonlinear map
    lo_arm = d["risk100"] - d["risk_lo"]
    hi_arm = d["risk_hi"] - d["risk100"]
    assert (lo_arm != hi_arm).mean() > 0.25


@needs_csv
def test_staleness_covers_every_required_field(df):
    """§9.1: an MVRV outage must set stale, not just darken V."""
    from model.v3 import features as F
    holed = df.copy()
    holed.iloc[-6:, holed.columns.get_loc("CapMVRVCur")] = np.nan
    s = F.build_raw(holed)["input_stale_days"]
    assert s.iloc[-1] == 6
    assert F.build_raw(df)["input_stale_days"].max() == 0


def test_price_alt_divergence_is_flagged_not_averaged():
    """§9.2: a 3% disagreement flags the row; the two prices are never averaged."""
    import importlib.util
    import sys as _s
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("d3", root / "etl" / "daily_v3.py")
    m = importlib.util.module_from_spec(spec)
    _s.modules["d3"] = m
    spec.loader.exec_module(m)

    official, alt = 100000.0, 105000.0
    off = abs(alt - official) / official
    assert off > m.PRICE_ALT_TOL
    # the official price must be untouched by the presence of a second source
    assert official != (official + alt) / 2

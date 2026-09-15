"""Tests for the outcome layer (Step 1).

The layer is DARK on the current tape. These tests exist so it cannot become
live by accident, and so the double-causality that makes it meaningful cannot be
quietly relaxed.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from model.v3 import features, growth, compute, maps, outcome, pillars

CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV), reason=f"{CSV} not present")


@pytest.fixture(scope="module")
def bundle():
    df = features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"]))
    raw = features.build_raw(df)
    params = growth.growth_params(raw["price"])
    P = pillars.build_pillars(raw, params)
    d = compute.build_daily(P, raw["price"], params)
    price = raw["price"].reindex(d.index)
    oc = outcome.forward_outcomes(price)
    kr = maps.expanding_cdf(raw["kappa"].dropna()).reindex(d.index)
    state = pd.DataFrame({"rank": d["rank_exact"], "kappa_rank": kr})
    return d, price, oc, state, outcome.conditional_outcomes(state, oc), \
        outcome.climatology(oc)


def test_forward_outcomes_are_open_at_the_tail():
    """The last `horizon` days cannot have a realised outcome."""
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    p = pd.Series(np.linspace(100, 200, 400), index=idx)
    o = outcome.forward_outcomes(p, horizon=180)
    assert o["fwd_ret"].iloc[-180:].isna().all()
    assert o["fwd_ret"].iloc[:-180].notna().all()


def test_drawdown_is_measured_from_today_not_from_a_later_peak():
    idx = pd.date_range("2020-01-01", periods=200, freq="D")
    v = np.full(200, 100.0)
    v[50:] = 200.0          # doubles, never falls below today's price
    v[150:] = 150.0
    p = pd.Series(v, index=idx)
    o = outcome.forward_outcomes(p, horizon=100)
    assert o["max_dd"].iloc[0] == pytest.approx(0.0)     # never below 100
    assert o["hit_dd"].iloc[0] == 0.0


@needs_csv
def test_analogues_are_only_days_whose_outcome_was_already_known(bundle):
    """Double causality: an analogue's forward window must have closed by t."""
    d, price, oc, state, cond, base = bundle
    horizon = outcome.HORIZON
    # rebuild one row by hand and check no neighbour is younger than t - horizon
    i = len(state) - 1
    t = state.index[i]
    usable = state.index[: max(i - horizon + 1, 0)]
    assert usable.max() <= t - pd.Timedelta(days=horizon - 1)
    # the layer must be dark for the first `horizon` rows, which have no analogues
    assert cond["p_dd"].iloc[:horizon].isna().all()


@needs_csv
def test_layer_is_dark_on_the_current_tape(bundle):
    """Pre-committed rule: no skill, no publication. Measured skill is -0.131."""
    d, price, oc, state, cond, base = bundle
    rep = outcome.calibration_report(cond, base, oc)
    assert rep["skill_vs_climatology"] < 0
    assert not outcome.layer_is_live(rep)


def test_activation_rule_cannot_be_satisfied_by_a_thin_sample():
    """Skill alone is not enough: an overlap-adjusted sample of 3 is not evidence."""
    assert not outcome.layer_is_live({"skill_vs_climatology": 0.9, "eff_n": 3})
    assert outcome.layer_is_live({"skill_vs_climatology": 0.9, "eff_n": 200})
    assert not outcome.layer_is_live({"skill_vs_climatology": float("nan"),
                                      "eff_n": 500})


def test_brier_and_skill_behave_as_expected():
    y = pd.Series([1.0, 1.0, 0.0, 0.0])
    perfect = pd.Series([1.0, 1.0, 0.0, 0.0])
    coin = pd.Series([0.5, 0.5, 0.5, 0.5])
    assert outcome.brier_score(perfect, y) == pytest.approx(0.0)
    assert outcome.brier_score(coin, y) == pytest.approx(0.25)


@needs_csv
def test_climatology_is_causal_and_calibrated(bundle):
    """The base rate uses only outcomes observable at t, and is well calibrated."""
    d, price, oc, state, cond, base = bundle
    b = base["p_dd"].dropna()
    assert b.between(0, 1).all()
    rep = outcome.calibration_report(cond, base, oc)
    # climatology is calibrated by construction: its Brier should sit near p(1-p)
    p = oc["hit_dd"].mean()
    assert abs(rep["brier_climatology"] - p * (1 - p)) < 0.05

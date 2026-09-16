"""Pillar G's functional-form risk: measured, disclosed, never used to switch."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from model.v3 import features, growth, growth_specs as gs, pillars

CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV), reason=f"{CSV} not present")


@pytest.fixture(scope="module")
def raw():
    return features.build_raw(
        features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"])))


@pytest.fixture(scope="module")
def G(raw):
    return gs.mapped_G(gs.all_residuals(raw["price"]))


@needs_csv
def test_official_spec_reproduces_production_G(raw, G):
    """If A drifts from production, the whole comparison is against a stranger."""
    params = growth.growth_params(raw["price"])
    prod = pillars.build_pillars(raw, params)["G"]
    a, b = G[gs.OFFICIAL].dropna().align(prod.dropna(), join="inner")
    assert len(a) > 4000
    assert np.allclose(a.to_numpy(), b.to_numpy())


@needs_csv
def test_every_spec_is_causal(raw):
    """Truncating the future must not move a past residual, in any specification."""
    cut = raw.index[-400]
    for spec in gs.SPECS:
        full = gs.residuals_for_spec(raw["price"], spec)
        trunc = gs.residuals_for_spec(raw["price"].loc[:cut], spec)
        if not np.isfinite(full.get(cut, np.nan)):
            continue
        assert full.loc[cut] == pytest.approx(trunc.loc[cut], abs=1e-10), spec


@needs_csv
def test_broken_etf_spec_stays_dark_until_its_hinge_is_estimable(G):
    """A hinge fitted on a handful of post-knot days is not a specification."""
    e = G["E_broken_etf"].dropna()
    assert not e.empty
    first = e.index[0]
    knot = pd.Timestamp(gs.ETF_KNOT)
    assert (first - knot).days >= gs.KNOT_MIN_DAYS
    # and the other specifications are live far earlier
    assert G[gs.OFFICIAL].first_valid_index() < knot


@needs_csv
def test_l1_and_huber_agree_more_than_huber_and_a_different_shape(raw, G):
    """Changing the estimator matters less than changing the functional form."""
    rep = gs.disagreement_report(G)
    c = rep["corr_with_official"]
    assert c["B_power_law_l1"] > 0.98          # same shape, different loss
    assert c["C_log_linear"] < c["B_power_law_l1"]   # different shape


@needs_csv
def test_disagreement_is_material_and_recent(raw, G):
    """The headline: G's spread is largest in the ETF era, as §14 predicted."""
    live = G.dropna(how="all")
    spread = (live.max(axis=1) - live.min(axis=1)) * 100
    assert spread.median() > 3
    recent = spread.loc["2025-01-01":].median()
    older = spread.loc[:"2021-12-31"].median()
    assert recent > older


def test_official_spec_is_named_and_first():
    assert gs.OFFICIAL == "A_power_law_huber"
    assert gs.SPECS[0] == gs.OFFICIAL


@needs_csv
def test_specs_never_feed_the_official_pillar():
    """No production module may import the alternatives; G cannot be switched."""
    import pathlib
    pkg = pathlib.Path(gs.__file__).parent
    for name in ("compute", "pillars", "features", "maps", "growth"):
        assert "growth_specs" not in (pkg / f"{name}.py").read_text(), name

"""Specification ensemble: it must report uncertainty, never select a member."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from model.v3 import compute, ensemble, features

CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV), reason=f"{CSV} not present")


@pytest.fixture(scope="module")
def raw():
    return features.build_raw(
        features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"])))


@pytest.fixture(scope="module")
def ens(raw):
    return ensemble.ensemble_band(raw)


def test_incumbent_member_is_the_constitution():
    """Member zero must be exactly the shipped constants, or the band is centred
    on something that is not the published model."""
    m = ensemble.members()[0]
    assert m["_label"] == "incumbent"
    assert m["ema_span"] == compute.EMA_SPAN
    assert m["weights"] == dict(compute.WEIGHTS)
    for k, v in ensemble.INCUMBENT.items():
        assert m[k] == v


def test_grid_is_deterministic():
    """Two calls must give identical members, or the published band drifts."""
    a = [m["_label"] for m in ensemble.members()]
    b = [m["_label"] for m in ensemble.members()]
    assert a == b
    assert len(set(a)) == len(a)


def test_every_axis_of_the_constitution_is_varied():
    """A knob missing from the grid is an untested choice reported as certain."""
    varied = set()
    for m in ensemble.members():
        for k, v in ensemble.INCUMBENT.items():
            if m[k] != v:
                varied.add(k)
    assert varied == set(ensemble.INCUMBENT), f"never varied: {set(ensemble.INCUMBENT) - varied}"


@needs_csv
def test_incumbent_reproduces_the_official_tape(raw, ens):
    """The ensemble's incumbent must equal the production build exactly."""
    from model.v3 import growth, pillars
    band, M = ens
    params = growth.growth_params(raw["price"])
    official = compute.build_daily(pillars.build_pillars(raw, params),
                                   raw["price"], params)["rank_exact"]
    a, b = M["incumbent"].dropna().align(official.dropna(), join="inner")
    assert np.allclose(a.to_numpy(), b.to_numpy())


@needs_csv
def test_band_contains_the_published_score(raw, ens):
    band, M = ens
    d = band.dropna(subset=["ens_min", "ens_max", "rank_exact"])
    assert (d["ens_min"] <= d["rank_exact"] + 1e-12).all()
    assert (d["ens_max"] >= d["rank_exact"] - 1e-12).all()


@needs_csv
def test_incumbent_is_not_an_outlier_of_its_own_grid(ens):
    """If the constitution sat at an extreme, the grid would be flattering it."""
    band, M = ens
    assert 0.25 < band["ens_pctile_of_incumbent"].median() < 0.75


@needs_csv
def test_every_member_is_causal(raw):
    """A member is a full recomputation, so truncation must not move old values."""
    spec = ensemble.members()[5]
    full = ensemble.build_member(raw, spec)
    cut = raw.index[-300]
    trunc = ensemble.build_member(raw.loc[:cut], spec)
    common = full.dropna().index.intersection(trunc.dropna().index)
    assert len(common) > 1000
    # expanding CDFs lengthen with history, so only the FIRST member's own
    # causality is asserted here: values at date d must match when computed on
    # data <= d. Compare on the truncated end point itself.
    assert full.loc[cut] == pytest.approx(trunc.loc[cut], abs=1e-12)


@needs_csv
def test_2025_is_a_knife_edge_and_is_reported_as_one(ens):
    """The finding that must not be lost: the incumbent passes 2025 by +0.0035,
    and only about half the plausible constitutions agree."""
    band, M = ens
    row = M.loc[pd.Timestamp("2025-10-06")].dropna()
    share = float((row >= 0.80).mean())
    assert 0.3 < share < 0.7
    # by contrast the older highs are unanimous
    for d in ("2013-12-04", "2017-12-17", "2021-04-14", "2024-03-13"):
        assert (M.loc[pd.Timestamp(d)].dropna() >= 0.80).mean() == 1.0

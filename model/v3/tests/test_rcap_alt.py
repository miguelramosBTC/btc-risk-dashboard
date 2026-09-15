"""The second realized-cap construction: a detector that must not become a voter."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model.v3 import rcap_alt


def _cm(n=800, seed=1):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    rng = np.random.default_rng(seed)
    price = 10000 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    rcap = 6000 * np.exp(np.cumsum(rng.normal(0.0008, 0.005, n)))
    supply = 19e6
    return pd.Series(price * supply, index=idx), pd.Series(rcap * supply, index=idx)


def test_no_mixed_clocks():
    """MVRV_alt exists only on dates both series carry — never a stale R."""
    mcap, rcap = _cm()
    alt = rcap_alt.mvrv_alt(mcap, rcap.iloc[:-5])          # alt lags 5 days
    assert alt.index.max() == rcap.index[-6]
    assert alt.index.max() < mcap.index.max()


def test_numerator_is_the_cm_market_cap():
    mcap, rcap = _cm()
    alt = rcap_alt.mvrv_alt(mcap, rcap)
    assert np.allclose(alt["MVRV_alt"] * alt["R_alt"], mcap.reindex(alt.index))


def test_methodology_gap_is_disclosed_not_alerted_without_a_band():
    """A constant 4% methodology difference is normal; without a frozen band, no alert."""
    mcap, rcap = _cm()
    mvrv_cm = mcap / rcap
    alt = rcap_alt.mvrv_alt(mcap, rcap * 1.04)             # alt R is 4% higher, forever
    rep = rcap_alt.gap_report(mvrv_cm, alt)
    assert rep["usable"]
    assert rep["level_gap_log_last"] == pytest.approx(np.log(1.04), abs=1e-9)
    assert rep["rank_corr_365"] > 0.999                     # ranks co-move perfectly
    assert rep["dlogR_corr_365"] > 0.999
    assert rep["alert"] is False and rep["band"] is None


def test_a_smooth_restatement_is_caught_by_drift_not_by_comovement():
    """A creeping restatement keeps co-movement looking healthy.

    CM drifts 60% away from the alternative over 200 days. dlog-R correlation
    stays at 0.999 and rank correlation only softens to 0.98 -- both still look
    fine on a dashboard. Only the drift of the gap shows it. This is why three
    statistics are published and not one.
    """
    mcap, rcap = _cm()
    restated = rcap.copy()
    restated.iloc[-200:] *= np.linspace(1.0, 1.6, 200)      # CM drifts away
    mvrv_cm = mcap / restated
    alt = rcap_alt.mvrv_alt(mcap, rcap)
    rep = rcap_alt.gap_report(mvrv_cm, alt)
    # a SMOOTH restatement is invisible to co-movement: this is the lesson
    assert rep["dlogR_corr_365"] > 0.99      # still looks healthy
    assert rep["rank_corr_365"] > 0.95       # softens to ~0.98, not alarming
    # ... and visible in the drift of the gap, which is why that statistic exists
    assert abs(rep["gap_drift_365"]) > 0.3
    # a constant methodology difference, by contrast, has no drift
    steady = rcap_alt.gap_report(mcap / rcap, rcap_alt.mvrv_alt(mcap, rcap * 1.04))
    assert abs(steady["gap_drift_365"]) < 1e-9


def test_thin_overlap_is_unusable():
    mcap, rcap = _cm(n=300)
    rep = rcap_alt.gap_report(mcap / rcap, rcap_alt.mvrv_alt(mcap, rcap))
    assert rep["usable"] is False


def test_alt_never_reaches_the_combiner():
    """No module in the official path imports rcap_alt; it cannot vote."""
    import pathlib
    pkg = pathlib.Path(rcap_alt.__file__).parent
    for name in ("compute", "pillars", "features", "growth", "maps"):
        src = (pkg / f"{name}.py").read_text()
        assert "rcap_alt" not in src, f"{name}.py must not depend on the detector"


def test_alt_hash_is_stable_and_null_without_a_value():
    row = pd.Series({"R_alt": 123456789.0})
    assert rcap_alt.alt_hash(row) == rcap_alt.alt_hash(row)
    assert rcap_alt.alt_hash(pd.Series({"R_alt": np.nan})) is None
    assert rcap_alt.alt_hash(pd.Series({"x": 1})) is None

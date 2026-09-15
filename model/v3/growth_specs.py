"""Growth-path specification risk — the one §14 limitation names by name.

    "A power law in days-since-genesis is a model. If the right structural trend
     is a broken log-linear with ETF-era slope change, G will mis-rank the next
     ATH. The 90-day freeze and Huber loss reduce drama; they do not pick the
     true trend."

`ensemble.py` varies G's *parameters* -- delta, cadence, blend, Nmin. It never
varies its *functional form*, so the published model-uncertainty band is silent
about exactly the risk §14 raises. This module fills that hole: it refits pillar
G under a pre-registered set of alternative trend specifications, each fully
causal and on the same 90-day refit-and-hold cadence, and publishes how much G
disagrees with itself.

It does not choose. The official G stays specification A. A disagreement is a
disclosure, not a trigger to switch: switching to whichever trend currently
flatters the score is the purest form of the retune this project forbids. If
the specifications diverge persistently, that is evidence for a *version*
review under §9.4, decided deliberately and shipped as its own tape.

PRE-REGISTERED SPECIFICATIONS (fixed 2026-09-14, before any output was seen):

  A power_law_huber   log P ~ a + b log(d)                 [OFFICIAL]
  B power_law_l1      same design, median (L1) regression
  C log_linear        log P ~ a + b d          (constant exponential growth)
  D trailing_8y       log P ~ a + b log(d), fitted on the last 2,920 days only
  E broken_etf        log P ~ a + b log(d) + c max(0, log d - log d_knot),
                      knot 2024-01-10 (US spot ETF approval), continuous hinge

Why these five. B is the other estimator §7.2 permits. C is the competing
structural story -- constant percentage growth rather than decaying power-law
growth. D asks whether the distant past should still set the slope at all. E is
the §14 sentence, implemented: it lets the ETF era have its own slope while
staying continuous at the knot, and it activates only once enough post-knot data
exists to estimate that slope (otherwise it is a hinge fitted on nothing).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import growth, maps
from .constants import (BLEND_G, GROWTH_FIRST_FIT_ASOF, GROWTH_REFIT_DAYS,
                        HUBER_DELTA, NMIN_4Y, NMIN_EXP, NMIN_GROWTH)

__all__ = ["SPECS", "OFFICIAL", "residuals_for_spec", "all_residuals",
           "mapped_G", "disagreement_report"]

OFFICIAL = "A_power_law_huber"
ETF_KNOT = "2024-01-10"
KNOT_MIN_DAYS = 400          # E stays dark until the post-knot slope is estimable
TRAILING_DAYS = 2920         # D: eight years

SPECS = (OFFICIAL, "B_power_law_l1", "C_log_linear", "D_trailing_8y",
         "E_broken_etf")


def _design(spec: str, logd: np.ndarray, d: np.ndarray) -> np.ndarray:
    ones = np.ones_like(logd)
    if spec in (OFFICIAL, "B_power_law_l1", "D_trailing_8y"):
        return np.column_stack([ones, logd])
    if spec == "C_log_linear":
        return np.column_stack([ones, d])
    if spec == "E_broken_etf":
        return np.column_stack([ones, logd, np.zeros_like(logd)])  # filled later
    raise KeyError(spec)


def residuals_for_spec(price: pd.Series, spec: str,
                       nmin: int = NMIN_GROWTH, delta: float = HUBER_DELTA,
                       cadence_days: int = GROWTH_REFIT_DAYS) -> pd.Series:
    """Causal residual series under one specification, 90-day refit-and-hold.

    Identical cadence machinery to production: fit on data <= mark, hold the
    coefficients until the next mark, and score every day in between against
    the held path. Never a same-day refit.
    """
    p = price.dropna()
    logp = np.log(p.to_numpy(dtype=float))
    d = growth.days_since_genesis(p.index)
    logd = np.log(d)
    knot_d = float((pd.Timestamp(ETF_KNOT) - pd.Timestamp(growth.GENESIS)).days)
    log_knot = np.log(knot_d)

    out = pd.Series(np.nan, index=price.index, name=spec)
    loss = "l1" if spec == "B_power_law_l1" else "huber"

    for k, mark in enumerate(growth.fit_marks(p.index, cadence_days=cadence_days)):
        m = p.index <= mark
        if int(m.sum()) < nmin:
            continue
        if spec == "D_trailing_8y":
            m = m & (p.index > mark - pd.Timedelta(days=TRAILING_DAYS))
            if int(m.sum()) < nmin:
                continue

        if spec == "E_broken_etf":
            post = np.maximum(0.0, logd - log_knot)
            n_post = int((p.index[m] >= pd.Timestamp(ETF_KNOT)).sum())
            if n_post < KNOT_MIN_DAYS:
                continue                      # hinge not yet estimable: stay dark
            X = np.column_stack([np.ones(m.sum()), logd[m], post[m]])
            beta = growth.huber_fit_design(X, logp[m], delta=delta)
            fitted_all = beta[0] + beta[1] * logd + beta[2] * post
        else:
            X = _design(spec, logd[m], d[m])
            beta = growth.huber_fit_design(X, logp[m], delta=delta, loss=loss)
            basis = d if spec == "C_log_linear" else logd
            fitted_all = beta[0] + beta[1] * basis

        nxt = mark + pd.Timedelta(days=cadence_days)
        seg = (p.index >= mark) & (p.index < nxt)
        out.loc[p.index[seg]] = (logp - fitted_all)[seg]
    return out


def all_residuals(price: pd.Series, **kw) -> pd.DataFrame:
    return pd.DataFrame({s: residuals_for_spec(price, s, **kw) for s in SPECS})


def mapped_G(residuals: pd.DataFrame, nmin_exp: int = NMIN_EXP,
             nmin_4y: int = NMIN_4Y, blend: tuple = BLEND_G) -> pd.DataFrame:
    """Each specification's residual put through the same G map."""
    out = {}
    for c in residuals.columns:
        g = residuals[c].dropna()
        if g.empty:
            out[c] = pd.Series(np.nan, index=residuals.index)
            continue
        out[c] = maps.blend(maps.expanding_cdf(g, nmin=nmin_exp),
                            maps.rolling_cdf_4y(g, nmin=nmin_4y),
                            blend).reindex(residuals.index)
    return pd.DataFrame(out)


def disagreement_report(G: pd.DataFrame, dates: list[str] | None = None) -> dict:
    """How far apart the specifications are, overall and at the anchor dates."""
    live = G.dropna(how="all")
    spread = (live.max(axis=1) - live.min(axis=1)) * 100
    rep = {
        "specs_live": {c: int(G[c].notna().sum()) for c in G.columns},
        "first_live": {c: (str(G[c].first_valid_index().date())
                           if G[c].notna().any() else None) for c in G.columns},
        "spread_median_pts": float(spread.median()),
        "spread_p90_pts": float(spread.quantile(0.90)),
        "spread_max_pts": float(spread.max()),
        "corr_with_official": {
            c: float(G[[OFFICIAL, c]].dropna().corr().iloc[0, 1])
            for c in G.columns if c != OFFICIAL},
    }
    if dates:
        rep["at_dates"] = {}
        for dt in dates:
            t = pd.Timestamp(dt)
            if t in G.index and G.loc[t].notna().any():
                row = G.loc[t].dropna()
                rep["at_dates"][dt] = {c: round(float(v), 3) for c, v in row.items()}
                rep["at_dates"][dt]["_spread_pts"] = round(
                    float((row.max() - row.min()) * 100), 1)
    return rep

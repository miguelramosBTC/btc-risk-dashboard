"""Pillar G — causal growth residual against a robust power-law path.

Spec §7.2 ("Pillar G"), §8.1 (adaptive parameters without a dark net), with the
cadence locked on 2026-09-11:

    log P_s = a + b * log(d_s) + e_s,     d_s = days since 2009-01-03

fitted by Huber M-estimation (delta = 1.345) on **data <= fit_asof only**, then
held for 90 calendar days.

Cadence semantics — refit-and-hold, not a lagged daily fit:

* First mark `2013-10-30`, the first UTC date with >= 1,200 daily closes
  (1,201 on that morning). Those (a, b) go live **the same day**, not 90 days
  later.
* (a, b) are held for 90 calendar days. Every official row in that window stores
  the same pair plus `fit_asof` and `n_fit`.
* Next marks 2014-01-28, 2014-04-28, ... each fitted on data <= that mark.
* The residual at t is always `log P_t - (a_k + b_k log d_t)` using the last
  *committed* fit. Never a same-day refit: through the Nov-Dec 2013 spike, G is
  scored against a path fitted on 30 October, so the residual can still print
  rich. A daily refit would absorb part of the move it is supposed to measure.

Rolling `fit_asof` is scheduled adaptation inside v3.0, so it does **not** bump
`schema_version`. Changing the cadence, the estimator, delta, or the 1,200-day
minimum does.

Why Huber and not OLS: least squares lets the 2013 and 2017 blow-offs own the
slope, which flattens later residuals exactly where the model has to stay
sensitive. Huber is quadratic for small standardised residuals and linear beyond
delta, so outliers move the fit proportionally rather than quadratically. The
scale is a MAD estimate re-derived at each IRLS step, so delta means the same
thing regardless of the sample's units.

No scipy dependency: a 20-line IRLS solves this exactly, which keeps the daily
GitHub Actions job on numpy/pandas only. `tests/test_phase2.py` cross-checks the
implementation against `statsmodels.RLM` when it is installed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constants import (GENESIS, GROWTH_FIRST_FIT_ASOF, GROWTH_REFIT_DAYS,
                        HUBER_DELTA, NMIN_GROWTH)

__all__ = ["days_since_genesis", "huber_fit", "huber_fit_design", "fit_marks",
           "growth_params", "growth_residual"]


def days_since_genesis(index: pd.DatetimeIndex, genesis: str = GENESIS) -> np.ndarray:
    """d_s, strictly positive so log(d) is defined."""
    d = (index - pd.Timestamp(genesis)).days.to_numpy(dtype=float)
    if (d <= 0).any():
        raise ValueError("dates at or before genesis have no log(d)")
    return d


def huber_fit_design(X: np.ndarray, y: np.ndarray, delta: float = HUBER_DELTA,
                     max_iter: int = 100, tol: float = 1e-10,
                     loss: str = "huber") -> np.ndarray:
    """Robust M-estimate for an arbitrary design matrix, by IRLS with MAD scale.

    `loss="huber"` is quadratic inside delta and linear beyond; `loss="l1"` is
    median (least-absolute-deviations) regression, the other estimator §7.2
    allows. Both are deterministic: seeded from OLS, run to a fixed tolerance.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    X, y = X[ok], y[ok]
    if X.shape[0] < X.shape[1] + 1:
        return np.full(X.shape[1], np.nan)

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    for _ in range(max_iter):
        r = y - X @ beta
        scale = 1.4826 * np.median(np.abs(r - np.median(r)))
        if not np.isfinite(scale) or scale <= 0:
            scale = float(np.sqrt(np.mean(r ** 2))) or 1.0
        u = r / scale
        if loss == "l1":
            w = 1.0 / np.maximum(np.abs(u), 1e-6)
        else:
            w = np.where(np.abs(u) <= delta, 1.0,
                         delta / np.maximum(np.abs(u), 1e-12))
        WX = X * w[:, None]
        new, *_ = np.linalg.lstsq(WX.T @ X, WX.T @ y, rcond=None)
        if np.max(np.abs(new - beta)) < tol:
            return new
        beta = new
    return beta


def huber_fit(x: np.ndarray, y: np.ndarray, delta: float = HUBER_DELTA,
              max_iter: int = 100, tol: float = 1e-10) -> tuple[float, float]:
    """Huber M-estimate of (intercept, slope) for `y ~ a + b x`.

    Thin wrapper over `huber_fit_design`; this is the production path for pillar
    G and its behaviour is unchanged.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 2:
        return (np.nan, np.nan)

    X = np.column_stack([np.ones_like(x), x])
    beta = huber_fit_design(X, y, delta=delta, max_iter=max_iter, tol=tol)
    return float(beta[0]), float(beta[1])


def _legacy_unused(X, y, beta, delta, max_iter, tol):

    return beta


def fit_marks(index: pd.DatetimeIndex,
              first_asof: str = GROWTH_FIRST_FIT_ASOF,
              cadence_days: int = GROWTH_REFIT_DAYS) -> list[pd.Timestamp]:
    """The refit calendar: first_asof, +90d, +90d, ... up to the end of `index`.

    Marks are calendar dates, not row counts, so a data hole cannot shift the
    schedule. A mark that falls on a day with no close uses all data <= that
    date, which is what "fit on data <= fit_asof" means.
    """
    start, end = pd.Timestamp(first_asof), index.max()
    if end < start:
        return []
    n = int((end - start).days // cadence_days)
    return [start + pd.Timedelta(days=cadence_days * k) for k in range(n + 1)]


def growth_params(price: pd.Series, nmin: int = NMIN_GROWTH,
                  delta: float = HUBER_DELTA,
                  cadence_days: int = GROWTH_REFIT_DAYS) -> pd.DataFrame:
    """Per-day (a, b, fit_asof, n_fit) as a step function of the refit calendar.

    Causality is structural: the fit at mark k sees `price.loc[:mark]` and
    nothing else, and a row at date t carries the last mark <= t.
    """
    if not isinstance(price.index, pd.DatetimeIndex):
        raise TypeError("growth_params requires a DatetimeIndex")
    p = price.dropna()
    logp = np.log(p.to_numpy(dtype=float))
    d = days_since_genesis(p.index)
    logd = np.log(d)

    rows = []
    for mark in fit_marks(p.index, cadence_days=cadence_days):
        m = p.index <= mark
        n_fit = int(m.sum())
        if n_fit < nmin:
            continue                      # not yet eligible; G stays dark
        a, b = huber_fit(logd[m], logp[m], delta=delta)
        rows.append({"fit_asof": mark, "a": a, "b": b, "n_fit": n_fit})

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(index=price.index,
                            columns=["a", "b", "fit_asof", "n_fit"], dtype=float)

    # step function: each date carries the most recent mark on or before it
    held = out.set_index("fit_asof").reindex(
        out["fit_asof"].tolist()).sort_index()
    idx = price.index
    pos = np.searchsorted(held.index.to_numpy(), idx.to_numpy(), side="right") - 1
    res = pd.DataFrame(index=idx, columns=["a", "b", "fit_asof", "n_fit"],
                       dtype=object)
    live = pos >= 0
    res.loc[live, "a"] = held["a"].to_numpy()[pos[live]]
    res.loc[live, "b"] = held["b"].to_numpy()[pos[live]]
    res.loc[live, "n_fit"] = held["n_fit"].to_numpy()[pos[live]]
    res.loc[live, "fit_asof"] = held.index.to_numpy()[pos[live]]
    for c in ("a", "b", "n_fit"):
        res[c] = pd.to_numeric(res[c], errors="coerce")
    return res


def growth_residual(price: pd.Series, params: pd.DataFrame | None = None) -> pd.Series:
    """g_t = log P_t - (a_k + b_k log d_t), with (a_k, b_k) from the held fit."""
    if params is None:
        params = growth_params(price)
    d = days_since_genesis(price.index)
    fitted = params["a"].to_numpy(dtype=float) + \
        params["b"].to_numpy(dtype=float) * np.log(d)
    return pd.Series(np.log(price.to_numpy(dtype=float)) - fitted,
                     index=price.index, name="g")

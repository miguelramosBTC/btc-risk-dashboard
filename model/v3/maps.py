"""Causal empirical-CDF maps — the only mapping allowed in the official v3 graph.

Spec §7.1 and §15.1. This module replaces v2's frozen logistics entirely: there
is no `PARAMS = {"mayer": (1.60, 0.55), ...}` anywhere in v3. A pillar's raw
series is turned into a [0,1] score by its *rank in its own causal history*,
which is what keeps the scale alive after cycle-peak multiples compress.

Convention (§15.1), for a window W_t of valid observations ending at t:

    F_t(x_t) = (#{x_s <= x_t : s in W_t} - 0.5) / |W_t|

clipped to [0.001, 0.999]. The -0.5 continuity correction stops a new lifetime
maximum from mapping to exactly 1.0 on a short window; the clip stops one print
from saturating a pillar before blending.

Two windows:

* `expanding_cdf`  — W_t = all valid observations from t0 through t. Inactive
  (NaN) until NMIN_EXP valid observations exist.
* `rolling_cdf_4y` — W_t = valid observations in the trailing *calendar* window
  [t - 1460 days, t], inclusive of t. Inactive until NMIN_4Y exist.

Causality is structural, not a convention to remember: every window ends at t
and no function here ever reads index i+1. `assert_no_lookahead` re-derives that
property by recomputation on truncated inputs, which is the check the unit tests
actually run.

NaN handling: NaN inputs are *excluded from the window* (they are not counted in
|W_t| and never satisfy the <= comparison), and map to NaN on their own day. A
hole in a raw series therefore shrinks that day's window rather than biasing it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constants import CDF_CLIP, NMIN_4Y, NMIN_EXP, WINDOW_4Y_DAYS

__all__ = [
    "empirical_cdf_point",
    "expanding_cdf",
    "rolling_cdf_4y",
    "blend",
    "assert_no_lookahead",
]


def _clip(a: np.ndarray) -> np.ndarray:
    lo, hi = CDF_CLIP
    return np.clip(a, lo, hi)


def empirical_cdf_point(window: np.ndarray, x: float) -> float:
    """F(x) over one explicit window, with the §15.1 continuity correction.

    The reference implementation: slow, obviously correct, and used by the tests
    to check the vectorised paths below. NaNs in `window` are dropped.
    """
    if x is None or not np.isfinite(x):
        return np.nan
    w = np.asarray(window, dtype=float)
    w = w[np.isfinite(w)]
    n = w.size
    if n == 0:
        return np.nan
    return float(_clip(np.array([((w <= x).sum() - 0.5) / n]))[0])


def expanding_cdf(x: pd.Series, nmin: int = NMIN_EXP) -> pd.Series:
    """Expanding empirical CDF of `x`, causal, inactive before `nmin`.

    The window at t is every valid observation from the start of `x` through t,
    inclusive. `x` must be sorted by date and carry a DatetimeIndex (the index is
    not used here, but requiring it keeps callers honest about alignment).

    Implementation note: this is O(n log n) via an order-statistic pass rather
    than the O(n^2) loop, and the tests assert it agrees with
    `empirical_cdf_point` exactly on a real series.
    """
    _require_datetime_index(x)
    v = x.to_numpy(dtype=float)
    n = v.size
    out = np.full(n, np.nan)

    order: list[float] = []          # sorted list of valid values seen so far
    import bisect

    for i in range(n):
        xi = v[i]
        if np.isfinite(xi):
            # count of valid values <= xi among those seen *including* today
            bisect.insort(order, xi)
            m = len(order)
            if m >= nmin:
                cnt = bisect.bisect_right(order, xi)
                out[i] = (cnt - 0.5) / m
        # NaN today -> NaN score today; the value is not inserted, so it never
        # enters any later window either.
    return pd.Series(_clip(out), index=x.index, name=_name(x, "F_exp"))


def rolling_cdf_4y(x: pd.Series, nmin: int = NMIN_4Y,
                   window_days: int = WINDOW_4Y_DAYS) -> pd.Series:
    """Empirical CDF over the trailing calendar window [t-(window_days-1), t].

    Calendar-based, not row-based: if the input has holes, the window is still
    four years of *dates*, so the 4-year map does not silently reach further back
    on a gappy series than on a complete one.
    """
    _require_datetime_index(x)
    idx = x.index
    v = x.to_numpy(dtype=float)
    n = v.size
    out = np.full(n, np.nan)

    # left edge by date, monotone -> two pointers
    lefts = idx.searchsorted(idx - pd.Timedelta(days=window_days - 1), side="left")
    for i in range(n):
        xi = v[i]
        if not np.isfinite(xi):
            continue
        w = v[lefts[i]: i + 1]
        w = w[np.isfinite(w)]
        m = w.size
        if m >= nmin:
            out[i] = ((w <= xi).sum() - 0.5) / m
    return pd.Series(_clip(out), index=idx, name=_name(x, "F_4y"))


def blend(a: pd.Series, b: pd.Series, weights: tuple[float, float]) -> pd.Series:
    """Weighted blend of two mapped series, renormalised over whichever is live.

    A pillar whose 4-year component has not warmed up yet is not dark: it runs on
    its expanding component alone at full weight. Both dark -> NaN -> the pillar
    is inactive and the combiner renormalises over the rest (§7.1).
    """
    wa, wb = weights
    if abs((wa + wb) - 1.0) > 1e-12:
        raise ValueError(f"blend weights must sum to 1.0, got {wa + wb}")
    A, B = a.align(b, join="outer")
    va, vb = A.to_numpy(dtype=float), B.to_numpy(dtype=float)
    oka, okb = np.isfinite(va), np.isfinite(vb)
    num = np.where(oka, wa * va, 0.0) + np.where(okb, wb * vb, 0.0)
    den = np.where(oka, wa, 0.0) + np.where(okb, wb, 0.0)
    out = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return pd.Series(out, index=A.index)


def assert_no_lookahead(fn, x: pd.Series, cuts: int = 12, tol: float = 0.0) -> None:
    """Re-derive causality: recompute `fn` on truncated inputs and compare.

    For a causal map, the value at date d computed on x[:d] must equal the value
    at d computed on the full series — bit for bit. This is the check that would
    have caught a centred rolling window, a `shift(-1)`, or a full-sample
    quantile pretending to be a causal rank. `cuts` truncation points are spread
    across the sample.
    """
    full = fn(x)
    n = len(x)
    positions = np.linspace(int(n * 0.35), n - 1, num=cuts, dtype=int)
    for p in sorted(set(int(p) for p in positions)):
        d = x.index[p]
        trunc = fn(x.iloc[: p + 1])
        a, b = full.loc[d], trunc.loc[d]
        if np.isnan(a) and np.isnan(b):
            continue
        if not (abs(float(a) - float(b)) <= tol):
            raise AssertionError(
                f"look-ahead at {d.date()}: full={a!r} truncated={b!r} "
                f"(fn={getattr(fn, '__name__', fn)})"
            )


def _require_datetime_index(x: pd.Series) -> None:
    if not isinstance(x.index, pd.DatetimeIndex):
        raise TypeError("maps require a DatetimeIndex")
    if not x.index.is_monotonic_increasing:
        raise ValueError("maps require a date-sorted series")
    if x.index.has_duplicates:
        raise ValueError("duplicate dates in input")


def _name(x: pd.Series, prefix: str) -> str:
    return f"{prefix}({x.name})" if x.name else prefix

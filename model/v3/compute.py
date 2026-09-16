"""v3.0 combiner — families to one daily object. Pure functions, no I/O.

Spec §7.4 (combiner), §7.5 (confidence, band, precision). The constitution this
implements is in `constants.py`; nothing here is fitted.

    raw_t   = sum_k w_k * Pi_k,t / sum_k w_k * 1{Pi_k,t live}
    smooth  = EMA(raw, span 8), y0 = x0, clipped to [0, 1]
    risk01  = F_exp(smooth)             <- the published score IS a causal rank
    risk100 = round(100 * risk01), clipped to [0, 100]
    band    = 100 * [F_exp(smooth - s_t), F_exp(smooth + s_t)],  s_t = family stdev

    Why the final rank (added 2026-09-12). A blend of four percentiles is not a
    percentile. The blended composite never exceeded 0.890 in fifteen years, so
    "82 means more extended than 82% of causal history" -- the meaning §14 gives
    the number -- was false for the number actually published, and the reach gate
    only passed because validate.py re-ranked the composite behind the scenes.
    Publishing that rank makes P3 literally true and puts the success criterion
    on the gauge: the next ATH prints >= 80 or it does not.

    It is a monotone transform of the blend, so it carries no new information and
    moves no gate -- reach, order, bottom and the Spearman baseline are all
    invariant. What changes is the scale's meaning and the frequency of high
    prints: about 30% of days sit at or above 80, because 30% of Bitcoin's days
    really have sat in the top quintile of their own history. The old 4.5% was an
    artefact of averaging percentiles, not a fact about the market. Policy
    thresholds must therefore be read on the ranked scale -- a moderate book
    sells at 90-95, not 80 -- which is why the books are bound to scale-free
    triggers.

    The pre-rank blend stays on every row as `raw01` and `smooth01`, so nothing
    is lost and any reader can reconstruct the map.
    conf    = round(10 * (0.60 agree + 0.30 comp + 0.10 (1-stale))
                       * (0.7 + 0.3 * clip(2|risk01 - 0.5|, 0, 1)))

Families are V, G, T and Sigma. M was demoted on 2026-09-11 and its 0.10 is
retired, so the active family weight is 0.90 and is disclosed as such.

Renormalisation, precisely. The denominator is the summed weight of the families
that are *live on that day*. This is the warm-up rule of §7.1/§7.4 — before G
clears its Nmin the score must still be comparable to later days — and it is not
the forbidden move of redistributing the retired budget by editing the weight
table. The retired 0.10 never enters the numerator or the denominator: it is
simply gone. One consequence worth stating plainly: on a day when all four
families are live the denominator is 0.90, so the score still spans the whole
0-1 range on 90 % of the original budget.

Order of operations is load-bearing. The EMA runs on `raw`, then the result is
rounded to an integer; rounding first would let the smoother compound rounding
error along the tape. The band uses the *dispersion of the day's families*, not
the dispersion of the smoothed series: it answers "how much do the sleeves
disagree today", which is the question a reader of a gauge actually has.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import maps
from .constants import (EMA_SPAN, NMIN_EXP, RETIRED_BUDGET, SCHEMA_VERSION,
                        WEIGHTS)

__all__ = ["FAMILIES", "combine_raw", "ema", "rank_composite", "confidence",
           "build_daily"]

FAMILIES = tuple(WEIGHTS)          # ("V", "G", "T", "S")


def combine_raw(P: pd.DataFrame, weights: dict[str, float] = WEIGHTS) -> pd.Series:
    """Weighted blend over live families, renormalised by live weight."""
    cols = list(weights)
    vals = P[cols]
    w = np.array([weights[c] for c in cols], dtype=float)
    live = vals.notna().to_numpy()
    num = np.nansum(np.where(live, vals.to_numpy(dtype=float), 0.0) * w, axis=1)
    den = (live * w).sum(axis=1)
    out = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return pd.Series(out, index=P.index, name="raw")


def ema(x: pd.Series, span: int = EMA_SPAN) -> pd.Series:
    """EMA with y0 = x0, computed on the live segment only.

    `adjust=False` is the recursion in §15.2. Seeding on the first live value
    (rather than on a NaN-padded head) keeps the first published day equal to its
    own raw value instead of an artefact of the warm-up.
    """
    s = x.dropna()
    if s.empty:
        return pd.Series(np.nan, index=x.index)
    return s.ewm(span=span, adjust=False).mean().reindex(x.index)


def rank_composite(smooth: pd.Series, spread: pd.Series,
                   nmin: int = NMIN_EXP):
    """Causal expanding rank of the smoothed composite, and its band.

    The band is the rank of `smooth +/- s_t`, not `rank +/- something`: the map
    is nonlinear, so the honest interval is the image of the family-disagreement
    interval under that same map. Where the composite's history is sparse the
    band widens and where it is dense it narrows, which a symmetric band on the
    rank cannot express.
    """
    s = smooth.dropna()
    r = maps.expanding_cdf(s, nmin=nmin)
    v = s.to_numpy(dtype=float)
    sd = spread.reindex(s.index).fillna(0.0).to_numpy(dtype=float)
    lo = np.full(v.size, np.nan)
    hi = np.full(v.size, np.nan)

    import bisect
    order: list[float] = []
    for i in range(v.size):
        bisect.insort(order, v[i])
        m = len(order)
        if m < nmin:
            continue
        lo[i] = (bisect.bisect_right(order, v[i] - sd[i]) - 0.5) / m
        hi[i] = (bisect.bisect_right(order, v[i] + sd[i]) - 0.5) / m

    cl = lambda a: np.clip(a, 0.001, 0.999)
    return (r, pd.Series(cl(lo), index=s.index), pd.Series(cl(hi), index=s.index))


def confidence(risk01: pd.Series, spread: pd.Series, n_live: pd.Series,
               stale: pd.Series, n_designed: int) -> pd.Series:
    """0-10 confidence: agreement and completeness dominate, extremity tilts.

    One implementation detail the spec does not state, because it only bites in
    the warm-up: with a single live family the sample standard deviation of the
    family scores is *undefined*, not zero. Treating it as zero reads as perfect
    agreement and hands a lone sleeve a high confidence -- the 2011 rows printed
    conf 6 on one family. Undefined agreement contributes nothing instead, so a
    one-family day is carried by completeness (0.25) alone and prints low.
    """
    agree = (1.0 - 2.0 * spread).clip(0.0, 1.0).where(n_live >= 2, 0.0)
    comp = n_live / float(n_designed)
    extremity = (2.0 * (risk01 - 0.5).abs()).clip(0.0, 1.0)
    base = 0.60 * agree + 0.30 * comp + 0.10 * (1.0 - stale)
    return (10.0 * base * (0.7 + 0.3 * extremity)).round().clip(0, 10)


def build_daily(P: pd.DataFrame, price: pd.Series,
                params: pd.DataFrame | None = None,
                stale: pd.Series | None = None,
                weights: dict[str, float] = WEIGHTS,
                extras: pd.DataFrame | None = None,
                span: int = EMA_SPAN, rank_nmin: int = NMIN_EXP) -> pd.DataFrame:
    """One row per UTC day: the official object the tape stores.

    `P` carries the mapped family scores; `extras` any raw/diagnostic columns to
    publish alongside (pillar raws, demoted mctc_mod, fragility).
    """
    cols = list(weights)
    vals = P[cols]
    raw = combine_raw(P, weights)
    smooth = ema(raw, span=span).clip(0.0, 1.0)

    n_live = vals.notna().sum(axis=1)
    # ddof=1 leaves NaN where only one family is live: that is the honest value.
    # It is kept NaN for the confidence term and treated as a zero-width band,
    # since a band is a disagreement measure and one sleeve cannot disagree.
    spread = vals.std(axis=1, ddof=1, skipna=True)
    if stale is None:
        stale = pd.Series(0.0, index=P.index)
    stale = stale.reindex(P.index).fillna(0.0).astype(float)

    # The published score is the causal rank of the smoothed blend.
    ranked, band_lo, band_hi = rank_composite(smooth, spread.fillna(0.0),
                                              nmin=rank_nmin)
    risk01_c = ranked.reindex(P.index)

    risk100 = (100.0 * risk01_c).round().clip(0, 100)

    out = pd.DataFrame(index=P.index)
    # The unrounded rank. Gates read THIS, never the integer: rounding 0.797 up to
    # 80 and then calling the day "top quintile" is rounding in our own favour.
    out["rank_exact"] = risk01_c
    out["price_usd"] = price.reindex(P.index)
    out["raw01"] = raw                       # blend of the families, pre-EMA
    out["smooth01"] = smooth                 # EMA of the blend, pre-rank
    out["risk01"] = risk100 / 100.0          # dashboard scale, from the integer
    out["risk100"] = risk100
    out["risk_lo"] = (100.0 * band_lo.reindex(P.index)).round().clip(0, 100)
    out["risk_hi"] = (100.0 * band_hi.reindex(P.index)).round().clip(0, 100)
    out["conf"] = confidence(risk01_c, spread.fillna(0.0), n_live, stale, len(cols))
    out["n_live"] = n_live
    out["stale"] = stale.astype(int)
    out["spread"] = spread.fillna(0.0)
    for c in cols:
        out[c] = P[c]
    if params is not None:
        for c in ("a", "b", "n_fit"):
            out[c] = params[c]
        out["fit_asof"] = params["fit_asof"]
    if extras is not None:
        for c in extras.columns:
            out[c] = extras[c]
    out["active_weight"] = float(sum(weights.values()))
    out["retired_weight"] = RETIRED_BUDGET
    out["schema_version"] = SCHEMA_VERSION
    return out.loc[out["risk100"].notna()]

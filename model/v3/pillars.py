"""The five mapped pillars. No combiner, no weights, no I/O — Phase 2 stops here.

Each pillar is a [0,1] score built only from causal empirical-CDF maps of the raw
series in `features.py`. The combiner that turns these into `risk100` is Phase 3
and lives in `compute.py`.

    V  0.65 F_exp(MVRV) + 0.35 F_4y(MVRV)
    G  0.70 F_exp(g)    + 0.30 F_4y(g)          g from growth.py (90-day held fit)
    T  F_exp(P / SMA_200)                       Mayer alone; T_dd deleted 2026-09-11
    M  F_exp(mctc_mod)                          Puell dropped 2026-09-11
    S  0.40 F_exp(sigma30) + 0.60 F_exp(fragility)

Pillar Sigma is the "quiet is not safe" term (P4). Compression
`kappa = sigma30/sigma365` below 1 means the last month is calm relative to the
year; `fragility = V * (1 - F_exp(kappa))` is high only when a *rich* tape is
also a *quiet* one. The first component keeps genuine stress vol from being
ignored; the second is the 2025 fix. In a cheap, quiet market (2015, late 2022)
V is low, so fragility stays low and the bottom test survives.

Sigma therefore takes V as an input by construction. That is deliberate, and it
is why the Phase 2 report measures corr(V, Sigma) explicitly instead of assuming
the pillars are independent: if that correlation approaches the 0.80 gate, the
fix is a spec change, not a quiet reweighting.
"""

from __future__ import annotations

import pandas as pd

from . import growth, maps
from .constants import (BLEND_G, BLEND_S, BLEND_V, NMIN_4Y, NMIN_EXP,
                        WINDOW_4Y_DAYS)

__all__ = ["pillar_V", "pillar_G", "pillar_T", "pillar_M", "pillar_S",
           "build_pillars", "PILLAR_KEYS"]

PILLAR_KEYS = ("V", "G", "T", "M", "S")


def pillar_V(mvrv: pd.Series) -> pd.Series:
    s = mvrv.dropna()
    return maps.blend(maps.expanding_cdf(s), maps.rolling_cdf_4y(s), BLEND_V) \
        .reindex(mvrv.index)


def pillar_G(price: pd.Series, params: pd.DataFrame | None = None) -> pd.Series:
    g = growth.growth_residual(price, params).dropna()
    return maps.blend(maps.expanding_cdf(g), maps.rolling_cdf_4y(g), BLEND_G) \
        .reindex(price.index)


def pillar_T(mayer: pd.Series) -> pd.Series:
    return maps.expanding_cdf(mayer.dropna()).reindex(mayer.index)


def pillar_M(mctc_mod: pd.Series) -> pd.Series:
    return maps.expanding_cdf(mctc_mod.dropna()).reindex(mctc_mod.index)


def pillar_S(sigma30: pd.Series, kappa: pd.Series, V: pd.Series):
    """Returns (S, fragility). V must already be mapped to [0,1]."""
    f_sigma = maps.expanding_cdf(sigma30.dropna()).reindex(sigma30.index)
    f_kappa = maps.expanding_cdf(kappa.dropna()).reindex(kappa.index)
    fragility = V * (1.0 - f_kappa)
    f_frag = maps.expanding_cdf(fragility.dropna()).reindex(fragility.index)
    return maps.blend(f_sigma, f_frag, BLEND_S), fragility


def build_pillars(raw: pd.DataFrame,
                  params: pd.DataFrame | None = None,
                  spec: dict | None = None) -> pd.DataFrame:
    """All mapped pillars plus the intermediate series the report needs.

    `spec` overrides map constants for the specification ensemble
    (`model/v3/ensemble.py`). It is None in production: the official row is
    always built on the constitution, never on an ensemble member.
    """
    if params is None:
        params = growth.growth_params(raw["price"])
    sp = spec or {}
    ne = sp.get("nmin_exp", NMIN_EXP)
    n4 = sp.get("nmin_4y", NMIN_4Y)
    w4 = sp.get("window_4y", WINDOW_4Y_DAYS)
    bv = sp.get("blend_v", BLEND_V)
    bg = sp.get("blend_g", BLEND_G)
    bs = sp.get("blend_s", BLEND_S)

    def fe(x):
        return maps.expanding_cdf(x.dropna(), nmin=ne).reindex(raw.index)

    def f4(x):
        return maps.rolling_cdf_4y(x.dropna(), nmin=n4,
                                   window_days=w4).reindex(raw.index)

    out = pd.DataFrame(index=raw.index)
    out["V"] = maps.blend(fe(raw["mvrv"]), f4(raw["mvrv"]), bv)
    g = growth.growth_residual(raw["price"], params)
    out["G"] = maps.blend(fe(g), f4(g), bg)
    out["T"] = fe(raw["mayer"])
    out["M"] = fe(raw["mctc_mod"])
    f_kappa = fe(raw["kappa"])
    out["fragility"] = out["V"] * (1.0 - f_kappa)
    out["S"] = maps.blend(fe(raw["sigma30"]), fe(out["fragility"]), bs)
    out["g_raw"] = g
    return out

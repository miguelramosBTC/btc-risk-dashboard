"""Specification ensemble — how much the score depends on choices nobody tested.

The constitution fixes about a dozen numbers that were *chosen*, not measured:
the EMA span, Nmin, the four-year window length, the V/G/Σ blend ratios, the
Huber delta, the refit cadence, the family weights. Each is defensible; none is
derived. A single number published off one such combination implies a precision
the object does not have -- which is the same false-precision problem §9.5
raises about decimal places, one level up.

This module recomputes the whole causal tape under a pre-registered grid of
*equally plausible* constitutions and publishes the spread. Two bands then sit
on the row, answering different questions:

    risk_lo / risk_hi   how much the FAMILIES disagree today
    ens_lo  / ens_hi    how much the ANSWER depends on our arbitrary choices

Three rules keep this from becoming a tuning device:

1. **No member is ever selected.** The official row is always the constitution.
   The ensemble reports, it does not choose. There is no "best" member and none
   is inspected for performance.
2. **The grid is pre-registered.** `SPEC_GRID` below was fixed before any
   ensemble output was looked at. Adding a level after seeing results, or
   dropping one that widens the band, is a retune -- and a particularly
   dishonest one, because it would narrow published uncertainty without
   changing the model.
3. **Every member is causal.** Each is a full recomputation with the same
   no-look-ahead machinery; a member is not a perturbation of the incumbent's
   output.

Cost is about 0.4 s per member, so a 30-member ensemble is ~12 s -- affordable
daily, but it belongs in the ETL rather than the browser.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import compute, growth, pillars
from .constants import (BLEND_G, BLEND_S, BLEND_V, EMA_SPAN, HUBER_DELTA,
                        NMIN_4Y, NMIN_EXP, WEIGHTS, WINDOW_4Y_DAYS,
                        GROWTH_REFIT_DAYS)

__all__ = ["INCUMBENT", "SPEC_GRID", "members", "build_member", "ensemble_band",
           "spread_report"]

INCUMBENT = {
    "ema_span": EMA_SPAN,
    "nmin_exp": NMIN_EXP,
    "nmin_4y": NMIN_4Y,
    "window_4y": WINDOW_4Y_DAYS,
    "blend_v": BLEND_V,
    "blend_g": BLEND_G,
    "blend_s": BLEND_S,
    "huber_delta": HUBER_DELTA,
    "cadence_days": GROWTH_REFIT_DAYS,
    "weights": dict(WEIGHTS),
}

# --------------------------------------------------------------------------- #
#  PRE-REGISTERED GRID — fixed 2026-09-14, before any ensemble output was seen.
#  Each level is a choice a careful person could have made instead. Ranges are
#  deliberately modest: this measures sensitivity to *plausible* alternatives,
#  not the full space of things the model could have been.
# --------------------------------------------------------------------------- #
_W_EQUAL = {"V": 0.225, "G": 0.225, "T": 0.225, "S": 0.225}
_W_VHEAVY = {"V": 0.40, "G": 0.20, "T": 0.15, "S": 0.15}
_W_SHEAVY = {"V": 0.25, "G": 0.20, "T": 0.15, "S": 0.30}

SPEC_GRID = {
    "ema_span": [5, 8, 13],
    "nmin_exp": [300, 400, 500],
    "nmin_4y": [150, 200, 300],
    "window_4y": [1095, 1461, 1827],
    "blend_v": [(0.50, 0.50), (0.65, 0.35), (0.80, 0.20)],
    "blend_g": [(0.55, 0.45), (0.70, 0.30), (0.85, 0.15)],
    "blend_s": [(0.25, 0.75), (0.40, 0.60), (0.55, 0.45)],
    "huber_delta": [1.0, 1.345, 2.0],
    "cadence_days": [90, 180],
    "weights": [dict(WEIGHTS), _W_EQUAL, _W_VHEAVY, _W_SHEAVY],
}
N_COMBINED = 12          # deterministic multi-axis members, seed fixed below
SEED = 20260914


def members(grid: dict | None = None, n_combined: int = N_COMBINED,
            seed: int = SEED) -> list[dict]:
    """Incumbent + one-at-a-time variants + deterministic multi-axis members.

    One-at-a-time shows which single choice the answer is most sensitive to.
    The combined members exist because sensitivities interact, and reporting
    only OAT spread would understate the band.
    """
    g = grid or SPEC_GRID
    out = [dict(INCUMBENT, _label="incumbent")]
    for axis, levels in g.items():
        for lv in levels:
            if lv == INCUMBENT[axis]:
                continue
            out.append(dict(INCUMBENT, **{axis: lv},
                            _label=f"{axis}={_short(lv)}"))
    rng = np.random.default_rng(seed)
    for i in range(n_combined):
        pick = {a: g[a][int(rng.integers(len(g[a])))] for a in g}
        out.append(dict(INCUMBENT, **pick, _label=f"combined_{i:02d}"))
    return out


def _short(v):
    if isinstance(v, dict):
        return "w:" + "/".join(f"{v[k]:.3g}" for k in ("V", "G", "T", "S"))
    if isinstance(v, tuple):
        return "/".join(f"{x:g}" for x in v)
    return str(v)


def build_member(raw: pd.DataFrame, spec: dict) -> pd.Series:
    """One full causal recomputation. Returns that member's rank_exact."""
    params = growth.growth_params(raw["price"], delta=spec["huber_delta"],
                                  cadence_days=spec["cadence_days"])
    P = pillars.build_pillars(raw, params, spec=spec)
    d = compute.build_daily(P, raw["price"], params,
                            weights=spec["weights"], span=spec["ema_span"],
                            rank_nmin=spec["nmin_exp"])
    return d["rank_exact"].rename(spec.get("_label", "member"))


def ensemble_band(raw: pd.DataFrame, specs: list[dict] | None = None,
                  progress: bool = False) -> pd.DataFrame:
    """Per-date spread of the published score across the grid."""
    specs = specs or members()
    cols = []
    for i, sp in enumerate(specs):
        if progress:
            print(f"  [{i + 1}/{len(specs)}] {sp['_label']}")
        cols.append(build_member(raw, sp))
    M = pd.concat(cols, axis=1)
    inc = M["incumbent"]
    out = pd.DataFrame(index=M.index)
    out["rank_exact"] = inc
    out["ens_min"] = M.min(axis=1)
    out["ens_p10"] = M.quantile(0.10, axis=1)
    out["ens_p90"] = M.quantile(0.90, axis=1)
    out["ens_max"] = M.max(axis=1)
    out["ens_n"] = M.notna().sum(axis=1)
    # where does the constitution sit among equally defensible alternatives?
    out["ens_pctile_of_incumbent"] = M.rank(axis=1, pct=True)["incumbent"]
    return out, M


def spread_report(band: pd.DataFrame, M: pd.DataFrame,
                  dates: list[str] | None = None) -> dict:
    """Headline numbers: band width, incumbent centrality, per-axis sensitivity."""
    w = (band["ens_max"] - band["ens_min"]).dropna() * 100
    rep = {
        "n_members": int(M.shape[1]),
        "width_median_pts": float(w.median()),
        "width_p90_pts": float(w.quantile(0.90)),
        "width_max_pts": float(w.max()),
        "incumbent_pctile_median": float(band["ens_pctile_of_incumbent"].median()),
    }
    if dates:
        rep["at_dates"] = {}
        for d in dates:
            t = pd.Timestamp(d)
            if t not in band.index:
                continue
            row = M.loc[t].dropna()
            rep["at_dates"][d] = {
                "incumbent": round(float(band.loc[t, "rank_exact"]), 4),
                "min": round(float(row.min()), 4),
                "max": round(float(row.max()), 4),
                "share_ge_080": round(float((row >= 0.80).mean()), 3),
                "share_le_020": round(float((row <= 0.20).mean()), 3),
            }
    # which single choice moves the answer most, on average
    sens = {}
    for col in M.columns:
        if col in ("incumbent",) or col.startswith("combined_"):
            continue
        d = (M[col] - M["incumbent"]).abs().dropna()
        if len(d):
            sens[col] = round(float(d.median() * 100), 2)
    rep["oat_sensitivity_pts_median"] = dict(
        sorted(sens.items(), key=lambda kv: -kv[1]))
    return rep

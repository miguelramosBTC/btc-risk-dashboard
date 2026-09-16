"""Outcome layer — what the state has historically implied, conditioned causally.

The v3.0 score is a *rank of extension*: 82 means "more extended than 82 % of
causal history". Every reader takes it to mean something about what happens
next, and it does not say that. §14 forbids implied probabilities "unless you
add a separately calibrated layer with its own Brier score". This module is that
layer.

It does not change `risk100`. It publishes, next to it, the empirical
distribution of forward outcomes on the historical days that most resembled
today -- and the number of such days, so a thin estimate announces itself.

Causality is doubly strict here, and this is the part that is easy to get wrong:

1. An analogue day `s` may only be used at date `t` if its outcome was already
   observable at `t`, i.e. `s + horizon <= t`. Using a day whose forward window
   is still open leaks the future into the present.
2. The state of both `s` and `t` is taken from the committed tape, which is
   itself causal.

So the layer at t sees strictly less than the score at t. The last `horizon`
days before t contribute nothing, by construction.

State. Two axes, deliberately few given three and a half cycles of data:
  * `rank`  -- where the composite sits in its own causal history
  * `kappa` -- the volatility-compression regime (sigma30/sigma365), ranked
Analogues are the `k` nearest days in that space under a weighted L1 distance.
k-nearest rather than fixed buckets because it degrades gracefully: in a sparse
region the neighbourhood simply widens, and `n_analogues` plus the spread of the
neighbour distances say how much to trust it.

Honest limits, which the calibration report is designed to expose rather than
hide:
  * Analogue days overlap heavily -- 200 neighbours drawn from consecutive days
    are nothing like 200 independent observations. Effective sample is closer to
    n / horizon. Intervals are therefore wide and should stay wide.
  * Three and a half cycles means every "probability" here is an empirical
    frequency over a handful of genuinely distinct episodes.
  * If the next regime does not rhyme with the last three, this layer is wrong
    in the same way the rest of the model is wrong.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["forward_outcomes", "conditional_outcomes", "climatology",
           "brier_score", "calibration_report", "layer_is_live", "MIN_SKILL"]

# Pre-committed activation rule, fixed BEFORE the layer was first measured: the
# conditional probabilities are published only if they beat the causal base rate.
# Measured 2026-09-14 on the full tape: skill -0.131 (2013-17: -0.216, 2018-26:
# -0.086), reliability inverted -- the highest-probability bin had the LOWEST
# observed drawdown frequency. The layer is therefore DARK, and the calibration
# report is committed as the reason.
MIN_SKILL = 0.02

HORIZON = 180            # trading the spec's 90d Spearman for a window long
                         # enough to contain a cycle drawdown
DD_THRESHOLD = 0.30      # "a >30% drawdown within the horizon"
K_NEIGHBOURS = 250
NMIN_ANALOGUES = 250     # below this the layer is dark, not approximate
W_RANK, W_KAPPA = 1.0, 0.5   # state weights; kappa is the secondary axis


def forward_outcomes(price: pd.Series, horizon: int = HORIZON,
                     dd_threshold: float = DD_THRESHOLD) -> pd.DataFrame:
    """Realised forward outcomes per day. NaN where the window is still open.

    `max_dd` is the worst peak-to-trough *from today's price* inside the window,
    not from a running peak: the question is "if I hold from here, how far under
    water do I go", which is the question a risk gauge is read for.
    """
    p = price.astype(float)
    n = len(p)
    v = p.to_numpy()
    fwd_ret = np.full(n, np.nan)
    max_dd = np.full(n, np.nan)
    for i in range(n):
        j = i + horizon
        if j >= n:
            break
        win = v[i + 1: j + 1]
        fwd_ret[i] = v[j] / v[i] - 1.0
        max_dd[i] = win.min() / v[i] - 1.0
    out = pd.DataFrame({"fwd_ret": fwd_ret, "max_dd": max_dd}, index=p.index)
    out["hit_dd"] = (out["max_dd"] <= -abs(dd_threshold)).astype(float)
    out.loc[out["max_dd"].isna(), "hit_dd"] = np.nan
    return out


def climatology(outcomes: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """The unconditional causal base rate: what history said before you looked.

    This is the benchmark the conditional layer must beat. A conditional
    probability that cannot beat "the base rate up to today" is ornament, and
    saying so is the whole point of computing it.
    """
    hit = outcomes["hit_dd"]
    ret = outcomes["fwd_ret"]
    idx = outcomes.index
    base = pd.DataFrame(index=idx, columns=["p_dd", "p50"], dtype=float)
    hv, rv = hit.to_numpy(), ret.to_numpy()
    seen_h: list[float] = []
    seen_r: list[float] = []
    for i in range(len(idx)):
        # only outcomes already observable at i
        j = i - horizon
        if j >= 0 and np.isfinite(hv[j]):
            seen_h.append(hv[j])
            seen_r.append(rv[j])
        if len(seen_h) >= NMIN_ANALOGUES:
            base.iloc[i, 0] = float(np.mean(seen_h))
            base.iloc[i, 1] = float(np.median(seen_r))
    return base


def conditional_outcomes(state: pd.DataFrame, outcomes: pd.DataFrame,
                         horizon: int = HORIZON, k: int = K_NEIGHBOURS,
                         nmin: int = NMIN_ANALOGUES) -> pd.DataFrame:
    """For each date, the outcome distribution of its k most similar past days.

    `state` must carry columns `rank` and `kappa_rank`, both in [0, 1].
    """
    if not {"rank", "kappa_rank"} <= set(state.columns):
        raise KeyError("state needs 'rank' and 'kappa_rank'")
    idx = state.index
    r = state["rank"].to_numpy(dtype=float)
    kp = state["kappa_rank"].to_numpy(dtype=float)
    hit = outcomes["hit_dd"].reindex(idx).to_numpy(dtype=float)
    ret = outcomes["fwd_ret"].reindex(idx).to_numpy(dtype=float)
    dd = outcomes["max_dd"].reindex(idx).to_numpy(dtype=float)

    cols = ["p_dd", "ret_p10", "ret_p50", "ret_p90", "dd_p50", "n_analogues",
            "nbr_dist"]
    out = pd.DataFrame(index=idx, columns=cols, dtype=float)

    for i in range(len(idx)):
        last = i - horizon                      # newest day whose outcome is known
        if last < 0:
            continue
        sel = np.arange(0, last + 1)
        ok = np.isfinite(hit[sel]) & np.isfinite(r[sel]) & np.isfinite(kp[sel])
        sel = sel[ok]
        if sel.size < nmin or not np.isfinite(r[i]) or not np.isfinite(kp[i]):
            continue
        d = W_RANK * np.abs(r[sel] - r[i]) + W_KAPPA * np.abs(kp[sel] - kp[i])
        take = sel[np.argsort(d, kind="stable")[:k]]
        out.iloc[i] = [
            float(np.mean(hit[take])),
            float(np.percentile(ret[take], 10)),
            float(np.percentile(ret[take], 50)),
            float(np.percentile(ret[take], 90)),
            float(np.percentile(dd[take], 50)),
            float(take.size),
            float(np.mean(np.sort(d)[:k])),
        ]
    return out


def layer_is_live(report: dict, min_skill: float = MIN_SKILL) -> bool:
    """Publish conditional probabilities only if they beat climatology.

    Deliberately strict, and deliberately not negotiable after the fact: a
    probability that cannot beat "the base rate up to today" is not a
    probability a user should be shown next to a risk score. If this returns
    False the product shows the unconditional climatology instead, which is
    calibrated by construction and claims nothing about today.
    """
    sk = report.get("skill_vs_climatology")
    if sk is None or not np.isfinite(sk):
        return False
    return bool(sk >= min_skill and report.get("eff_n", 0) >= 20)


def brier_score(prob: pd.Series, actual: pd.Series) -> float:
    d = pd.concat([prob.rename("p"), actual.rename("y")], axis=1).dropna()
    if d.empty:
        return float("nan")
    return float(((d["p"] - d["y"]) ** 2).mean())


def calibration_report(cond: pd.DataFrame, base: pd.DataFrame,
                       outcomes: pd.DataFrame, horizon: int = HORIZON,
                       bins: int = 5) -> dict:
    """Brier vs climatology, skill score, reliability, and interval coverage.

    `eff_n` divides by the horizon because overlapping windows are not
    independent observations; every interval below should be read against it.
    """
    y = outcomes["hit_dd"]
    b_cond = brier_score(cond["p_dd"], y)
    b_base = brier_score(base["p_dd"], y)
    d = pd.concat([cond["p_dd"].rename("p"), y.rename("y")], axis=1).dropna()
    rel = []
    if not d.empty:
        q = pd.qcut(d["p"], bins, duplicates="drop")
        for g, blk in d.groupby(q, observed=True):
            rel.append({"bin": str(g), "n": int(len(blk)),
                        "predicted": round(float(blk["p"].mean()), 3),
                        "observed": round(float(blk["y"].mean()), 3)})
    cov = pd.concat([cond[["ret_p10", "ret_p90"]],
                     outcomes["fwd_ret"].rename("r")], axis=1).dropna()
    coverage = (float(((cov["r"] >= cov["ret_p10"]) &
                       (cov["r"] <= cov["ret_p90"])).mean())
                if not cov.empty else float("nan"))
    return {
        "brier_conditional": b_cond,
        "brier_climatology": b_base,
        "skill_vs_climatology": (1 - b_cond / b_base) if b_base else float("nan"),
        "n": int(len(d)),
        "eff_n": int(len(d) / horizon) if len(d) else 0,
        "reliability": rel,
        "p10_p90_coverage": coverage,
    }

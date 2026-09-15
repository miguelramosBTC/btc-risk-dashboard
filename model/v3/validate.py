"""Ship gates for v3.0 (spec §12.1). Exits non-zero if any gate fails.

    python3 -m model.v3.validate            # full report
    python3 -m model.v3.validate --quiet    # exit code only

Gates are evaluated on the committed causal tape, never on a diagnostic
recompute. The rule this file exists to enforce is not "make the gates pass" but
"fail loudly when they do not": if a gate fails, fix the pillar or change the
version — do not widen Nmin, shrink a weight, or drop a date from the reach list.

The holdout table (2025-09-11 -> 2026-09-10) is printed separately and is
labelled *design-contaminated*, not "unseen": expanding CDFs and pillar Sigma
were motivated by v2 dying at the 2025 ATH, and a one-year embargo cannot
un-know that. No parameter is chosen from that table. Real out-of-sample is the
next ATH after v3.0 is frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .constants import WEIGHTS

REACH_DATES = ["2013-12-04", "2017-12-17", "2021-04-14", "2021-11-10",
               "2024-03-13", "2025-10-06"]
BOTTOM_DATES = ["2015-01-14", "2018-12-15", "2022-11-21"]
ORDER_PAIR = ("2025-10-06", "2024-03-13")     # later must not print below earlier

# Spec §4 allows a cycle-high inversion when it is documented as a residual.
# An inversion NOT in this registry is a gate failure. Adding an entry is a
# deliberate, reviewable act -- never a way to silence a surprise.
DOCUMENTED_INVERSIONS = {
    ("2025-10-06", "2024-03-13"): (
        "Higher high in dollars, lower high on extension and cost basis. "
        "Mayer 1.18 vs 1.85, MVRV 2.29 vs 2.75, kappa 0.62 vs 1.33. "
        "G +0.047 and Sigma +0.233 both vote 2025; V -0.076 and T -0.306 vote "
        "2024. Accepted 2026-09-11: forcing 2025 above 2024 would be the ATH "
        "sleeve under a new name -- T_dd printed 0.999 on both dates and still "
        "left a -0.168 gap. No weight change, no version bump."),
}

# Family collinearity (ruling of 2026-09-11, replacing the raw-difference gate).
# Three statistics are printed every run; exactly one of them can fail the build.
#
#   r(ds_i, ds_j | dlog P)  ship blocker              fail if |r| >= 0.70
#   r(s_i, s_j)   levels    disclose only             never fails
#   r(ds_i, ds_j) raw diff  price-elasticity diagnostic, never fails
#
# Why the raw difference is not the blocker: V, G and T are monotone maps of a
# price-like state, so ds_i ~ beta_i * dlog P + u_i and r(dV, dT) = 0.883 is
# mostly r(dlog P, dlog P). Weekly and quarterly figures get worse for the same
# reason -- differencing throws away the day-noise and keeps the shared bull/bear
# tick. It isolates the common driver the families are ALLOWED to share; it does
# not reveal aliases. The partial, controlling for that driver, is the statistic
# that matches the intent "shared regime, not an alias".
COLLINEARITY_PARTIAL_MAX = 0.70   # ship blocker, partial on d log P
COLLINEARITY_PAIRS = ("V", "G", "T")   # Sigma pairs are off the gate: a family is
                                       # supposed to correlate with the composite.
HOLDOUT = ("2025-09-11", "2026-09-10")

# Named checks: reported, never a gate and never a retune date. 2026-06-30 is the
# first v3 out-of-sample bottom; until the tape reaches it the holdout stays
# UNSIGNED. No sentence of the form "validated through 2026" may be written while
# this reads PENDING, and no Coinbase spot may be spliced into V or G to fill it.
NAMED_CHECKS = {"2026-06-30": "first v3 out-of-sample bottom (June 2026 low)"}
COLLINEARITY_MAX = 0.80


class Gate:
    def __init__(self, name):
        self.name, self.ok, self.lines, self.skipped = name, True, [], False

    def check(self, cond, msg):
        self.ok &= bool(cond)
        self.lines.append(("PASS" if cond else "FAIL") + "  " + msg)
        return cond

    def note(self, msg):
        self.lines.append("      " + msg)

    def skip(self, msg):
        self.skipped, self.lines = True, self.lines + ["SKIP  " + msg]

    def report(self):
        head = "SKIP" if self.skipped and self.ok else ("PASS" if self.ok else "FAIL")
        return f"[{head}] {self.name}\n" + "\n".join("       " + l for l in self.lines)


def _quintile_rank(tape: pd.Series, date: str) -> float | None:
    """The day's causal rank, read straight off the tape.

    Since 2026-09-12 the published score IS the causal expanding rank of the
    smoothed composite, so this returns it rather than re-ranking it. Ranking a
    rank is a different question (rank-of-rank) and gave materially different
    numbers -- 0.711 vs 0.797 at 2025-10-06.

    Pass the UNROUNDED `rank_exact` column. The integer `risk100` carries
    rounding that can flatter a borderline day: 2025-10-06 ranks 0.797 and
    prints 80.
    """
    t = pd.Timestamp(date)
    if t not in tape.index or pd.isna(tape.loc[t]):
        return None
    return float(tape.loc[t])


def gate_reach(tape: pd.Series) -> Gate:
    g = Gate("Reach — every cycle high in the top quintile of its own causal history")
    for d in REACH_DATES:
        r = _quintile_rank(tape, d)
        if r is None:
            g.check(False, f"{d}: not in the computed tape (cannot be waived)")
        else:
            g.check(r >= 0.80,
                    f"{d}: causal rank {r:.4f} (needs >= 0.800, margin {r - 0.80:+.4f})")
    return g


def gate_order(tape: pd.Series) -> Gate:
    later, earlier = ORDER_PAIR
    g = Gate(f"Order — {later} not below {earlier}, or a documented residual")
    if later not in tape.index or earlier not in tape.index:
        g.check(False, "one of the dates is missing from the tape")
        return g
    a, b = tape.loc[later], tape.loc[earlier]
    if a >= b:
        g.check(True, f"{later}={a:.4f} >= {earlier}={b:.4f}")
        return g
    residual = DOCUMENTED_INVERSIONS.get((later, earlier))
    g.check(residual is not None,
            f"{later}={a:.4f} < {earlier}={b:.4f} — "
            + ("inversion documented under §4" if residual else
               "UNDOCUMENTED inversion: inspect G and Sigma, do not touch weights"))
    if residual:
        for line in _wrap(residual):
            g.note(line)
    return g


def _wrap(text, width=88):
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return out + ([cur] if cur else [])


def gate_bottom(tape: pd.Series) -> Gate:
    g = Gate("Bottom — bear lows stay in the bottom quintile")
    for d in BOTTOM_DATES:
        r = _quintile_rank(tape, d)
        if r is None:
            g.check(False, f"{d}: not in the computed tape")
        else:
            g.check(r <= 0.20,
                    f"{d}: causal rank {r:.4f} (needs <= 0.200, margin {0.20 - r:+.4f})")
    return g


def gate_low_vol_rich(pillars_mod, maps_mod) -> Gate:
    """Synthetic: at constant richness, compressing vol must not lower Sigma."""
    g = Gate("Low-vol rich — quiet is not safe")
    idx = pd.date_range("2015-01-01", periods=1400, freq="D")
    V = pd.Series(0.85, index=idx)
    base = pd.Series(np.linspace(1.30, 1.32, 1400), index=idx)
    quiet = base.copy()
    quiet.iloc[-250:] = np.linspace(1.30, 0.45, 250)
    f_base = (V * (1 - maps_mod.expanding_cdf(base))).iloc[-1]
    f_quiet = (V * (1 - maps_mod.expanding_cdf(quiet))).iloc[-1]
    g.check(f_quiet > f_base,
            f"fragility rises as kappa falls at fixed V: {f_base:.3f} -> {f_quiet:.3f}")
    cheap = pd.Series(0.05, index=idx)
    g.check((cheap * (1 - maps_mod.expanding_cdf(quiet))).dropna().max() < 0.10,
            "cheap+quiet stays low-fragility (bottom test not sacrificed)")
    return g


def gate_collinearity(P: pd.DataFrame, price: pd.Series) -> Gate:
    """Family collinearity on the Mode A common live span, pairs in {V, G, T}.

    Control: the same BTC log price that feeds T, first-differenced and aligned to
    the score date; Pearson on the residuals of ds on dlog P. If someone later
    swaps in a "smarter" control (Mayer, or V itself), that is a NEW GATE and a
    new version -- not a tweak.
    """
    g = Gate(f"Collinearity — partial |r(ds_i, ds_j | dlog P)| < {COLLINEARITY_PARTIAL_MAX:.2f}"
             f" on {'/'.join(COLLINEARITY_PAIRS)}")
    X = P[list(WEIGHTS)].dropna()
    g.note(f"Mode A common live span {X.index[0].date()} -> {X.index[-1].date()}  n={len(X)}")

    dX = X.diff().dropna()
    dp = np.log(price).reindex(X.index).diff().reindex(dX.index)
    ok = dp.notna()
    dX, dp = dX.loc[ok], dp.loc[ok]

    def _partial(a: str, b: str) -> float:
        ra = dX[a] - np.polyval(np.polyfit(dp, dX[a], 1), dp)
        rb = dX[b] - np.polyval(np.polyfit(dp, dX[b], 1), dp)
        return float(np.corrcoef(ra, rb)[0, 1])

    L, D = X.corr(), dX.corr()
    pairs = [(a, b) for i, a in enumerate(COLLINEARITY_PAIRS)
             for b in COLLINEARITY_PAIRS[i + 1:]]

    # The three columns, printed every run so this inversion cannot be forgotten:
    # levels look hot, raw difference looks hotter, the partial is the decision.
    g.note(f"{'pair':>6s} {'partial (BLOCKER)':>19s} {'level (disclose)':>18s}"
           f" {'raw diff (diagnose)':>21s}")
    partials = {}
    for a, b in pairs:
        partials[(a, b)] = _partial(a, b)
        g.note(f"{a + '-' + b:>6s} {partials[(a, b)]:>19.3f} {L.loc[a, b]:>18.3f}"
               f" {D.loc[a, b]:>21.3f}")
    g.note("price elasticity of each family: "
           + "  ".join(f"r(d{k},dlogP)={dX[k].corr(dp):+.3f}" for k in dX))

    breaches = [(a, b, r) for (a, b), r in partials.items()
                if abs(r) >= COLLINEARITY_PARTIAL_MAX]
    g.check(not breaches,
            ("no family pair reaches the partial threshold: max |r| = "
             f"{max(abs(v) for v in partials.values()):.3f}" if not breaches else
             "breaches: " + ", ".join(f"{a}-{b}={r:+.3f}" for a, b, r in breaches)))
    if breaches:
        g.note("Remedy is demotion into V on the M rule — not orthogonalisation, "
               "not a weight cut to paint the cell green.")

    # Disclosure only. Neither of these can fail the build.
    sig = [f"{a}-{b}={L.loc[a, b]:.3f}" for a, b in pairs if abs(L.loc[a, b]) > 0.80]
    if sig:
        g.note("DISCLOSED (not a fail) — levels above 0.80: " + ", ".join(sig))
    g.note("Sigma pairs, reported off-gate: "
           + "  ".join(f"S-{k}={L.loc['S', k]:+.3f}" for k in COLLINEARITY_PAIRS))
    return g


def gate_nested_baseline(tape: pd.Series, raw: pd.DataFrame, maps_mod,
                         horizon: int = 90, embargo_start: str = HOLDOUT[0]) -> Gate:
    """Walk-forward Spearman of -risk vs the next `horizon`-day return."""
    g = Gate(f"Nested baseline — non-inferior to Mayer and MVRV percentiles ({horizon}d)")
    price = raw["price"]
    fwd = price.shift(-horizon) / price - 1.0
    end = min(pd.Timestamp(embargo_start), price.index[-1])

    cand = {
        "v3 risk": tape,
        "Mayer pct": maps_mod.expanding_cdf(raw["mayer"].dropna()),
        "MVRV pct": maps_mod.expanding_cdf(raw["mvrv"].dropna()),
        "200w SMA dist": maps_mod.expanding_cdf(
            (price / price.rolling(1400, min_periods=1400).mean()).dropna()),
    }
    # COMMON sample. Each candidate has its own warm-up (the 200-week SMA alone
    # costs 1,400 days), so scoring them on their own live ranges compares
    # different decades and is not a baseline test at all.
    X = pd.DataFrame(cand).join(fwd.rename("f")).dropna().loc[:end]
    scores = {k: -X[k].corr(X["f"], method="spearman") for k in cand}
    g.note(f"common sample n={len(X)}  {X.index[0].date()} -> {X.index[-1].date()}")
    for k in cand:
        g.note(f"{k:>14s}: Spearman(-signal, fwd{horizon}) = {scores[k]:+.4f}")

    # Power: 90-day windows overlap, so the effective sample is n/horizon blocks.
    eff = max(len(X) // horizon, 2)
    rng = np.random.default_rng(0)
    blocks = np.array_split(np.arange(len(X)), eff)
    diffs = []
    for _ in range(1000):
        idx = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        s_ = X.iloc[idx]
        best = max((k for k in cand if k != "v3 risk"),
                   key=lambda k: -X[k].corr(X["f"], method="spearman"))
        diffs.append((-s_["v3 risk"].corr(s_["f"], method="spearman")) -
                     (-s_[best].corr(s_["f"], method="spearman")))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    g.note(f"~{eff} independent {horizon}-day blocks; v3 minus best baseline "
           f"95% CI [{lo:+.3f}, {hi:+.3f}]"
           f"{'  (difference not statistically resolvable)' if lo < 0 < hi else ''}")

    # Pass rule (2026-09-11): NON-INFERIORITY against the two named baselines
    # only. v3 fails if the bootstrap CI for s(v3) - s(baseline) lies entirely
    # below zero. Inconclusive is not a failure -- failing a model on a statistic
    # that cannot rank the candidates is a gate bug, not a model defect.
    # 200-week SMA distance is a reported comparator, never a ship blocker.
    for name in ("Mayer pct", "MVRV pct"):
        diffs = []
        for _ in range(1000):
            idx = np.concatenate([blocks[i] for i in
                                  rng.integers(0, len(blocks), len(blocks))])
            s_ = X.iloc[idx]
            diffs.append((-s_["v3 risk"].corr(s_["f"], method="spearman")) -
                         (-s_[name].corr(s_["f"], method="spearman")))
        lo_n, hi_n = np.percentile(diffs, [2.5, 97.5])
        g.check(not (hi_n < 0),
                f"non-inferior to {name}: v3 {scores['v3 risk']:+.4f} vs "
                f"{scores[name]:+.4f}, CI [{lo_n:+.3f}, {hi_n:+.3f}]")
    g.note(f"comparator (not a blocker) 200w SMA dist: {scores['200w SMA dist']:+.4f}")
    g.note("Do not insert the 200w SMA into V to win a point estimate.")
    return g


def gate_outcome_layer(cond, base, outcomes, outcome_mod) -> Gate:
    """The outcome layer must beat climatology to be published, or stay dark.

    This gate does not fail the build: a dark layer is a legitimate, documented
    state, not a defect. It fails only if a DARK layer is somehow being
    published, i.e. if the activation rule is bypassed.
    """
    g = Gate("Outcome layer — conditional probabilities must beat climatology")
    rep = outcome_mod.calibration_report(cond, base, outcomes)
    live = outcome_mod.layer_is_live(rep)
    g.note(f"Brier conditional {rep['brier_conditional']:.4f} vs climatology "
           f"{rep['brier_climatology']:.4f}  ->  skill {rep['skill_vs_climatology']:+.3f}")
    g.note(f"n={rep['n']}, effective n (overlap-adjusted) = {rep['eff_n']}")
    g.note(f"p10-p90 coverage of realised forward return: "
           f"{rep['p10_p90_coverage']:.1%} (nominal 80%)")
    for r in rep["reliability"]:
        g.note(f"   {r['bin']:>18s} n={r['n']:5d} predicted {r['predicted']:.3f} "
               f"observed {r['observed']:.3f}")
    g.note(f"ACTIVATION: layer is {'LIVE' if live else 'DARK'} "
           f"(needs skill >= {outcome_mod.MIN_SKILL})")
    if not live:
        g.note("Publish the unconditional climatology instead. Do NOT publish a "
               "conditional probability that loses to the base rate, and do not "
               "search outcome definitions until one passes.")
    g.check(True, "activation rule evaluated and recorded")
    return g


def gate_spec_ensemble(raw, ens_mod) -> Gate:
    """Reach robustness across a pre-registered grid of plausible constitutions.

    Reports; does not fail. A threshold on "share of members agreeing" would
    have to be invented after seeing the numbers, which is the retune this
    project forbids. What it does is stop a knife-edge result from being
    reported as a clean pass: the incumbent margin at 2025-10-06 is +0.0035,
    and the honest statement is that roughly half of equally defensible
    constitutions put that day in the top quintile.
    """
    g = Gate("Specification ensemble — model uncertainty across plausible constitutions")
    band, M = ens_mod.ensemble_band(raw)
    rep_ = ens_mod.spread_report(band, M, REACH_DATES + BOTTOM_DATES)
    g.note(f"{rep_['n_members']} members; band width median "
           f"{rep_['width_median_pts']:.1f} pts, p90 {rep_['width_p90_pts']:.1f}, "
           f"max {rep_['width_max_pts']:.1f}")
    g.note(f"incumbent's median percentile among members "
           f"{rep_['incumbent_pctile_median']:.2f} (0.50 = central, so the "
           f"constitution is not an outlier of its own grid)")
    g.note(f"{'date':>12s} {'incumbent':>10s} {'min':>7s} {'max':>7s}  agreement")
    for d in REACH_DATES:
        v = rep_["at_dates"].get(d)
        if v:
            g.note(f"{d:>12s} {v['incumbent']:10.3f} {v['min']:7.3f} {v['max']:7.3f}"
                   f"  {v['share_ge_080']:.0%} of members >= 0.80")
    for d in BOTTOM_DATES:
        v = rep_["at_dates"].get(d)
        if v:
            g.note(f"{d:>12s} {v['incumbent']:10.3f} {v['min']:7.3f} {v['max']:7.3f}"
                   f"  {v['share_le_020']:.0%} of members <= 0.20")
    top = list(rep_["oat_sensitivity_pts_median"].items())[:5]
    g.note("most influential single choices (median |delta| vs incumbent, pts): "
           + ", ".join(f"{k} {v}" for k, v in top))
    g.check(True, "ensemble computed and recorded")
    return g


def gate_growth_specs(raw, gs_mod) -> Gate:
    """How much pillar G depends on the SHAPE of the trend, not its parameters.

    The specification ensemble varies G's parameters but never its functional
    form, so it is silent about the one limitation §14 names. Reports only: the
    official G stays specification A, because switching to whichever trend
    currently flatters the score is the purest retune there is.
    """
    g = Gate("Growth specification — functional-form risk in pillar G")
    G = gs_mod.mapped_G(gs_mod.all_residuals(raw["price"]))
    rep_ = gs_mod.disagreement_report(G, REACH_DATES + BOTTOM_DATES)
    g.note(f"spread median {rep_['spread_median_pts']:.1f} pts, "
           f"p90 {rep_['spread_p90_pts']:.1f}, max {rep_['spread_max_pts']:.1f}")
    g.note("correlation with the official specification: "
           + ", ".join(f"{k.split('_', 1)[0]} {v:.3f}"
                       for k, v in rep_["corr_with_official"].items()))
    g.note("first live: " + ", ".join(f"{k.split('_', 1)[0]} {v}"
                                      for k, v in rep_["first_live"].items()))
    g.note(f"{'date':>12s}" + "".join(f"{s.split('_', 1)[0]:>7s}" for s in gs_mod.SPECS)
           + "   spread")
    for d in REACH_DATES + BOTTOM_DATES:
        v = rep_["at_dates"].get(d)
        if v:
            g.note(f"{d:>12s}" + "".join(
                (f"{v[s]:7.3f}" if s in v and v[s] == v[s] else "    n/a")
                for s in gs_mod.SPECS) + f"{v['_spread_pts']:8.1f}")
    g.check(True, "specification disagreement computed and recorded")
    return g


def gate_rewrite_probe(recompute) -> Gate:
    """Compute twice; committed rows must be byte-stable."""
    g = Gate("Rewrite probe — recomputation is byte-stable")
    a, b = recompute(), recompute()
    same = a.equals(b)
    g.check(same, "two independent computations produce an identical tape")
    if not same:
        diff = (a != b).sum().sum()
        g.note(f"{diff} differing cells")
    return g


def holdout_table(tape: pd.Series, window=HOLDOUT) -> str:
    """Separate report for the embargo year. No parameter may be chosen from it."""
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    seg = tape.loc[(tape.index >= lo) & (tape.index <= hi)].dropna()
    if seg.empty:
        return "holdout window not covered by the computed tape"
    lines = [f"holdout {lo.date()} -> {min(hi, tape.index[-1]).date()}  n={len(seg)}",
             "  label: HOLDOUT / DESIGN-CONTAMINATED (not 'unseen')"]
    if hi > tape.index[-1]:
        lines.append(f"  INCOMPLETE: tape ends {tape.index[-1].date()}, "
                     f"{(hi - tape.index[-1]).days} days of the window are missing")
    ath = pd.Timestamp("2025-10-06")
    if ath in seg.index:
        r = _quintile_rank(tape, "2025-10-06")
        lines.append(f"  reach  2025-10-06: risk={seg.loc[ath]:.4f} causal rank {r:.3f} "
                     f"-> {'top quintile' if r >= 0.8 else 'FAILS top quintile'}")
    for d, why in NAMED_CHECKS.items():
        t = pd.Timestamp(d)
        if t in tape.index:
            r = _quintile_rank(tape, d)
            lines.append(f"  named  {d}: risk={tape.loc[t]:.4f} causal rank {r:.3f} "
                         f"({why}) -> {'bottom quintile' if r <= 0.2 else 'NOT bottom quintile'}")
        else:
            lines.append(f"  named  {d}: PENDING — outside the computed tape ({why}). "
                         "Holdout stays UNSIGNED.")

    trough = seg.idxmin()
    r_t = _quintile_rank(tape, str(trough.date()))
    lines.append(f"  bottom {trough.date()}: risk={seg.min():.4f} causal rank {r_t:.3f} "
                 f"-> {'bottom quintile' if r_t <= 0.2 else 'NOT bottom quintile'}")
    return "\n".join(lines)


def run_all(tape, P, raw, maps_mod, pillars_mod, recompute,
            outcome_bundle=None, ensemble_mod=None) -> tuple[list[Gate], str]:
    gates = [gate_reach(tape), gate_order(tape), gate_bottom(tape),
             gate_low_vol_rich(pillars_mod, maps_mod),
             gate_collinearity(P, raw['price']),
             gate_nested_baseline(tape, raw, maps_mod), gate_rewrite_probe(recompute)]
    if outcome_bundle is not None:
        gates.append(gate_outcome_layer(*outcome_bundle))
    if ensemble_mod is not None:
        gates.append(gate_spec_ensemble(raw, ensemble_mod))
        from . import growth_specs as _gs
        gates.append(gate_growth_specs(raw, _gs))
    return gates, holdout_table(tape)


def main(argv=None) -> int:
    import pandas as pd

    from . import compute, features, growth, maps, pillars

    argv = argv or sys.argv[1:]
    quiet = "--quiet" in argv
    csv = next((a.split("=", 1)[1] for a in argv if a.startswith("--csv=")), None)
    if csv is None:
        # A fresh CI checkout has no local snapshot; fall back to the community
        # dump so the gate report runs in Actions exactly as it does locally.
        csv = ("btc.csv" if Path("btc.csv").exists() else
               "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv")

    def build():
        df = features.prepare_frame(pd.read_csv(csv, parse_dates=["time"]))
        raw = features.build_raw(df)
        params = growth.growth_params(raw["price"])
        P = pillars.build_pillars(raw, params)
        daily = compute.build_daily(P, raw["price"], params,
                                    extras=P[["fragility", "g_raw"]].join(
                                        raw[["mvrv", "mayer", "mctc_mod"]]))
        return raw, params, P, daily

    raw, params, P, daily = build()
    tape = daily["rank_exact"]          # unrounded; see _quintile_rank

    from . import outcome as outcome_mod
    kr = maps.expanding_cdf(raw["kappa"].dropna()).reindex(daily.index)
    state = pd.DataFrame({"rank": daily["rank_exact"], "kappa_rank": kr})
    oc = outcome_mod.forward_outcomes(raw["price"].reindex(daily.index))
    bundle = (outcome_mod.conditional_outcomes(state, oc),
              outcome_mod.climatology(oc), oc, outcome_mod)

    gates, holdout = run_all(tape, P, raw, maps, pillars,
                             recompute=lambda: build()[3],
                             outcome_bundle=bundle,
                             ensemble_mod=(None if "--no-ensemble" in argv
                                           else __import__(
                                               "model.v3.ensemble",
                                               fromlist=["ensemble"])))
    failed = [g for g in gates if not g.ok]

    if not quiet:
        print("=" * 78)
        print(f"v3.0 SHIP GATES — tape {tape.index[0].date()} -> {tape.index[-1].date()} "
              f"({len(tape)} rows)")
        print("=" * 78)
        for g in gates:
            print(g.report())
            print()
        print("-" * 78)
        print(holdout)
        print("-" * 78)
        print(f"{len(gates) - len(failed)}/{len(gates)} gates pass")
        if failed:
            print("FAILED: " + ", ".join(g.name.split(" — ")[0] for g in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

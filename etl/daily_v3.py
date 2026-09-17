#!/usr/bin/env python3
"""Daily v3.0 ETL — the only process allowed to write the official tape.

    python3 etl/daily_v3.py --bootstrap     # once: write the full causal history
    python3 etl/daily_v3.py                 # daily: append today's row only
    python3 etl/daily_v3.py --dry-run       # compute and report, write nothing
    python3 etl/daily_v3.py --health-only   # re-run the health check on the tape

Guarantees, in the order they are enforced:

1. **No future data.** The computed frame is asserted to contain no timestamp
   later than the current UTC date before anything is written.
2. **Append-only.** Rows already in `series/v3.0.jsonl` are never rewritten,
   re-ordered or re-serialised. The writer opens the file in append mode and
   only ever emits dates strictly later than the last committed `asof_date`.
   This is the point of the whole design: expanding CDFs mean a recompute today
   would move 2017's rank, so the committed row is frozen and a recompute is a
   diagnostic, never a publisher.
3. **Reproducibility per row.** Each row carries `input_hash` (SHA-256 of that
   day's exact input vector), `git_sha`, `schema_version`, and the growth
   parameters `a`, `b`, `fit_asof`, `n_fit` in force that morning.
4. **Health, not silence.** A large jump, a stale feed or a missing family is
   reported and exits non-zero so the Action surfaces it. It never triggers a
   rewrite, a refit or a weight change.

This script does NOT touch `data.json`, `data.js`, the dashboard, the API, the
email engine or the bot. v2 keeps running untouched until the Phase 5 cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.v3 import (compute, ensemble, features, growth,           # noqa: E402
                      growth_specs, outcome, pillars, rcap_alt)
from model.v3 import constants                                       # noqa: E402
from model.v3.constants import NEED_V3, SCHEMA_VERSION               # noqa: E402

### The input loader lives in etl/inputs.py, so the gate report and the daily
### append cannot drift onto two different sources. Re-exported here because
### `load_inputs`, `CSV_URL` and `CM_API` were part of this module's surface.
from etl.inputs import (CM_API, CSV_URL, _fetch_api,   # noqa: E402,F401
                        load_inputs)

TAPE = Path("series/v3.0.jsonl")
WINDOW = Path("series/v3.0_last90.json")
WINDOW_DAYS = 90
BOOTSTRAP_MAX_LAG_DAYS = 3   # a bootstrap on stale inputs is unrecoverable
JUMP_ALERT = 12          # |delta risk100| above this is flagged for a human
STALE_HOURS = 36         # §9.1: any required field older than this sets stale=1
PAGE_AFTER_DAYS = 7      # §9.2: a field missing this long pages the maintainer
PRICE_ALT_TOL = 0.03     # §9.2: flag the row when the two sources differ by >3%
PRICE_ALT_URL = ("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
                 "?vs_currency=usd&days=2&interval=daily")


# --------------------------------------------------------------------------- #
#  Input
# --------------------------------------------------------------------------- #
def fetch_price_alt() -> tuple[pd.Timestamp, float] | None:
    """A second, independent daily close (§9.2). Keyless, best-effort.

    Stored as `price_alt` and compared with the official Coin Metrics price; the
    row is flagged when they diverge by more than 3%. The two are NEVER averaged
    -- that would invent a third price nobody publishes. If the source is
    unreachable the row simply carries price_alt = null, which is honest: no
    second opinion was available that morning.
    """
    import urllib.request
    try:
        req = urllib.request.Request(PRICE_ALT_URL,
                                     headers={"User-Agent": "btc-risk-v3/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
        pts = j.get("prices") or []
        if not pts:
            return None
        ts, px = pts[-1]
        return pd.Timestamp(ts, unit="ms").normalize(), float(px)
    except Exception as e:                            # noqa: BLE001
        print(f"[data] price_alt unavailable ({type(e).__name__}); row will carry null")
        return None


def input_hash(row: pd.Series) -> str:
    """SHA-256 of the exact input vector, so a restatement is detectable.

    Values are formatted with fixed precision: float repr differs across numpy
    versions, and a hash that changes when the runtime changes is worthless.
    """
    payload = "|".join(f"{c}={row[c]:.10g}" if pd.notna(row[c]) else f"{c}=NA"
                       for c in NEED_V3)
    return hashlib.sha256(payload.encode()).hexdigest()


# --------------------------------------------------------------------------- #
#  Compute
# --------------------------------------------------------------------------- #
def build_tape(df_raw: pd.DataFrame, price_alt: bool = True,
               with_rcap_alt: bool = False, with_ensemble: bool = True) -> pd.DataFrame:
    """Full causal tape. Which rows get *published* is decided by the appender."""
    today = pd.Timestamp(datetime.now(timezone.utc).date())

    # Guard the RAW frame first. prepare_frame drops rows with no price, so a
    # future-dated row with a missing field would vanish silently instead of
    # being reported -- and a vendor that starts publishing tomorrow's date is
    # exactly the event this assertion exists to catch.
    incoming = pd.to_datetime(df_raw["time"]).max()
    if pd.notna(incoming) and pd.Timestamp(incoming).normalize() > today:
        raise AssertionError(
            f"future timestamp in raw input: {pd.Timestamp(incoming).date()} > {today.date()}")

    df = features.prepare_frame(df_raw)
    if df.index.max() > today:
        raise AssertionError(
            f"future timestamp after preparation: {df.index.max().date()} > {today.date()}")

    raw = features.build_raw(df)
    params = growth.growth_params(raw["price"])
    P = pillars.build_pillars(raw, params)

    # §9.1: ANY required field older than 36h sets stale, not just thermocap's.
    stale = (raw["input_stale_days"] > (STALE_HOURS / 24)).astype(float)
    extras = P[["fragility", "g_raw"]].join(
        raw[["mvrv", "mayer", "mctc_mod", "sigma30", "sigma365", "kappa",
             "thermo_stale_days", "input_stale_days"]])
    tape = compute.build_daily(P, raw["price"], params, stale=stale, extras=extras)

    # Model uncertainty: the same tape recomputed under a pre-registered grid of
    # equally plausible constitutions. Answers "how much does today's number
    # depend on choices nobody tested", which the family-disagreement band does
    # not. ~10s for 33 members; no member is ever selected.
    if with_ensemble:
        eb, _M = ensemble.ensemble_band(raw)
        for c in ("ens_min", "ens_p10", "ens_p90", "ens_max", "ens_n"):
            tape[c] = eb[c].reindex(tape.index)
        print(f"[ensemble] {int(eb['ens_n'].iloc[-1])} members; last-row band "
              f"{100 * eb['ens_min'].iloc[-1]:.0f}-{100 * eb['ens_max'].iloc[-1]:.0f}")

    # Pillar G's functional-form risk (§14), published per row. The official G is
    # always specification A; the spread is disclosure, never a switch.
    if with_ensemble:
        Gspecs = growth_specs.mapped_G(growth_specs.all_residuals(raw["price"]))
        live = Gspecs.reindex(tape.index)
        tape["g_spec_min"] = live.min(axis=1)
        tape["g_spec_max"] = live.max(axis=1)
        tape["g_spec_n"] = live.notna().sum(axis=1)
        print(f"[growth_specs] last-row G across {int(tape['g_spec_n'].iloc[-1])} "
              f"specifications: {tape['g_spec_min'].iloc[-1]:.3f}-"
              f"{tape['g_spec_max'].iloc[-1]:.3f}")

    # Unconditional climatology: the causal base rate of a >30% drawdown within
    # 180 days, conditioned on NOTHING. Calibrated by construction. This is what
    # the product publishes in place of the conditional outcome layer, which was
    # measured at skill -0.131 and ships dark (decision 24).
    oc = outcome.forward_outcomes(raw["price"].reindex(tape.index))
    clim = outcome.climatology(oc)
    tape["clim_p_dd30_180"] = clim["p_dd"].reindex(tape.index)
    tape["clim_ret_p50_180"] = clim["p50"].reindex(tape.index)

    # Composite grid on the rank's true reference set, for the window file.
    global _COMPOSITE_GRID
    _sm = compute.ema(compute.combine_raw(P, compute.WEIGHTS)).clip(0, 1).dropna()
    if _sm.size >= 400:
        _COMPOSITE_GRID = [round(float(v), 8) for v in
                           np.quantile(_sm.to_numpy(), np.linspace(0, 1, 201))]

    tape["input_hash"] = [input_hash(df.loc[d]) for d in tape.index]
    tape["git_sha"] = os.environ.get("GITHUB_SHA", "local")
    # Second realized-cap construction: diagnostics only, never a vote, never a
    # swap. Off unless asked for, so the daily job gains no new failure mode.
    tape["R_alt"] = None
    tape["MVRV_alt"] = None
    tape["mvrv_gap"] = None
    tape["rcap_alt_hash"] = None
    if with_rcap_alt:
        r_alt = rcap_alt.fetch_rcap_alt()
        if r_alt is not None:
            alt = rcap_alt.mvrv_alt(df["CapMrktCurUSD"].astype(float), r_alt)
            common = tape.index.intersection(alt.index)
            tape.loc[common, "R_alt"] = alt.loc[common, "R_alt"]
            tape.loc[common, "MVRV_alt"] = alt.loc[common, "MVRV_alt"]
            tape.loc[common, "mvrv_gap"] = (
                df.loc[common, "CapMVRVCur"].astype(float)
                / alt.loc[common, "MVRV_alt"] - 1.0)
            tape.loc[common, "rcap_alt_hash"] = [
                rcap_alt.alt_hash(alt.loc[d]) for d in common]
            rep_gap = rcap_alt.gap_report(
                df["CapMVRVCur"].astype(float).reindex(alt.index), alt)
            print(f"[rcap_alt] {rep_gap}")

    tape["csv_vintage"] = df_raw.attrs.get("csv_vintage")
    tape["api_vintage"] = df_raw.attrs.get("api_vintage")

    # §9.2 second-source price, on the last row only (that is the only day a
    # live quote exists for); older rows carry null rather than a backfill.
    tape["price_alt"] = None
    tape["price_alt_flag"] = 0
    alt = fetch_price_alt() if price_alt else None
    if alt is not None:
        d_alt, px_alt = alt
        if d_alt in tape.index:
            tape.loc[d_alt, "price_alt"] = px_alt
            off = abs(px_alt - tape.loc[d_alt, "price_usd"]) / tape.loc[d_alt, "price_usd"]
            tape.loc[d_alt, "price_alt_flag"] = int(off > PRICE_ALT_TOL)
            if off > PRICE_ALT_TOL:
                print(f"[data][ALERT] price sources differ by {off:.1%} on {d_alt.date()}: "
                      f"CM {tape.loc[d_alt, 'price_usd']:.0f} vs alt {px_alt:.0f}")
    if tape.index.max() > today:
        raise AssertionError("future timestamp in computed tape")
    return tape


def _row_to_json(date: pd.Timestamp, r: pd.Series) -> dict:
    def num(v, nd=None):
        if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
            return None
        return round(float(v), nd) if nd is not None else float(v)

    required = ("schema_version", "git_sha", "input_hash")
    missing = [k for k in required if k not in r.index or pd.isna(r[k])]
    if missing:
        raise KeyError(
            f"row {date.date()} is missing provenance {missing}. Provenance is not "
            "optional: a published row must be reconstructible. Build the frame "
            "through build_tape(), which attaches input_hash and git_sha.")

    def opt(key, nd=None):
        """Optional column: a v3.1 frame need not carry every diagnostic."""
        return num(r[key], nd) if key in r.index else None

    return {
        "asof_date": date.strftime("%Y-%m-%d"),
        "price_usd": num(r["price_usd"], 2),
        "risk100": int(r["risk100"]),
        "risk01": num(r["risk01"], 4),
        "risk_lo": int(r["risk_lo"]),
        "risk_hi": int(r["risk_hi"]),
        "conf": int(r["conf"]),
        "n_live": int(r["n_live"]),
        "stale": int(r["stale"]),
        "spread": num(r["spread"], 6),
        "V": num(r["V"], 6), "G": num(r["G"], 6),
        "T": num(r["T"], 6), "S": num(r["S"], 6),
        "mvrv": opt("mvrv", 6), "mayer": opt("mayer", 6),
        "g_raw": opt("g_raw", 6), "fragility": opt("fragility", 6),
        "sigma30": opt("sigma30", 8), "sigma365": opt("sigma365", 8),
        "kappa": opt("kappa", 6),
        "mctc_mod": opt("mctc_mod", 6),          # demoted: published, does not vote
        "raw01": opt("raw01", 6), "smooth01": opt("smooth01", 6),
        "rank_exact": opt("rank_exact", 6),
        "input_stale_days": (int(r["input_stale_days"])
                             if "input_stale_days" in r.index
                             and pd.notna(r["input_stale_days"]) else None),
        "price_alt": opt("price_alt", 2),
        "price_alt_flag": (int(r["price_alt_flag"])
                           if "price_alt_flag" in r.index
                           and pd.notna(r["price_alt_flag"]) else 0),
        "clim_p_dd30_180": opt("clim_p_dd30_180", 4),
        "clim_ret_p50_180": opt("clim_ret_p50_180", 4),
        "g_spec_lo": opt("g_spec_min", 4), "g_spec_hi": opt("g_spec_max", 4),
        "g_spec_n": (int(r["g_spec_n"]) if "g_spec_n" in r.index
                     and pd.notna(r["g_spec_n"]) else None),
        "ens_lo": (int(round(100 * r["ens_min"]))
                   if "ens_min" in r.index and pd.notna(r["ens_min"]) else None),
        "ens_hi": (int(round(100 * r["ens_max"]))
                   if "ens_max" in r.index and pd.notna(r["ens_max"]) else None),
        "ens_p10": opt("ens_p10", 4), "ens_p90": opt("ens_p90", 4),
        "ens_n": (int(r["ens_n"]) if "ens_n" in r.index
                  and pd.notna(r["ens_n"]) else None),
        "R_alt": opt("R_alt", 2), "MVRV_alt": opt("MVRV_alt", 6),
        "mvrv_gap": opt("mvrv_gap", 6),
        "rcap_alt_hash": (r["rcap_alt_hash"] if "rcap_alt_hash" in r.index
                          and pd.notna(r["rcap_alt_hash"]) else None),
        "csv_vintage": (r["csv_vintage"] if "csv_vintage" in r.index
                        and pd.notna(r["csv_vintage"]) else None),
        "api_vintage": (r["api_vintage"] if "api_vintage" in r.index
                        and pd.notna(r["api_vintage"]) else None),
        "a": opt("a", 6), "b": opt("b", 6),
        "fit_asof": (pd.Timestamp(r["fit_asof"]).strftime("%Y-%m-%d")
                     if "fit_asof" in r.index and pd.notna(r["fit_asof"]) else None),
        "n_fit": (int(r["n_fit"]) if "n_fit" in r.index and pd.notna(r["n_fit"])
                  else None),
        "active_weight": num(r["active_weight"], 4),
        "retired_weight": num(r["retired_weight"], 4),
        "schema_version": r["schema_version"],
        "git_sha": r["git_sha"],
        "input_hash": r["input_hash"],
    }


# --------------------------------------------------------------------------- #
#  Append-only tape
# --------------------------------------------------------------------------- #
def read_tape(path: Path = TAPE) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def append_rows(tape: pd.DataFrame, path: Path = TAPE, bootstrap: bool = False,
                dry_run: bool = False, allow_stale: bool = False) -> list[dict]:
    """Append rows strictly newer than the last committed date. Never rewrite.

    On bootstrap the file must not already exist: re-bootstrapping over a live
    tape is exactly the history rewrite this design forbids.
    """
    existing = read_tape(path)
    if bootstrap and existing:
        raise SystemExit(f"[abort] {path} already has {len(existing)} rows. "
                         "Bootstrap would rewrite published history.")

    # Freshness guard. Bootstrap happens once, ever: --bootstrap refuses to run
    # twice and the tape is append-only, so a tape founded on a stale CSV can
    # never be repaired. If the API top-up silently failed, the newest row is
    # weeks old and this is the last moment anything can stop it. Enforced in
    # code rather than left to whoever reads the log -- a human skimming for
    # "success", or an agent optimising for a green run, will miss it.
    if bootstrap and not tape.empty:
        newest = pd.Timestamp(tape.index.max()).normalize()
        today = pd.Timestamp(datetime.now(timezone.utc).date())
        lag = (today - newest).days
        if lag > BOOTSTRAP_MAX_LAG_DAYS and not allow_stale:
            raise SystemExit(
                f"[abort] refusing to bootstrap on stale inputs: newest row is "
                f"{newest.date()}, {lag} days behind {today.date()} (limit "
                f"{BOOTSTRAP_MAX_LAG_DAYS}). The Coin Metrics API top-up almost "
                f"certainly failed and only the lagging CSV was read. Founding "
                f"the tape here would freeze a truncated series permanently. "
                f"Fix the feed and re-run; use --allow-stale-bootstrap only if "
                f"you intend a deliberately historical tape.")

    last = existing[-1]["asof_date"] if existing else None
    if last is not None:
        seen = {r["asof_date"] for r in existing}
        if len(seen) != len(existing):
            raise SystemExit("[abort] duplicate asof_date already in the tape")

    new = [(d, r) for d, r in tape.iterrows()
           if last is None or d.strftime("%Y-%m-%d") > last]
    if not bootstrap and len(new) > 1:
        print(f"[tape] {len(new)} days since the last commit ({last}) — "
              "catching up; committed rows untouched")

    rows = [_row_to_json(d, r) for d, r in new]
    if dry_run:
        print(f"[dry-run] would append {len(rows)} row(s)")
        return rows

    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:                     # append mode, never "w"
            for row in rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"[tape] appended {len(rows)} row(s); tape now {len(existing) + len(rows)}")
    return rows


# The composite quantile grid must be built from the SAME reference set the
# published rank uses -- every day the composite exists, including the ~400
# warm-up days that never reach the tape. Deriving it from the tape alone shifts
# the browser's inversion by ~4.7 points against the gauge, which is two
# different "current risk" objects on one page. build_tape stashes the true grid
# here; map_support falls back to the tape only if it is absent.
_COMPOSITE_GRID: list | None = None


def map_support(tape_path: Path = TAPE, n_q: int = 201) -> dict:
    """Quantile grids of the raw series behind V, G and T, as of the last row.

    The risk-price matrix has to answer "what price would read risk X today?",
    which means inverting the V, G and T maps in the browser. Those maps are
    expanding empirical CDFs over the WHOLE history, and the free window only
    carries 90 days -- so the window ships a compact quantile grid (201 points)
    of each raw series instead. The browser interpolates F_exp from the grid.

    This is support for the *inversion widget only*. The official score is never
    recomputed in the browser: it is read from the committed row.
    """
    rows = read_tape(tape_path)
    if not rows:
        return {}
    qs = np.linspace(0.0, 1.0, n_q)
    out: dict = {"n": len(rows), "quantile_levels": [round(float(q), 5) for q in qs]}
    # Expanding grid (whole tape) AND the trailing 4-year grid, because V and G
    # are BLENDS of the two maps (0.65/0.35 and 0.70/0.30). Shipping only the
    # expanding grid would make the browser's pillar a different object from the
    # committed one -- measured at 1.6e-2 on G before this was fixed.
    cutoff = pd.Timestamp(rows[-1]["asof_date"]) - pd.Timedelta(days=1460)
    recent = [r for r in rows if pd.Timestamp(r["asof_date"]) >= cutoff]
    for key in ("mvrv", "g_raw", "mayer"):
        vals = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size < 400:
            continue
        out[key] = [round(float(v), 8) for v in np.quantile(vals, qs)]
        v4 = np.array([r[key] for r in recent if r.get(key) is not None], dtype=float)
        v4 = v4[np.isfinite(v4)]
        if v4.size >= 200:
            out[key + "_4y"] = [round(float(v), 8) for v in np.quantile(v4, qs)]
    out["blend"] = {"V": list(constants.BLEND_V), "G": list(constants.BLEND_G)}
    # The published score is the causal RANK of the smoothed blend, so the matrix
    # widget must map its inverted blend through the composite's own distribution.
    # Without this grid the browser would return blend-space numbers while the
    # gauge shows ranks -- three-current-risk-objects again, in a new costume.
    if _COMPOSITE_GRID is not None:
        out["composite"] = _COMPOSITE_GRID
        out["composite_ref"] = "full causal composite history (matches the rank)"
    else:
        comp = np.array([r["smooth01"] for r in rows
                         if r.get("smooth01") is not None], dtype=float)
        comp = comp[np.isfinite(comp)]
        if comp.size >= 400:
            out["composite"] = [round(float(v), 8) for v in np.quantile(comp, qs)]
            out["composite_ref"] = "published tape only (approximate)"
    last = rows[-1]
    # realized price = price / MVRV lets the browser recompute MVRV at a hypothetical
    # price without shipping realized cap; a, b give the growth path for G.
    if last.get("mvrv"):
        out["realized_price"] = round(last["price_usd"] / last["mvrv"], 6)
    out["last"] = {k: last.get(k) for k in
                   ("asof_date", "price_usd", "a", "b", "S", "V", "G", "T",
                    "risk100", "risk01", "risk_lo", "risk_hi", "conf",
                    "n_live", "stale", "spread")}
    out["genesis"] = "2009-01-03"
    out["weights"] = dict(compute.WEIGHTS)
    out["retired_weight"] = float(last.get("retired_weight") or 0.0)
    return out


def window_for(tape_path: Path) -> Path:
    """The window file that belongs to a given tape.

    Derived, never a module constant: `--tape /tmp/x.jsonl` used to compute a
    sandbox tape and then write the window into the REAL `series/` directory, so
    a test run left a 90-row artifact in the working tree that `git add -A` would
    commit. The only process allowed to write the tape must keep every output it
    produces inside the path it was given.
    """
    tape_path = Path(tape_path)
    return tape_path.with_name(tape_path.stem + "_last90.json")


def chart_for(tape_path: Path) -> Path:
    """The public chart series that belongs to a given tape. Derived, never a
    module constant -- same reason as `window_for`."""
    tape_path = Path(tape_path)
    return tape_path.with_name(tape_path.stem + "_chart.json")


def write_chart(path: Path | None = None, tape_path: Path = TAPE,
                dry_run: bool = False) -> dict:
    """Four parallel arrays for the public price-and-risk chart, full history.

    The site had no v3 history to plot: the 90-day window is too short and the
    tape is 4.9 MB, which is not a browser asset. So the chart kept plotting
    `DATA.r` from data.js -- the v2 blend -- underneath a v3 gauge. That is not a
    cosmetic mismatch: 2025-10-06 read 0.556 on the chart against v3's 80/100,
    and 2024-03-13 read 0.676 against 86/100. v2's central failure (a compressed
    -multiple all-time high printing mid-cycle) was being drawn as history under
    the number that exists to correct it.

    Columnar rather than a list of row objects: ~131 KB against 166 KB for the
    data.js already served publicly, so this costs nothing. The free/Pro split is
    untouched -- full rows, pillars, bands and diagnostics stay in the tape and
    the window.

    DERIVED FROM THE COMMITTED TAPE, never recomputed. Rule 1: a recompute today
    would move 2017's rank, so the chart must show what was published.
    """
    path = Path(path) if path is not None else chart_for(tape_path)
    rows = read_tape(tape_path)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("Public chart series, derived from the committed tape. "
                 "r is risk100: an integer 0-100 PERCENTILE of causal history, "
                 "NOT v2's 0-1 blend and NOT a probability. The two scales must "
                 "never share an axis."),
        "scale": {"r": "risk100, integer 0-100, causal percentile rank",
                  "c": "confidence 0-10", "p": "USD close"},
        "n": len(rows),
        "t": [r["asof_date"] for r in rows],
        "p": [r["price_usd"] for r in rows],
        "r": [r["risk100"] for r in rows],
        "c": [r["conf"] for r in rows],
    }
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, separators=(",", ":")))
        kb = path.stat().st_size / 1024
        print(f"[chart] wrote {len(rows)} rows ({kb:.0f} KB) -> {path}")
    return doc


def write_window(path: Path | None = None, tape_path: Path = TAPE,
                 days: int = WINDOW_DAYS, dry_run: bool = False) -> dict:
    """The free-tier window the UI reads. Derived from the tape, never recomputed."""
    path = Path(path) if path is not None else window_for(tape_path)
    rows = read_tape(tape_path)[-days:]
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": days,
        "note": ("Free window. Full tape is series/v3.0.jsonl. "
                 "Committed rows are never rewritten."),
        "map_support": map_support(tape_path),
        "rows": rows,
    }
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, separators=(",", ":")))
        print(f"[window] wrote {len(rows)} rows -> {path}")
    return doc


# --------------------------------------------------------------------------- #
#  Health check — report, never repair
# --------------------------------------------------------------------------- #
def health_check(path: Path = TAPE, jump: int = JUMP_ALERT) -> list[str]:
    rows = read_tape(path)
    alerts: list[str] = []
    if not rows:
        return ["tape is empty"]
    last = rows[-1]

    if len(rows) >= 2:
        d = last["risk100"] - rows[-2]["risk100"]
        if abs(d) > jump:
            alerts.append(f"|delta risk100| = {abs(d)} > {jump} "
                          f"({rows[-2]['asof_date']} -> {last['asof_date']}): "
                          "possible data hole, not a signal")
    if last.get("stale"):
        alerts.append(f"stale=1 on {last['asof_date']}: an on-chain field is older "
                      f"than {STALE_HOURS}h")
    if last.get("n_live", 0) < 4:
        alerts.append(f"n_live={last['n_live']} on {last['asof_date']}: a family is dark")
    sd = last.get("input_stale_days") or 0
    if sd >= PAGE_AFTER_DAYS:
        alerts.append(f"PAGE THE MAINTAINER: a required field has been missing "
                      f"{sd} days on {last['asof_date']} (§9.2). Do not rewrite the "
                      f"tape, do not refit, do not change a weight.")
    if last.get("price_alt_flag"):
        alerts.append(f"price sources diverge >3% on {last['asof_date']} "
                      f"(CM {last.get('price_usd')} vs alt {last.get('price_alt')})")

    today = datetime.now(timezone.utc).date()
    lag = (today - datetime.strptime(last["asof_date"], "%Y-%m-%d").date()).days
    if lag > 2:
        alerts.append(f"tape is {lag} days behind today ({last['asof_date']})")

    dates = [r["asof_date"] for r in rows]
    if dates != sorted(dates):
        alerts.append("asof_date is not monotonically increasing")
    if len(set(dates)) != len(dates):
        alerts.append("duplicate asof_date in the tape")

    print(f"[health] last={last['asof_date']} risk100={last['risk100']} "
          f"band={last['risk_lo']}-{last['risk_hi']} conf={last['conf']} "
          f"n_live={last['n_live']} stale={last['stale']}")
    for a in alerts:
        print(f"[health][ALERT] {a}")
    if not alerts:
        print("[health] ok")
    return alerts


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true",
                    help="write the full causal history once (refuses if the tape exists)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--health-only", action="store_true")
    ap.add_argument("--allow-stale-bootstrap", action="store_true",
                    help="permit founding the tape on inputs older than "
                         f"{BOOTSTRAP_MAX_LAG_DAYS} days. Only for a deliberately "
                         "historical tape; the normal answer is to fix the feed.")
    ap.add_argument("--strict-health", action="store_true",
                    help="exit non-zero when the health check alerts. OFF by "
                         "default: in CI a non-zero exit aborts the job before "
                         "the commit step, so a stale-data day would compute a "
                         "row and then throw it away. Alerts belong in a "
                         "dedicated step that runs AFTER the tape is committed.")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--no-api", action="store_true")
    ap.add_argument("--no-ensemble", action="store_true",
                    help="skip the specification ensemble (model-uncertainty band)")
    ap.add_argument("--with-rcap-alt", action="store_true",
                    help="fetch the second realized-cap construction (diagnostics "
                         "only; never votes, never swaps). Unverified from the "
                         "build sandbox -- exercise once in Actions first.")
    ap.add_argument("--tape", default=str(TAPE))
    a = ap.parse_args(argv)
    tape_path = Path(a.tape)

    if a.health_only:
        return 1 if health_check(tape_path) else 0

    df = load_inputs(a.csv, use_api=not a.no_api)
    tape = build_tape(df, with_rcap_alt=a.with_rcap_alt,
                      with_ensemble=not a.no_ensemble)
    print(f"[compute] {len(tape)} causal rows through {tape.index.max().date()}")

    append_rows(tape, tape_path, bootstrap=a.bootstrap, dry_run=a.dry_run,
                allow_stale=a.allow_stale_bootstrap)
    write_window(window_for(tape_path), tape_path, dry_run=a.dry_run)
    write_chart(chart_for(tape_path), tape_path, dry_run=a.dry_run)
    alerts = health_check(tape_path)
    # Report, but do not fail the append: committing the row matters more than
    # surfacing the alert here, and the alert is surfaced by --health-only in a
    # later step that cannot cost us the row.
    return 1 if (alerts and a.strict_health) else 0


if __name__ == "__main__":
    raise SystemExit(main())

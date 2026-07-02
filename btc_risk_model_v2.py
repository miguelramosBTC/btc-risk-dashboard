#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
btc_risk_model_v2.py
====================
Transparent, causal, fully-replicable BTC "Risk Level" (0..1) composite — v2.

This is the reference implementation of the *factor-based* model that the
dashboard (index.html) embeds. It supersedes the 6-component v1 model.

What changed in v2
------------------
v1 used 6 sub-scores (Mayer, log-deviation, fraction-of-ATH, 90d-ROC, MVRV,
Puell). Diagnostics on the full history showed (a) the three pure price-trend
metrics carry a *momentum* sign that partly cancels the on-chain risk signal,
and (b) several Into-the-Cryptoverse (ITC) on-chain signals fill genuine gaps.
v2 therefore:

  * Consolidates price-trend to two signals (Mayer + fraction-of-ATH) and drops
    log-deviation and 90d-ROC (they were noisy momentum, not risk).
  * Adds the ITC on-chain signals, grouped into factors so redundant metrics
    don't multiply-count:
        A valuation : MVRV, MVRV-Z, supply-in-profit*, terminal-price*
        B miner     : Puell
        C security  : Mcap/Thermocap (raw) + its detrended deviation (mCTC)
        D behavior  : RHODL* (holder-age proxy)
        E demand    : transaction fees

Real vs proxy
-------------
Five signals are computed faithfully from the Coin Metrics community feed:
    MVRV, MVRV-Z, Puell, Mcap/Thermocap, transaction fees.
Four ITC signals need coin-age / cost-basis data (Glassnode-class) the feed does
NOT carry; they are implemented here as transparent, clearly-labelled PROXIES at
low weight (marked * above). The core score stays dominated by the five real
signals. This split is disclosed in the dashboard too — it is not silently faked.

Output
------
  * Prints a calibration-anchor table (known tops/bottoms) so you can sanity-check.
  * Writes MODEL config + the historical r[]/c[] arrays as JSON for the dashboard.

Run:  python3 btc_risk_model_v2.py
NOT the official ITC metric. NOT financial advice.
"""

import os, sys, json
import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
#  CONFIG
# --------------------------------------------------------------------------- #
CSV_LOCAL = "btc.csv"
CSV_URL   = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"

# Cutoff date. Default None => compute through the LATEST row in the CSV (full automation).
# Override for reproducible local runs with:  BTC_LAST_DATE=2026-05-23 python3 btc_risk_model_v2.py
LAST_DATE = os.environ.get("BTC_LAST_DATE", "").strip() or None

# Only export rows once at least this many signals are live (drops the early
# warmup where long-window on-chain factors are NaN) — keeps the series causal.
MIN_ACTIVE_EXPORT = 9

ATH_EXP   = 1.5     # ath sub-score = (min(price/cummax,1)) ** ATH_EXP
EMA_SPAN  = 3       # light smoothing of the final composite
SMA_MAYER = 200

# Fixed logistic centers/scales per signal (center c, scale b). sig(x)=1/(1+e^-((x-c)/b)).
# Anchored to ABSOLUTE levels so diminishing metric peaks -> diminishing risk peaks.
PARAMS = {
    "mayer":   (1.60, 0.55),
    "mvrv":    (2.25, 0.90),
    "mvrvz":   (3.20, 1.60),
    "sip":     (0.85, 0.10),   # supply-in-profit proxy (already 0..1; sigmoid sharpens near 0.85)
    "term":    (2.10, 0.85),   # terminal-price proxy ratio
    "puell":   (1.70, 0.95),
    "mctc":    (2.40, 0.80),   # raw log(mcap/thermocap), absolute level
    "mctcmod": (0.30, 0.85),   # detrended deviation of log(mcap/thermocap) from its 2y mean
    "rhodl":   (0.45, 0.55),   # log exchange-inflow ratio (RHODL proxy)
    "fees":    (0.35, 0.55),   # log fee ratio
}

# Per-factor weights (sum to 1.0). Grouped so a factor's total reflects its
# importance, not how many redundant metrics it happens to contain.
WEIGHTS = {
    "mayer": 0.08, "ath": 0.18,                      # trend (consolidated)    0.26
    "mvrv": 0.18, "mvrvz": 0.05, "sip": 0.04, "term": 0.03,  # A valuation     0.30
    "puell": 0.12,                                   # B miner                 0.12
    "mctc": 0.08, "mctcmod": 0.13,                   # C security valuation    0.21
    "rhodl": 0.05,                                   # D holder behavior       0.05
    "fees": 0.06,                                    # E network demand        0.06
}

ANCHORS = {
    "2013-12-04": "TOP", "2015-01-14": "BTM", "2017-12-17": "TOP", "2018-12-15": "BTM",
    "2019-06-26": "top", "2020-03-13": "btm", "2021-04-14": "TOP", "2021-11-10": "TOP",
    "2022-11-21": "BTM", "2024-03-13": "top", "2025-10-10": "high", "2026-05-23": "now",
}


# --------------------------------------------------------------------------- #
#  DATA
# --------------------------------------------------------------------------- #
NEED = ["PriceUSD", "CapMVRVCur", "CapMrktCurUSD", "SplyCur",
        "IssTotUSD", "FeeTotNtv", "FlowInExNtv"]

# Community API host (no key needed for community-tier metrics). A live feed, so it is
# usually fresher than the periodic GitHub CSV dump, which has been stalling.
CM_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def _load_recent_from_cm_api(after):
    """Fetch complete daily rows (all NEED metrics present) at/after `after` from the
    Coin Metrics community API. Returns a DataFrame or None. Never raises to the caller
    via load_data (wrapped in try/except there), so a bad API day just means no top-up."""
    import urllib.request, json, time as _t
    start = (pd.Timestamp(after) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")  # small overlap
    url = (f"{CM_API}?assets=btc&metrics={','.join(NEED)}"
           f"&frequency=1d&page_size=10000&start_time={start}")
    rows = []
    for _ in range(10):  # paginate defensively (community: 10 req / 6s)
        req = urllib.request.Request(url, headers={"User-Agent": "btc-risk-model/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode())
        rows.extend(j.get("data", []))
        nxt = j.get("next_page_url")
        if not nxt:
            break
        url = nxt
        _t.sleep(0.7)
    if not rows:
        return None
    d = pd.DataFrame(rows)
    if "time" not in d.columns or any(c not in d.columns for c in NEED):
        return None  # API doesn't carry all required metrics -> skip top-up
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_localize(None).dt.normalize()
    for c in NEED:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=NEED)                       # only fully-populated rows
    return d[["time"] + NEED] if not d.empty else None


def load_data():
    # 1) Deep history from the CSV (local snapshot if present, else the community dump).
    if os.path.exists(CSV_LOCAL):
        df = pd.read_csv(CSV_LOCAL, parse_dates=["time"])
    else:
        print(f"[data] downloading {CSV_URL}")
        df = pd.read_csv(CSV_URL, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # 2) Top up the recent tail from the community API (set USE_CM_API=0 to disable).
    if os.environ.get("USE_CM_API", "1") != "0" and not df.empty:
        try:
            tail = _load_recent_from_cm_api(df["time"].max())
            if tail is not None and not tail.empty:
                before = df["time"].max()
                df = (pd.concat([df, tail], ignore_index=True)
                        .drop_duplicates(subset="time", keep="last")
                        .sort_values("time").reset_index(drop=True))
                print(f"[data] community API top-up: {before.date()} -> {df['time'].max().date()}")
            else:
                print("[data] community API returned no complete recent rows; using CSV only")
        except Exception as e:
            print(f"[data] community API top-up skipped ({type(e).__name__}: {e}); using CSV only")

    if LAST_DATE:
        df = df[df["time"] <= pd.Timestamp(LAST_DATE)].reset_index(drop=True)
    miss = [c for c in NEED if c not in df.columns]
    if miss:
        sys.exit(f"[data] source is missing required columns: {miss}")
    df = df[df["PriceUSD"].notna()].reset_index(drop=True)
    print(f"[data] {len(df)} rows through {df['time'].max().date()}")
    return df


# --------------------------------------------------------------------------- #
#  MODEL
# --------------------------------------------------------------------------- #
def sig(x, c, b):
    return (1.0 / (1.0 + np.exp(-(x - c) / b))).clip(0, 1)

def compute(df):
    P  = df["PriceUSD"].astype(float)
    ln = np.log(P)

    # ---- price-trend (recomputed live from price) ------------------------- #
    mayer = P / P.rolling(SMA_MAYER, min_periods=50).mean()
    ath   = (P / P.cummax()).clip(0, 1)

    # ---- factor A: valuation --------------------------------------------- #
    mvrv   = df["CapMVRVCur"].astype(float)
    mcap   = df["CapMrktCurUSD"].astype(float)
    rcap   = mcap / mvrv
    sply   = df["SplyCur"].astype(float)
    rprice = rcap / sply
    mvrvz  = (mcap - rcap) / mcap.expanding(min_periods=200).std()
    # supply-in-profit proxy: share of trailing-4y daily closes below today's price
    Wsip = int(365 * 4)
    parr = P.values
    sip  = np.full(len(parr), np.nan)
    for i in range(len(parr)):
        if i >= 120:
            lo = max(0, i - Wsip)
            sip[i] = float((parr[lo:i + 1] < parr[i]).mean())
    sip = pd.Series(sip, index=P.index)
    # terminal-price proxy ratio: price / (realized_price * 21e6 / supply)
    term = P / (rprice * 21e6 / sply)

    # ---- factor B: miner -------------------------------------------------- #
    iss   = df["IssTotUSD"].astype(float)
    puell = iss / iss.rolling(365, min_periods=120).mean()

    # ---- factor C: security-adjusted valuation ---------------------------- #
    fee_usd  = df["FeeTotNtv"].astype(float) * P
    thermo   = (iss.fillna(0) + fee_usd.fillna(0)).cumsum()
    mctc_log = np.log(mcap / thermo)
    # mCTC (interpreted): deviation of log(mcap/thermo) from its trailing 2y mean
    mctc_mod = mctc_log - mctc_log.rolling(int(365 * 2), min_periods=180).mean()

    # ---- factor D: holder behavior (RHODL proxy) -------------------------- #
    flin  = df["FlowInExNtv"].astype(float)
    rhodl = np.log(flin.rolling(30, min_periods=15).mean()
                   / flin.rolling(365, min_periods=120).mean())

    # ---- factor E: network demand ----------------------------------------- #
    fees = np.log(fee_usd.rolling(30, min_periods=15).mean()
                  / fee_usd.rolling(365, min_periods=120).mean())

    raw = {"mayer": mayer, "mvrv": mvrv, "mvrvz": mvrvz, "sip": sip, "term": term,
           "puell": puell, "mctc": mctc_log, "mctcmod": mctc_mod,
           "rhodl": rhodl, "fees": fees}

    # ---- map each to [0,1] ------------------------------------------------ #
    N = pd.DataFrame(index=P.index)
    N["ath"] = ath ** ATH_EXP
    for k, v in raw.items():
        c, b = PARAMS[k]
        N[k] = sig(v, c, b)

    # ---- per-factor weighted blend (renormalize over live signals) -------- #
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, sum(WEIGHTS.values())
    cols = list(WEIGHTS)
    w    = pd.Series(WEIGHTS)
    vals = N[cols]
    wmat = pd.DataFrame(np.tile(w.values, (len(vals), 1)), columns=cols, index=vals.index)
    wmat = wmat.where(vals.notna(), 0.0)
    wsum = wmat.sum(axis=1).replace(0, np.nan)
    blend = (vals.fillna(0) * wmat).sum(axis=1) / wsum
    risk  = blend.ewm(span=EMA_SPAN, adjust=False).mean().clip(0, 1)

    # ---- confidence 0..10 ------------------------------------------------- #
    disp    = vals.std(axis=1, skipna=True)
    agree   = (1 - 2 * disp).clip(0, 1)
    extreme = (2 * (risk - 0.5).abs()).clip(0, 1)
    conf    = (10 * (0.78 * agree + 0.22 * extreme)).round().clip(0, 10)

    out = df[["time", "PriceUSD"]].copy()
    out["risk"] = risk.values
    out["conf"] = conf.values
    for k in cols:
        out["n_" + k] = N[k].values
    out["nact"] = vals.notna().sum(axis=1).values
    return out, cols


# --------------------------------------------------------------------------- #
#  REPORT + EXPORT
# --------------------------------------------------------------------------- #
def report(out):
    hdr = " date         kind   risk conf nact   ath  mvrv mvrvz  sip  term puell mctc mctcmod rhodl fees"
    print("\nCalibration anchors (diminishing tops / low bottoms expected):")
    print(hdr)
    for d, k in ANCHORS.items():
        r = out[out["time"] == d]
        if len(r):
            r = r.iloc[0]
            print(f"{d} {k:>4} {r.risk:6.3f} {int(r.conf):4d} {int(r.nact):4d}  "
                  f"{r.n_ath:5.2f} {r.n_mvrv:4.2f} {r.n_mvrvz:5.2f} {r.n_sip:4.2f} {r.n_term:4.2f} "
                  f"{r.n_puell:4.2f} {r.n_mctc:4.2f} {r.n_mctcmod:6.2f} {r.n_rhodl:4.2f} {r.n_fees:4.2f}")
    q = out["risk"].quantile([0, .05, .25, .5, .75, .95, 1]).round(3).tolist()
    print("\nrisk distribution [min, 5, 25, 50, 75, 95, max]:", q)
    last = out.iloc[-1]
    print(f"LAST {last.time.date()}  risk={last.risk:.4f}  conf={int(last.conf)}")


def export(out, cols, model_path="model_v2.json", series_path="series_v2.json"):
    disp = out[out["nact"] >= MIN_ACTIVE_EXPORT].reset_index(drop=True)
    last = disp.iloc[-1]
    heldSubs = {k: round(float(last["n_" + k]), 6) for k in cols if k not in ("mayer", "ath")}
    MODEL = {
        "weights":  WEIGHTS,
        "params":   {k: [round(PARAMS[k][0], 4), round(PARAMS[k][1], 4)] for k in PARAMS},
        "athExp":   ATH_EXP,
        "emaSpan":  EMA_SPAN,
        "smaMayer": SMA_MAYER,
        "lastDate": str(last.time.date()),
        "lastRisk": round(float(last.risk), 6),     # EMA seed for the live point
        "lastATH":  round(float(disp["PriceUSD"].max()), 2),
        "heldSubs": heldSubs,                        # 9 on-chain sub-scores held for the live point
    }
    SERIES = {
        "t": [d.strftime("%Y-%m-%d") for d in disp["time"]],
        "p": [round(float(x), 2) for x in disp["PriceUSD"]],
        "r": [round(float(x), 4) for x in disp["risk"]],
        "c": [int(x) for x in disp["conf"]],
    }
    json.dump(MODEL,  open(model_path, "w"),  separators=(",", ":"))
    json.dump(SERIES, open(series_path, "w"), separators=(",", ":"))

    # ---- combined artifacts for full automation -------------------------- #
    # data.json: canonical, read by the email engine (getLatestRisk) and any tooling.
    # data.js : tiny browser shim the dashboard loads BEFORE its main <script>, so the
    #           page keeps its synchronous render path untouched (defines DATA + MODEL).
    json.dump({"model": MODEL, "series": SERIES}, open("data.json", "w"), separators=(",", ":"))
    with open("data.js", "w") as f:
        f.write("/* auto-generated daily by btc_risk_model_v2.py - do not edit by hand */\n")
        f.write("var DATA="  + json.dumps(SERIES, separators=(",", ":")) + ";\n")
        f.write("var MODEL=" + json.dumps(MODEL,  separators=(",", ":")) + ";\n")
    print(f"\n[export] {len(SERIES['t'])} rows  {SERIES['t'][0]} -> {SERIES['t'][-1]}")
    print(f"[export] wrote {model_path}, {series_path}, data.json and data.js")
    print("[export] MODEL.heldSubs:", json.dumps(heldSubs))
    return MODEL


def main():
    df = load_data()
    out, cols = compute(df)
    report(out)
    export(out, cols)


if __name__ == "__main__":
    main()

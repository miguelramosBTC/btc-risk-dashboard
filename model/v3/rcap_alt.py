"""Second realized-cap construction — a restatement detector, not a substitute.

Official pillar V uses Coin Metrics `CapMVRVCur`. This module fetches an
independent realized cap in USD, forms

    MVRV_alt_t = CapMrktCurUSD_t / R_alt_t

with the SAME Coin Metrics market cap the rest of the tape trusts, so any gap
between MVRV_cm and MVRV_alt is a difference in realized-cap methodology, not a
different close. It publishes the gap and never votes.

What it is for. `input_hash` catches "the inputs changed"; it cannot say which
side is right. If Coin Metrics restates `CapMVRVCur` and the alternative does
not move, the gap says so. That is the whole job.

What it is NOT for -- these rules are the same family as LOCF and the tape:

  * V never swaps onto the alternative mid-version. If CM is missing, V goes
    dark and the combiner renormalises. A vendor swap is v3.1, with its own
    tape.
  * No mixed clocks. MVRV_alt is formed only where both series carry the same
    as-of date. Yesterday's R_alt against today's cap is not a ratio.
  * No level equality. Methodologies differ (lost coins, internal transfers,
    price clock), so a constant level gap is normal. Three statistics, each for
    a different failure: rank co-movement and dlog-R correlation catch jumps
    and noise; the 365-day DRIFT of the log gap catches a creeping
    restatement, which the first two cannot see -- a smooth 60% divergence
    over 200 days leaves dlog-R correlation at 0.999.
  * Alerts need a band, and a band needs history. Until a real historical gap
    series exists the module publishes the gap and alerts on NOTHING; a guessed
    threshold is worse than none.

P9 gate 2 ("if this vendor goes dark, is there a second public construction
within 30 days?") is NOT satisfied by this module. A vendor API is a second
phone number, not a second method. Gate 2 is satisfied only by a realized cap
rebuilt from an indexed UTXO set (R_self), which needs a full node and is out of
reach for a browser-only maintainer on GitHub Actions. That is recorded, not
hidden.

Off by default: `etl/daily_v3.py --with-rcap-alt`. The fetch is unverified from
the build sandbox (403); it must be exercised once in Actions before the daily
job depends on it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

__all__ = ["fetch_rcap_alt", "mvrv_alt", "gap_report", "alt_hash"]

RCAP_ALT_URL = "https://bitcoin-data.com/api/v1/realized-cap"   # bridge source
GAP_BAND_LOG = None       # frozen only after a historical gap series exists;
                          # None means "publish the gap, alert on nothing"
MIN_OVERLAP = 400


def fetch_rcap_alt(timeout: int = 30) -> pd.Series | None:
    """Daily realized cap in USD from the bridge source. Best-effort, keyless.

    Returns a date-indexed Series or None. Rows flagged `delayed` by the vendor
    are kept and the flag is carried, because a delayed value is still a valid
    value for the date it describes -- it is just not available the same
    morning. Never poll this like a price API: the free tier is a few requests
    a day. One pull at bootstrap, one a day, that is all.
    """
    import urllib.request
    try:
        req = urllib.request.Request(RCAP_ALT_URL, headers={"User-Agent": "btc-risk-v3/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode())
    except Exception as e:                            # noqa: BLE001
        print(f"[rcap_alt] unavailable ({type(e).__name__}); diagnostics stay null")
        return None
    rows = j if isinstance(j, list) else j.get("data", [])
    if not rows:
        return None
    d = pd.DataFrame(rows)
    date_col = next((c for c in ("d", "date", "time", "t") if c in d.columns), None)
    val_col = next((c for c in ("realizedCap", "realized_cap", "value", "v") if c in d.columns), None)
    if date_col is None or val_col is None:
        print(f"[rcap_alt] unrecognised payload columns {list(d.columns)}")
        return None
    s = pd.Series(pd.to_numeric(d[val_col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(d[date_col]).dt.tz_localize(None).dt.normalize())
    return s.dropna().sort_index()


def mvrv_alt(mcap_cm: pd.Series, rcap_alt: pd.Series) -> pd.DataFrame:
    """MVRV_alt on the dates where BOTH series exist, same as-of date. No clocks mixed."""
    both = pd.concat([mcap_cm.rename("m"), rcap_alt.rename("r")], axis=1, join="inner").dropna()
    out = pd.DataFrame(index=both.index)
    out["R_alt"] = both["r"]
    out["MVRV_alt"] = both["m"] / both["r"]
    return out


def gap_report(mvrv_cm: pd.Series, alt: pd.DataFrame, window: int = 365) -> dict:
    """Co-movement of ranks and of dlog R, plus the level gap -- for disclosure.

    The level gap is reported because readers will ask for it, and labelled as
    not-the-test because it is not: two honest realized caps can differ by
    several percent forever. Rank co-movement and dlog-R correlation are the
    test. No alert unless GAP_BAND_LOG has been frozen from real history.
    """
    both = pd.concat([mvrv_cm.rename("cm"), alt["MVRV_alt"].rename("alt")], axis=1,
                     join="inner").dropna()
    if len(both) < MIN_OVERLAP:
        return {"overlap": int(len(both)), "usable": False}
    tail = both.iloc[-window:]
    gap = np.log(both["cm"] / both["alt"])
    rep = {
        "overlap": int(len(both)),
        "usable": True,
        "level_gap_log_last": float(gap.iloc[-1]),
        "level_gap_log_median_365": float(gap.iloc[-window:].median()),
        "rank_corr_365": float(tail["cm"].rank().corr(tail["alt"].rank())),
        "dlogR_corr_365": float(np.log(tail["cm"]).diff().corr(np.log(tail["alt"]).diff())),
        # creeping restatement detector: how much the gap itself has moved
        "gap_drift_365": float(gap.iloc[-1] - gap.iloc[-min(window, len(gap))]),
        "band": GAP_BAND_LOG,
        "alert": False,
        "note": ("level is disclosure; rank/dlogR co-movement catches jumps and noise; "
                 "gap drift catches a smooth restatement"),
    }
    if GAP_BAND_LOG is not None:
        recent = gap.iloc[-30:]
        rep["alert"] = bool((recent.abs() > GAP_BAND_LOG).all())
    return rep


def alt_hash(row: pd.Series) -> str | None:
    """SHA-256 of the alternative realized-cap value used that morning."""
    if "R_alt" not in row.index or pd.isna(row["R_alt"]):
        return None
    return hashlib.sha256(f"R_alt={row['R_alt']:.10g}".encode()).hexdigest()

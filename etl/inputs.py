"""The one input loader.

Deep history from the Coin Metrics community CSV dump, recent tail from the
community API. Everything that needs the model's inputs reads them through
`load_inputs` so that there is ONE loader with ONE behaviour.

Why this is a module of its own
------------------------------
It used to live in `etl/daily_v3.py`, and `model/v3/validate.py` had a second
code path that called `pd.read_csv` directly. That second path quietly read a
*staler source*: the CSV dump has been frozen at 2026-05-24 since May, so every
recompute-based gate was scored on data stopping in May while the tape ran to
September -- and would have kept stopping in May indefinitely. That is a
standing condition, not one-off staleness, and it is exactly the class of bug a
duplicated loader produces. One loader, one behaviour.

Layering
--------
This module imports stdlib, pandas and `model.v3.constants` -- which is itself
import-free -- and NOTHING else. It must never import a compute module. That
keeps the dependency arrow one-way (`etl.inputs -> model.v3.constants`) and
leaves `model.v3.validate -> etl.inputs` cycle-free.

`model/v3/` must not import from `etl/`. `validate.py` is the one exception and
only at the edge: it is a reporting tool, not part of the compute path, nothing
under `model/` imports it except its own tests, and the import is LAZY, inside
`main()`. So `import model.v3.validate` still pulls no ETL code; only running
the gate report does. If the import fails the report falls back to a bare CSV
read and SAYS SO in its header -- a silent downgrade to staler inputs is the
failure this module exists to prevent.

Dependencies stay numpy + pandas (CLAUDE.md). `json` and `urllib.request` are
stdlib and are imported inside the fetch, not at module scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

if __package__:
    from model.v3.constants import NEED_V3
else:                                     # loaded by file path, as the tests do
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from model.v3.constants import NEED_V3

CSV_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
CM_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def _fetch_api(after: pd.Timestamp) -> pd.DataFrame | None:
    """Community API top-up. Returns complete rows only, or None."""
    import time
    import urllib.request

    start = (pd.Timestamp(after) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    url = (f"{CM_API}?assets=btc&metrics={','.join(NEED_V3)}"
           f"&frequency=1d&page_size=10000&start_time={start}")
    rows: list[dict] = []
    for _ in range(10):
        req = urllib.request.Request(url, headers={"User-Agent": "btc-risk-v3/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode())
        rows.extend(j.get("data", []))
        nxt = j.get("next_page_url")
        if not nxt:
            break
        url = nxt
        time.sleep(0.7)
    if not rows:
        return None
    d = pd.DataFrame(rows)
    if "time" not in d.columns or any(c not in d.columns for c in NEED_V3):
        return None
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_localize(None).dt.normalize()
    for c in NEED_V3:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=NEED_V3)
    return d[["time"] + NEED_V3] if not d.empty else None


def load_inputs(csv: str | None = None, use_api: bool = True) -> pd.DataFrame:
    """Deep history from the community CSV, recent tail from the community API.

    Same hybrid as v2: the GitHub CSV dump stalls, so the API carries the tail.
    Coin Metrics community is the only required source (decision of 2026-09-11);
    nothing else enters the daily job.
    """
    # Default to the live dump. Preferring a local btc.csv whenever one happens to
    # exist meant that committing a snapshot -- or leaving one behind from a test
    # run -- would silently freeze the daily job on stale inputs while every log
    # line still read "success". A local file is used only when asked for by name.
    src = csv or CSV_URL
    print(f"[data] reading {src}")
    df = pd.read_csv(src, parse_dates=["time"]).sort_values("time")
    # §9.2: persist both vintages, so a restatement can be traced to its source
    df.attrs["csv_vintage"] = str(df["time"].max().date()) if not df.empty else None
    df.attrs["api_vintage"] = None

    if use_api and not df.empty:
        try:
            tail = _fetch_api(df["time"].max())
            if tail is not None and not tail.empty:
                before = df["time"].max()
                df = (pd.concat([df, tail], ignore_index=True)
                        .drop_duplicates(subset="time", keep="last")
                        .sort_values("time").reset_index(drop=True))
                df.attrs["csv_vintage"] = str(before.date())
                df.attrs["api_vintage"] = str(tail["time"].max().date())
                print(f"[data] API top-up: {before.date()} -> {df['time'].max().date()}")
            else:
                print("[data] API returned no complete rows; CSV only")
        except Exception as e:                       # noqa: BLE001
            print(f"[data] API top-up skipped ({type(e).__name__}: {e}); CSV only")
    return df

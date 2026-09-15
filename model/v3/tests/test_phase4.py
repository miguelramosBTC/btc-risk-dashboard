"""Phase 4 tests — the ETL and the append-only tape.

Run:  python3 -m pytest model/v3/tests -q

The rule under test is the one the whole design rests on: expanding CDFs mean a
recompute today would move 2017's rank, so a committed row is frozen and a
recompute is a diagnostic, never a publisher. These tests try to break that.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
CSV = os.environ.get("BTC_CSV", "btc.csv")
needs_csv = pytest.mark.skipif(not os.path.exists(CSV), reason=f"{CSV} not present")


def _load_etl():
    spec = importlib.util.spec_from_file_location("daily_v3", ROOT / "etl" / "daily_v3.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["daily_v3"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def etl():
    return _load_etl()


@pytest.fixture(scope="module")
def tape(etl):
    return etl.build_tape(pd.read_csv(CSV, parse_dates=["time"]))


# --------------------------------------------------------------------------- #
#  Append-only
# --------------------------------------------------------------------------- #
@needs_csv
def test_bootstrap_then_daily_appends_nothing_new(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    first = p.read_bytes()
    n = len(etl.read_tape(p))
    assert n == len(tape)

    etl.append_rows(tape, p)          # same inputs, same day: nothing to add
    assert p.read_bytes() == first
    assert len(etl.read_tape(p)) == n


@needs_csv
def test_bootstrap_refuses_to_overwrite_a_live_tape(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    with pytest.raises(SystemExit):
        etl.append_rows(tape, p, bootstrap=True, allow_stale=True)


@needs_csv
def test_committed_rows_survive_a_changed_recompute(etl, tape, tmp_path):
    """The core guarantee: history does not move when today's model output does.

    Truncate, commit, then commit again from the *full* tape. Expanding CDFs mean
    the recomputed values for old dates differ — the committed bytes must not.
    """
    p = tmp_path / "v3.0.jsonl"
    cut = tape.index[-400]
    etl.append_rows(tape.loc[:cut], p, bootstrap=True, allow_stale=True)
    before = [json.loads(l) for l in p.read_text().splitlines()]

    etl.append_rows(tape, p)          # catch-up run
    after = [json.loads(l) for l in p.read_text().splitlines()]

    assert after[:len(before)] == before          # byte-identical prefix
    assert len(after) > len(before)
    dates = [r["asof_date"] for r in after]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


@needs_csv
def test_a_later_weight_change_does_not_touch_the_v30_tape(etl, tape, tmp_path):
    """A v3.1 experiment writes its own file; v3.0.jsonl is untouched."""
    from model.v3 import compute, features, growth, pillars

    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    digest = p.read_bytes()

    df = features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"]))
    raw = features.build_raw(df)
    params = growth.growth_params(raw["price"])
    P = pillars.build_pillars(raw, params)
    v31 = compute.build_daily(P, raw["price"], params,
                              weights={"V": 0.40, "G": 0.20, "T": 0.15, "S": 0.15})
    # deliberately built WITHOUT the diagnostic extras: the writer must tolerate
    # a frame that carries fewer optional columns
    assert not v31["risk100"].equals(tape["risk100"])      # it really is different

    # provenance is mandatory even for an experiment; a v3.1 ETL attaches its own
    v31["schema_version"] = "v3.1"
    v31["git_sha"] = "experiment"
    v31["input_hash"] = tape["input_hash"]
    etl.append_rows(v31, tmp_path / "v3.1.jsonl", bootstrap=True, allow_stale=True)
    assert p.read_bytes() == digest
    assert json.loads((tmp_path / "v3.1.jsonl").read_text().splitlines()[-1]
                      )["schema_version"] == "v3.1"


@needs_csv
def test_a_row_without_provenance_is_refused(etl, tape, tmp_path):
    """A published row must be reconstructible; no hash, no publication."""
    bad = tape.drop(columns=["input_hash"])
    with pytest.raises(KeyError, match="provenance"):
        etl.append_rows(bad, tmp_path / "v3.0.jsonl", bootstrap=True, allow_stale=True)


# --------------------------------------------------------------------------- #
#  Causality and provenance
# --------------------------------------------------------------------------- #
@needs_csv
def test_future_timestamps_are_rejected(etl):
    df = pd.read_csv(CSV, parse_dates=["time"])
    complete = df[df["PriceUSD"].notna()]
    future = complete.iloc[[-1]].copy()          # a row with real data, not the NaN tail
    future["time"] = pd.Timestamp.now("UTC").normalize().tz_localize(None) + pd.Timedelta(days=3)
    with pytest.raises(AssertionError):
        etl.build_tape(pd.concat([df, future], ignore_index=True))


@needs_csv
def test_input_hash_is_stable_and_sensitive(etl):
    df = etl.features.prepare_frame(pd.read_csv(CSV, parse_dates=["time"]))
    row = df.iloc[-1]
    assert etl.input_hash(row) == etl.input_hash(row)
    moved = row.copy()
    moved["PriceUSD"] = float(moved["PriceUSD"]) * 1.000001
    assert etl.input_hash(moved) != etl.input_hash(row)


@needs_csv
def test_every_row_carries_its_provenance(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    rows = etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    for r in (rows[0], rows[len(rows) // 2], rows[-1]):
        for k in ("asof_date", "risk100", "risk01", "risk_lo", "risk_hi", "conf",
                  "n_live", "stale", "schema_version", "git_sha", "input_hash",
                  "active_weight", "retired_weight"):
            assert k in r
        assert r["schema_version"] == "v3.0"
        assert len(r["input_hash"]) == 64
        assert r["active_weight"] == 0.9 and r["retired_weight"] == 0.1
    assert rows[-1]["n_fit"] and rows[-1]["fit_asof"]


@needs_csv
def test_window_is_derived_from_the_tape_not_recomputed(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    w = tmp_path / "win.json"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    doc = etl.write_window(w, p, days=90)
    assert doc["window_days"] == 90 and len(doc["rows"]) == 90
    assert doc["rows"] == etl.read_tape(p)[-90:]


# --------------------------------------------------------------------------- #
#  Confidence in the warm-up
# --------------------------------------------------------------------------- #
@needs_csv
def test_one_live_family_cannot_print_high_confidence(etl, tape):
    """A lone sleeve has undefined agreement, not perfect agreement."""
    lone = tape[tape["n_live"] == 1]
    if lone.empty:
        pytest.skip("no single-family rows in this tape")
    assert lone["conf"].max() <= 3
    full = tape[tape["n_live"] == 4]
    assert full["conf"].max() >= 8          # the fix did not flatten everything


# --------------------------------------------------------------------------- #
#  Health check reports, never repairs
# --------------------------------------------------------------------------- #
@needs_csv
def test_health_check_flags_a_jump_and_leaves_the_tape_alone(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    rows = etl.read_tape(p)
    rows[-1]["risk100"] = min(100, rows[-2]["risk100"] + 40)
    p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    before = p.read_bytes()

    alerts = etl.health_check(p)
    assert any("delta risk100" in a for a in alerts)
    assert p.read_bytes() == before          # reported, not repaired


@needs_csv
def test_health_check_flags_stale_and_dark_families(etl, tape, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    rows = etl.read_tape(p)
    rows[-1]["stale"] = 1
    rows[-1]["n_live"] = 3
    p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    alerts = etl.health_check(p)
    assert any("stale=1" in a for a in alerts)
    assert any("n_live=3" in a for a in alerts)


def test_health_check_catches_a_disordered_tape(etl, tmp_path):
    p = tmp_path / "v3.0.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"asof_date": "2026-01-02", "risk100": 40, "conf": 7, "n_live": 4,
         "stale": 0, "risk_lo": 38, "risk_hi": 42},
        {"asof_date": "2026-01-01", "risk100": 41, "conf": 7, "n_live": 4,
         "stale": 0, "risk_lo": 39, "risk_hi": 43},
    ]) + "\n")
    alerts = etl.health_check(p)
    assert any("monotonic" in a for a in alerts)


# --------------------------------------------------------------------------- #
#  Exit-code contract with CI (found 2026-09-14)
# --------------------------------------------------------------------------- #
@needs_csv
def test_append_does_not_fail_the_job_on_a_health_alert(etl, tmp_path):
    """A non-zero exit in Actions aborts the job BEFORE the commit step.

    If the append step failed on a stale feed, the runner would compute the row,
    write it to an ephemeral checkout and then throw it away. Alerts belong in a
    later step that cannot cost us the row.
    """
    p = tmp_path / "v3.0.jsonl"
    rc = etl.main(["--bootstrap", "--allow-stale-bootstrap", "--csv", CSV,
                   "--no-api", "--tape", str(p)])
    assert rc == 0
    assert etl.health_check(p), "this fixture is expected to raise an alert"
    # ... and the alert is still surfaced by the dedicated step
    assert etl.main(["--health-only", "--tape", str(p)]) == 1
    # ... and --strict-health restores the failing behaviour for manual runs
    assert etl.main(["--csv", CSV, "--no-api", "--tape", str(p),
                     "--strict-health"]) == 1


@needs_csv
def test_climatology_is_published_and_calibrated(etl, tape):
    """The dark conditional layer is replaced on the row by the base rate."""
    c = tape["clim_p_dd30_180"].dropna()
    assert len(c) > 3000
    assert c.between(0, 1).all()
    # calibrated by construction: it should track the realised frequency
    oc = etl.outcome.forward_outcomes(tape["price_usd"])
    realised = oc["hit_dd"].mean()
    assert abs(c.iloc[-1] - realised) < 0.15


@needs_csv
def test_bootstrap_refuses_stale_inputs(etl, tape, tmp_path):
    """The one irreversible action, guarded in code rather than in a runbook.

    If the API top-up fails silently the newest row is weeks old, and founding
    the tape there freezes a truncated series forever: --bootstrap refuses to
    run twice and append-only means it can never be repaired. A human skimming
    a log for "success", or an agent optimising for a green run, will miss it.
    """
    p = tmp_path / "v3.0.jsonl"
    with pytest.raises(SystemExit, match="stale inputs"):
        etl.append_rows(tape, p, bootstrap=True)          # sandbox CSV is 114d old
    assert not p.exists(), "nothing may be written when the guard fires"
    # the escape hatch exists, but has to be asked for by name
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    assert len(etl.read_tape(p)) == len(tape)


@needs_csv
def test_daily_append_is_not_subject_to_the_freshness_guard(etl, tape, tmp_path):
    """The guard is about FOUNDING the tape. A daily run on a lagging feed still
    appends what it has; the health check reports the lag."""
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape.iloc[:-10], p, bootstrap=True, allow_stale=True)
    rows = etl.append_rows(tape, p)
    assert len(rows) == 10

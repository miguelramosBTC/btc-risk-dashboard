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

import numpy as np
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

    # Construct staleness explicitly rather than inheriting it from whatever
    # vintage of btc.csv happens to be on disk. CI fetches a FRESH dump, so a
    # test that assumed the fixture was old would pass locally and fail on the
    # runner -- or, worse, silently stop testing the guard at all.
    stale = tape.copy()
    stale.index = stale.index - pd.Timedelta(days=400)
    with pytest.raises(SystemExit, match="stale inputs"):
        etl.append_rows(stale, p, bootstrap=True)
    assert not p.exists(), "nothing may be written when the guard fires"

    # the escape hatch exists, but has to be asked for by name
    etl.append_rows(stale, p, bootstrap=True, allow_stale=True)
    assert len(etl.read_tape(p)) == len(stale)

    # and a FRESH tape bootstraps without the flag, whatever the CSV vintage
    fresh = tape.copy()
    lag = (pd.Timestamp.utcnow().tz_localize(None).normalize() - fresh.index.max()).days
    fresh.index = fresh.index + pd.Timedelta(days=lag)
    q = tmp_path / "fresh.jsonl"
    etl.append_rows(fresh, q, bootstrap=True)
    assert len(etl.read_tape(q)) == len(fresh)


@needs_csv
def test_daily_append_is_not_subject_to_the_freshness_guard(etl, tape, tmp_path):
    """The guard is about FOUNDING the tape. A daily run on a lagging feed still
    appends what it has; the health check reports the lag."""
    p = tmp_path / "v3.0.jsonl"
    etl.append_rows(tape.iloc[:-10], p, bootstrap=True, allow_stale=True)
    rows = etl.append_rows(tape, p)
    assert len(rows) == 10


# --------------------------------------------------------------------------- #
#  Defects found in review, 2026-09-15
# --------------------------------------------------------------------------- #
def test_no_scipy_in_the_append_path(etl):
    """The process that writes the tape must run on numpy + pandas alone.

    `Series.corr(method="spearman")` imports scipy. With scipy absent that turned
    an import error into a non-zero exit on the step whose job is to append a
    row — a dependency failure wearing a gate failure's clothes, and no row ever
    committed. Spearman is Pearson on ranks; the replacement is exact.
    """
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name.split(".")[0] == "scipy":
            raise ModuleNotFoundError("scipy blocked for this test")
        return real(name, *a, **k)

    builtins.__import__ = blocked
    try:
        import importlib

        from model.v3 import validate
        importlib.reload(validate)
        x = pd.Series(np.arange(500.0))
        y = x.iloc[::-1].reset_index(drop=True)
        assert validate._spearman(x, y) == pytest.approx(-1.0, abs=1e-9)
    finally:
        builtins.__import__ = real


def test_spearman_matches_scipy_including_ties(etl):
    pytest.importorskip("scipy")
    from model.v3 import validate
    rng = np.random.default_rng(3)
    a = pd.Series(rng.normal(size=2000))
    b = pd.Series(rng.normal(size=2000))
    a.iloc[::7] = a.iloc[0]          # ties are where naive versions drift
    b.iloc[::11] = b.iloc[3]
    assert validate._spearman(a, b) == pytest.approx(
        a.corr(b, method="spearman"), abs=1e-12)


@needs_csv
def test_tape_path_confines_every_output(etl, tape, tmp_path):
    """--tape must redirect the window too, or a test run writes into series/."""
    p = tmp_path / "sandbox.jsonl"
    assert etl.window_for(p) == tmp_path / "sandbox_last90.json"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    etl.write_window(etl.window_for(p), p)
    assert (tmp_path / "sandbox_last90.json").exists()
    # the real series/ directory must be untouched by a sandbox run
    assert not (tmp_path / "series").exists()


def test_local_csv_is_never_preferred_silently(etl):
    """A stray btc.csv must not hijack the daily job's inputs."""
    import inspect
    src = inspect.getsource(etl.load_inputs)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "src = csv or CSV_URL" in code
    assert 'Path("btc.csv").exists()' not in code


@needs_csv
def test_gates_score_the_committed_tape_not_a_recompute(etl, tape, tmp_path, monkeypatch):
    """§12.1: reach/order/bottom are evaluated on the committed tape.

    Recomputing them scored a different, staler series than the one published:
    the ETL tops up from the API, a bare CSV read stops at the dump. The report
    said 5,191 rows to 2026-05-23 while the tape held 5,307 to 2026-09-16, and
    kept calling the June 2026 low PENDING after it had landed.
    """
    from model.v3 import validate

    p = tmp_path / "series" / "v3.0.jsonl"
    p.parent.mkdir()
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)

    monkeypatch.chdir(tmp_path)
    committed = validate.read_committed_tape("series/v3.0.jsonl")
    assert committed is not None
    assert len(committed) == len(tape)
    assert committed.index.max() == tape.index.max()
    # values must be the unrounded rank, not risk100/100
    assert not np.allclose(committed.to_numpy(),
                           (committed * 100).round().to_numpy() / 100)


def test_missing_tape_is_labelled_not_silently_recomputed(tmp_path, monkeypatch):
    from model.v3 import validate
    monkeypatch.chdir(tmp_path)
    assert validate.read_committed_tape("series/v3.0.jsonl") is None


# --------------------------------------------------------------------------- #
#  The public chart series
# --------------------------------------------------------------------------- #
@needs_csv
def test_chart_series_is_derived_from_the_tape_never_recomputed(etl, tape, tmp_path):
    """The chart must show what was PUBLISHED.

    The site had no v3 history, so the chart kept plotting v2's blend under a v3
    gauge: 2025-10-06 drew 0.556 against v3's 80/100. Fixing that by recomputing
    a history in the browser or the ETL would have been the worse bug -- an
    expanding CDF means a recompute today moves 2017's rank, so the picture
    would disagree with the tape it sits under.
    """
    p = tmp_path / "series" / "v3.0.jsonl"
    p.parent.mkdir()
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    rows = etl.read_tape(p)

    doc = etl.write_chart(etl.chart_for(p), p)
    assert doc["t"] == [r["asof_date"] for r in rows]
    assert doc["r"] == [r["risk100"] for r in rows]     # identity, not similarity
    assert doc["p"] == [r["price_usd"] for r in rows]
    assert doc["c"] == [r["conf"] for r in rows]
    assert doc["n"] == len(rows)


@needs_csv
def test_chart_r_is_the_0_100_percentile_not_the_0_1_blend(etl, tape, tmp_path):
    """The scale change is the thing most likely to be silently undone.

    v2's DATA.r is a 0-1 blend; v3's r is an integer 0-100 percentile. A chart
    that plots one on the other's axis is wrong by a factor of 100 and looks
    plausible, so assert the type and the range rather than trusting the name.
    """
    p = tmp_path / "series" / "v3.0.jsonl"
    p.parent.mkdir()
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    doc = etl.write_chart(etl.chart_for(p), p)

    assert all(isinstance(v, int) for v in doc["r"])
    assert all(0 <= v <= 100 for v in doc["r"])
    assert max(doc["r"]) > 1, "a 0-1 blend leaked in where a 0-100 rank belongs"
    assert "risk100" in doc["note"] and "NOT" in doc["note"]
    assert doc["scale"]["r"].startswith("risk100")


@needs_csv
def test_chart_stays_inside_the_path_it_was_given(etl, tape, tmp_path):
    """Same rule as the window: --tape somewhere else must not write here."""
    p = tmp_path / "sandbox.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    assert etl.chart_for(p) == tmp_path / "sandbox_chart.json"
    etl.write_chart(etl.chart_for(p), p)
    assert (tmp_path / "sandbox_chart.json").exists()
    assert not (tmp_path / "series").exists()


@needs_csv
def test_chart_dry_run_writes_nothing(etl, tape, tmp_path):
    p = tmp_path / "sandbox.jsonl"
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    doc = etl.write_chart(etl.chart_for(p), p, dry_run=True)
    assert doc["n"] == len(etl.read_tape(p))
    assert not (tmp_path / "sandbox_chart.json").exists()


# --------------------------------------------------------------------------- #
#  Committed pillars feed the collinearity gate
# --------------------------------------------------------------------------- #
@needs_csv
def test_committed_pillars_match_the_recompute(etl, tape, tmp_path, monkeypatch):
    """Reading the tape's V/G/T/S must be the same measurement, not a new one.

    Equal to the half-ulp of the 6-decimal rounding `_row_to_json` applies on the
    way out, and no looser: if these ever diverge by more than that, the tape and
    the recompute have stopped being the same quantity and the gate's numbers
    would be comparing two different things.

    The `+ 1e-12` is float representation, not slack. A value sitting exactly on
    the rounding boundary lands at 5.000000000005e-07 once the two decimals are
    subtracted in binary, so a bare `<= 5e-7` fails on a difference that IS the
    half-ulp. The claim is unchanged; only the comparison is made representable.
    """
    from model.v3 import validate

    p = tmp_path / "series" / "v3.0.jsonl"
    p.parent.mkdir()
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    monkeypatch.chdir(tmp_path)

    HALF_ULP_6DP, HALF_ULP_2DP = 5e-7 + 1e-12, 5e-3 + 1e-9
    cp = validate.read_committed_pillars("series/v3.0.jsonl")
    assert cp is not None
    for col in ("V", "G", "T", "S"):
        both = cp[col].dropna().index.intersection(tape[col].dropna().index)
        assert len(both) > 1000
        assert (cp.loc[both, col] - tape.loc[both, col]).abs().max() <= HALF_ULP_6DP
    assert (cp["price_usd"] - tape.loc[cp.index, "price_usd"]).abs().max() <= HALF_ULP_2DP


@needs_csv
def test_collinearity_reads_the_committed_pillars_and_outruns_the_csv(etl, tape, tmp_path, monkeypatch):
    """The gate used to score a CSV recompute that stops at the dump's vintage.

    The Coin Metrics dump has been frozen at 2026-05-24 since May while the API
    top-up reaches the present, so the pillar-based gates were permanently four
    months behind the tape -- a standing condition, not one-off staleness. The
    tape carries V/G/T/S already, so the gate needs no recompute at all.
    """
    from model.v3 import validate

    p = tmp_path / "series" / "v3.0.jsonl"
    p.parent.mkdir()
    etl.append_rows(tape, p, bootstrap=True, allow_stale=True)
    monkeypatch.chdir(tmp_path)

    cp = validate.read_committed_pillars("series/v3.0.jsonl")
    g = validate.gate_collinearity(cp, cp["price_usd"], "COMMITTED tape (published pillars)")
    assert g.ok
    # every printed statistic survives the source change
    assert any("partial (BLOCKER)" in l for l in g.lines)
    assert any("level (disclose)" in l for l in g.lines)
    assert any("raw diff (diagnose)" in l for l in g.lines)
    assert any("price elasticity" in l for l in g.lines)
    assert any("Sigma pairs, reported off-gate" in l for l in g.lines)
    # and the report says which pillars were measured, so a silent swap is visible
    assert any("pillars read from: COMMITTED tape" in l for l in g.lines)


def test_a_tape_without_pillars_falls_back_rather_than_guessing(tmp_path, monkeypatch):
    """A v3.1 frame may drop a diagnostic. Returning None lets the caller say so."""
    from model.v3 import validate

    d = tmp_path / "series"
    d.mkdir()
    (d / "v3.0.jsonl").write_text(
        json.dumps({"asof_date": "2026-01-01", "risk100": 50, "rank_exact": 0.5}) + "\n")
    monkeypatch.chdir(tmp_path)
    assert validate.read_committed_pillars("series/v3.0.jsonl") is None
    assert validate.read_committed_tape("series/v3.0.jsonl") is not None


# --------------------------------------------------------------------------- #
#  One loader
# --------------------------------------------------------------------------- #
def test_there_is_exactly_one_input_loader(etl):
    """Two loaders is how the gate report ended up on a staler source than the
    tape. `etl.load_inputs` must BE `etl.inputs.load_inputs`, not a copy."""
    import etl.inputs as inputs_mod
    assert etl.load_inputs is inputs_mod.load_inputs
    assert etl.CSV_URL is inputs_mod.CSV_URL


def test_the_loader_does_not_import_a_compute_module():
    """Keeps `model.v3.validate -> etl.inputs` cycle-free, and keeps the daily
    append's dependency surface at numpy + pandas."""
    src = (ROOT / "etl" / "inputs.py").read_text()
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for banned in ("import compute", "import pillars", "import features",
                   "import growth", "import ensemble", "from model.v3 import",
                   "import scipy", "import statsmodels"):
        assert banned not in body, f"etl/inputs.py must not carry `{banned}`"
    assert "from model.v3.constants import" in body      # constants only


def test_validate_imports_the_loader_lazily():
    """`import model.v3.validate` must not drag the ETL in: model/ does not
    depend on etl/ except at validate's CLI edge, inside main()."""
    import ast

    src = (ROOT / "model" / "v3" / "validate.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:                               # module scope only
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            names = " ".join(a.name for a in node.names)
            assert "etl" not in mod and "etl" not in names, \
                "etl imported at module scope; it must be lazy inside main()"
    main_fn = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    lazy = [n for n in ast.walk(main_fn)
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("etl")]
    assert lazy, "main() must import the shared loader"

# v3.0 landing status

The v3 backend is landed and verified. **The frontend cutover has not started** —
`index.html`, `app.js` and the three backend consumers still read v2, and the
live site still serves v2.

Two defects found during verification are open and are **blockers for the daily
job**, not for the model. Both are recorded in full below because
`CLAUDE.md` rule 8 says negative results are results.

---

## Verification, 2026-09-15

All three checks from the drop manifest, run on this tree.

| Check | Result |
|---|---|
| `sha256sum -c MANIFEST.txt` | **33 OK, 0 FAILED** |
| `python3 -m pytest model/v3/tests -q` | **110 passed, 2 skipped, 0 failed** (112 collected) |
| `python3 -m model.v3.validate` | **exit 0 — 10/10 gates pass** |

`series/v2_frozen.json` landed byte-identical: SHA-256
`0de9ddd6fe3f59aabd014042ca642946c725db807bdae47ec833fada1167067c`, 170,040
bytes, as `series/README.md` requires.

**`model.v3.validate` reproduces `docs/gate_report_v3.0.txt` byte-for-byte.**
That is the strongest evidence in this repository that the committed gate report
is the output of the committed code, on the committed constitution.

The two skips are both legitimate and neither is a masked failure:

| Skip | Reason |
|---|---|
| `test_phase2.py:74` | `statsmodels` absent — a test-only cross-check, and `CLAUDE.md` requires it stay optional. |
| `test_phase4.py:182` | *"no single-family rows in this tape"* — decision 18's 400-observation rank warm-up removed every one- and two-family row, so the fixture the test needs cannot exist. Data-dependent by design. |

### Two things the checks need that the repo does not carry

1. **`btc.csv`** — the Coin Metrics snapshot. Without it **63 of 112 tests skip**
   (`btc.csv not present`) and the suite reports a misleading *"49 passed"*.
   Fetch it from the URL in `etl/daily_v3.py`:
   ```
   curl -sSL -o btc.csv https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv
   ```
   It is **gitignored on purpose**: `load_inputs()` prefers a local `btc.csv`
   over the live URL, so a committed snapshot would silently freeze the daily job
   on stale inputs — which looks exactly like success in the log.
2. **`scipy`** — see defect 1.

---

## Defect 1 — `scipy` is undeclared, and CI will fail every day

**`pandas.Series.corr(method="spearman")` requires `scipy`.** It is called six
times in `model/v3/validate.py:262-296` — the **nested-baseline gate** — and once
in `model/v3/tests/test_phase2.py:407`.

`requirements.txt` declares numpy and pandas only. On a clean install:

```
python3 -m pytest model/v3/tests -q   ->  1 failed, 109 passed
                                          ModuleNotFoundError: No module named 'scipy'
python3 -m model.v3.validate          ->  exit 1, same ImportError
```

That is not a gate failure. It is an import error wearing a gate failure's exit
code — which is worse, because `validate.py`'s contract is *"exit non-zero on any
failure"* and a reader sees a non-zero exit and assumes a gate broke.

**In `.github/workflows/btc-data-v3.yml` this stops the tape.** The job installs
`-r requirements.txt pytest`, and its second step —

```yaml
- name: Unit tests (a broken model must not write a row)
  run: python3 -m pytest model/v3/tests -q
```

— has no `continue-on-error`. It fails, and **no row is ever appended.** The
`Ship gates` step would also fail, but that one is `continue-on-error: true`.

**The daily append path itself does not need scipy.** Verified by running the ETL
with `scipy` blocked at import:

```
[compute] 5191 causal rows through 2026-05-23
[dry-run] would append 5191 row(s)
[exit] 0
```

No module under `model/` or `etl/` imports scipy directly. So `CLAUDE.md`'s
*"dependencies are numpy and pandas only… the daily job must not grow a
dependency it does not need"* is **still true of the job that writes the tape**.
Only the test and gate surfaces need scipy.

**Not fixed here.** The three repairs differ in what they cost, and rule 9
reserves the choice:

| Option | Cost |
|---|---|
| Add `scipy` to `requirements.txt` | Simplest, but grows the daily job's install with a dependency it provably does not need — the thing `CLAUDE.md` closes by forbidding. |
| Install scipy only in the CI test/gate steps (`pip install -r requirements.txt pytest scipy`) | Keeps the runtime at numpy + pandas. Touches a shipped workflow. |
| Replace the six `method="spearman"` calls with a numpy rank implementation | No new dependency anywhere, but it edits gate code, and the replacement must reproduce `docs/gate_report_v3.0.txt` to the digit or the change is invisible tampering with a gate. |

---

## Defect 2 — `--tape` does not redirect the window, so a test writes into `series/`

`etl/daily_v3.py:612`:

```python
write_window(WINDOW, tape_path, dry_run=a.dry_run)
```

`--tape` redirects `tape_path`. It does **not** redirect `WINDOW`, which stays the
module constant `Path("series/v3.0_last90.json")` (line 51). So any `main()` call
that redirects the tape still writes the window to the **real repository path**.

`model/v3/tests/test_phase4.py:234 test_append_does_not_fail_the_job_on_a_health_alert`
does exactly that, three times, with `dry_run` false. Reproduced in isolation:

```
$ ls series/                 # README.md  v2_frozen.json
$ pytest ".../test_phase4.py::test_append_does_not_fail_the_job_on_a_health_alert" -q
1 passed
$ ls series/                 # README.md  v2_frozen.json  v3.0_last90.json   <-- leaked
```

The leaked file is real-looking — 90 rows, `map_support.n = 5191`, last row
2026-05-23 — but it is derived from a **deliberately stale fixture tape**, built
with `--allow-stale-bootstrap --no-api` from a CSV the same test asserts is 114
days old.

**Why it matters.** `series/README.md` states the rule this breaks: *"Nothing in
this directory is a build output… Any workflow that writes here must be reviewed
against this README first."*

- In `btc-data-v3.yml` the normal path is **contained**: the tests run at step 2,
  the real ETL overwrites the window at step 3, and `git add series/…` comes
  after. A green run commits the correct file.
- The exposure is a **local** one, and it is easy to hit: run the tests, then
  `git add -A`, and a fixture-derived window is committed as though the ETL had
  produced it. Nothing in the diff would look wrong.
- `--dry-run` is **not** affected. Verified: the dry run writes nothing.

**Not fixed here.** The obvious repair is to derive the window path from the tape
path, or add a `--window` argument. Either changes the signature of the only
process allowed to write the tape, days before that process founds it — so it is
the maintainer's call, not an agent's. A test-local `monkeypatch` of
`etl.WINDOW` would also work and touches no shipped code, but it fixes the test
rather than the footgun.

**Neither defect was worked around by weakening a test.** Both tests are correct;
one caught a missing dependency and the other caught a path that escapes its
sandbox.

---

## The tape does not exist yet

`series/v3.0.jsonl` has not been founded. `--bootstrap` is the maintainer's to
run, and `CLAUDE.md` forbids an agent from running it.

Local dry run on the CSV alone (`--no-api --csv btc.csv`):

```
[compute] 5191 causal rows through 2026-05-23
[dry-run] would append 5191 row(s)
```

**5,191 through 2026-05-23 is the runbook's abort condition**, and it is expected
here: the Coin Metrics community API is unreachable from this sandbox
(`CONNECT tunnel failed, response 403`), so only the lagging CSV was read. The
real dry run in Actions must report **~5,300 rows ending yesterday or today**. If
it still says 5,191, stop — see `docs/BOOTSTRAP_RUNBOOK.md` §2.

**Consequence for the frontend cutover:** with no tape there is no
`series/v3.0_last90.json`, so `v3.js` sets `V3.active = false` and the page must
serve v2. The v3 rendering path cannot be verified end-to-end against a real
committed row until the tape is founded.

---

## What is committed

| Path | Source |
|---|---|
| `model/` — 11 modules, 8 test files, 2 package markers | drop, hash-verified |
| `etl/daily_v3.py` | drop, hash-verified (supersedes the earlier sample: the composite grid is now built from the rank's true reference set, worth ~4.7 points of browser-vs-gauge drift) |
| `tools/check_copy.py` | drop, hash-verified |
| `v3.js` | drop, hash-verified — **staged, not yet wired into any page** |
| `series/README.md`, `series/v2_frozen.json` | drop, hash-verified |
| `docs/AUDIT_v3.0.md`, `docs/CHANGELOG_v3.md`, `docs/BOOTSTRAP_RUNBOOK.md` | drop, hash-verified |
| `.github/workflows/btc-data-v3.yml`, `.github/workflows/copy-audit.yml` | drop, hash-verified |
| `CLAUDE.md` | drop, hash-verified |
| `docs/MODEL_v3.md`, `docs/SCHEMA_v3.0.md` | written from the above; see below |
| `docs/gate_report_v3.0.txt` | reproduced byte-for-byte by `model.v3.validate` on this tree |

`MANIFEST.txt` was deleted after verification — a transfer artifact, not repo
content. `btc.csv` is gitignored.

Untouched: `index.html`, `app.js`, `style.css`, `data.json`, `data.js`,
`btc_risk_model_v2.py`, `functions/`, `lib/`, `bot/`, `db/`,
`send-notifications.mjs`, `btc-risk-weekly.mjs`, and the three v2 workflows.

### Earlier open questions, now resolved by the drop

- **O1 — M's demotion.** Resolved. `series/README.md`: *"M was demoted on
  2026-09-11 and its budget retired, not recycled."* `active_weight` 0.90 and
  `retired_weight` 0.10 travel on every row. The constitution table in
  `docs/CHANGELOG_v3.md` still lists the pre-demotion five-pillar weights and is
  the one place a reader can still be misled.
- **O2, O3, O4** — verifiable against the landed code; `docs/MODEL_v3.md` and
  `docs/SCHEMA_v3.0.md` were written before it existed and have not yet been
  re-checked against it line by line.

---

## Next

Blocked on a decision for defect 1 before the daily job can ever append.
Defect 2 wants a decision before the bootstrap.

The frontend cutover (`index.html`, `app.js`), the three v2-reading backend
consumers, the P6 copy rewrite and the README note are **not started**.

Current P6 state, for scale: `tools/check_copy.py` exits 1 with **39 defect-G
strings** (hashrate 16, active addresses 11, difficulty 6, LTH/STH 4, implied
probability 2) and **92 cutover-fatal strings** (eleven signals 29, logistic map
17, Puell 15, MVRV-Z 9, RHODL 7, terminal price 7, five factor families 6,
supply in profit 2).

# v3.0 landing status

The v3 backend is landed and verified, the tape is founded and appending, and
**the cutover is complete**: `index.html`, `app.js`, the Cloudflare risk API,
the notification mailer and the Telegram bot all read the last committed row of
`series/v3.0.jsonl`. The copy names only what the model computes.

Two defects found during verification were **blockers for the daily job**, not
for the model. Both are now fixed. Their write-ups are kept in full below
because `CLAUDE.md` rule 8 says negative results are results — a defect that is
deleted once repaired teaches nobody why the guard exists.

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

1. **`btc.csv`** — the Coin Metrics snapshot. Without it **63 of 116 tests skip**
   (`btc.csv not present`) and the suite reports a misleading *"51 passed"* while
   the step whose job is *"a broken model must not write a row"* goes green having
   checked barely half the model. Fetch it from the URL in `etl/daily_v3.py`:
   ```
   curl -sSL -o btc.csv https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv
   ```
   **Fixed in CI**: `btc-data-v3.yml:63` fetches the dump before the test step.
   The gate went from 51 passed / 65 skipped in a few seconds to **115 passed,
   3 skipped in 106 s** — see below.

   `btc.csv` stays **gitignored on purpose**, and a committed snapshot can no
   longer hijack production either way: `load_inputs()` now defaults to the live
   dump and reads a local CSV only when one is passed by name.
2. **`scipy`** — see defect 1. Resolved without adding it.

---

## Defect 1 — `scipy` is undeclared, and CI will fail every day

> **RESOLVED**, and by the third option in the table below — the one with no new
> dependency anywhere. `model/v3/validate.py:94` now carries `_spearman`, Pearson
> on the ranks, and the six gate calls use it. `model/v3/tests/test_phase4.py:351`
> checks it against `Series.corr(method="spearman")` to 1e-12 **including ties**,
> behind `pytest.importorskip("scipy")`, so the equivalence is proven wherever
> scipy exists and skipped where it does not. The daily job still installs numpy
> and pandas only. `CLAUDE.md` now names this trap by name.

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

> **RESOLVED** by deriving the window from the tape, not by patching the test —
> the footgun is gone rather than stepped over. `etl/daily_v3.py:506` defines
> `window_for(tape_path)`, `write_window` defaults to it, and `main()` calls
> `write_window(window_for(tape_path), …)`. A run given `--tape /tmp/x.jsonl`
> writes its window beside that tape. `CLAUDE.md` states the rule: *everything a
> run writes stays inside the path it was given.*

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

## The tape is live

Bootstrapped on the runner 2026-09-17 *(decision 33)*. `series/v3.0.jsonl` holds
**5,307 causal rows, 2012-03-07 → 2026-09-16**, and the 06:05 cron has begun
appending one row a day.

| check | value |
|---|---|
| last row | 2026-09-16 · risk100 **32** · band 24–44 · conf 7 |
| `n_live` / `stale` | 4 / 0 |
| `csv_vintage` → `api_vintage` | 2026-05-24 → 2026-09-16 (they differ, as they must) |
| `ens_lo`–`ens_hi` | 27–34 across 33 members |
| `g_spec_lo`–`g_spec_hi` | 0.228–0.477 across 5 specifications |
| `price_alt` | **null**, flag 0 |

`price_alt` null is not a failure: the second public close was unreachable from
the runner, and the row records that no second opinion was available rather than
inventing one. A `price_alt_flag` of **1** would be the thing to investigate.

**The holdout is complete and its named check landed.** `2026-06-30` — the first
v3 out-of-sample bottom — prints causal rank **0.104**, inside the bottom
quintile; the holdout trough (2026-07-01) prints 0.104 too. The window is now
n = 365 and the `INCOMPLETE` line is gone. The label does **not** change: the
changelog still calls this stretch design-contaminated, and one bottom is not a
distribution. Real out-of-sample begins at the next ATH after the tape is frozen.

**`docs/gate_report_v3.0.txt` is now written by the job, on every run.** It used
to be a hand-committed file that went stale the moment the tape moved — it sat
at a 5,191-row pre-bootstrap recompute for two days after the tape reached
5,307. The ship-gates step now tees `model.v3.validate` into it and the commit
step stages it with the tape, so the published report can only ever describe the
published tape.

The step runs under `bash -eo pipefail {0}`. GitHub's default is `bash -e {0}`,
where a pipeline takes `tee`'s exit code — so a **failing** gate report would
have been committed under a **green** step. Verified locally:

```
with pipefail:    exit=1
without pipefail: exit=0
```

The regeneration ran on 2026-09-17 (run 35233448299). Header now reads
`scored on : COMMITTED tape series/v3.0.jsonl — 5307 rows, 2012-03-07 ->
2026-09-16`; 10/10 gates pass; the holdout block prints `n=365` with
2026-06-30 at rank 0.104. That run appended **no** tape row — the Coin Metrics
API vintage was still 2026-09-16, `[tape] appended 0 row(s)` — so the commit
touched only the report and the 90-day window. The append-only tape was not
rewritten to refresh a report, which is the point.

## What is committed

| Path | Source |
|---|---|
| `model/` — 11 modules, 8 test files, 2 package markers | drop, hash-verified |
| `etl/daily_v3.py` | drop, hash-verified (supersedes the earlier sample: the composite grid is now built from the rank's true reference set, worth ~4.7 points of browser-vs-gauge drift) |
| `tools/check_copy.py` | drop, hash-verified |
| `v3.js` | drop, hash-verified — **wired into `index.html`, loaded before `app.js`** |
| `series/README.md`, `series/v2_frozen.json` | drop, hash-verified |
| `docs/AUDIT_v3.0.md`, `docs/CHANGELOG_v3.md`, `docs/BOOTSTRAP_RUNBOOK.md` | drop, hash-verified |
| `.github/workflows/btc-data-v3.yml`, `.github/workflows/copy-audit.yml` | drop, hash-verified |
| `CLAUDE.md` | drop, hash-verified |
| `docs/MODEL_v3.md`, `docs/SCHEMA_v3.0.md` | written from the above; see below |
| `docs/gate_report_v3.0.txt` | reproduced byte-for-byte by `model.v3.validate` on this tree |

`MANIFEST.txt` was deleted after verification and has never been committed to any
branch — a transfer artifact, not repo content. `btc.csv` is gitignored.

**Cut over to the committed v3 row** (all read the tape; none recomputes):
`index.html`, `app.js`, `functions/api/risk/[[route]].js`,
`send-notifications.mjs`, `btc-risk-weekly.mjs`, `bot/post_risk_update.py`.
`README.md` was added to state the two scales.

Untouched: `style.css`, `data.json`, `data.js`, `btc_risk_model_v2.py`, `lib/`,
`db/`, Stripe/auth/billing, and the three v2 workflows. `data.json` keeps
publishing on the v2 scale by design — the README says why, and says the two
numbers must never be quoted interchangeably.

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

## The test gate, as it stands on 2026-09-17

Run 35233448299, and reproduced on this tree with the same two modules absent:

```
115 passed, 3 skipped, 5 warnings in 106.40s      (CI, runner)
115 passed, 3 skipped, 5 warnings in  94.22s      (local)
```

118 collected, **0 failed**. All three skips are named, and none is a masked
failure:

| Skip | Reason | Why it is legitimate |
|---|---|---|
| `test_phase2.py:74` | `statsmodels` absent | A test-only cross-check. `CLAUDE.md` requires it stay behind `importorskip`. |
| `test_phase4.py:183` | *"no single-family rows in this tape"* | Decision 18's 400-observation warm-up removed every one- and two-family row, so the fixture cannot exist. Data-dependent by design. |
| `test_phase4.py:343` | `scipy` absent | The `_spearman`-vs-scipy equivalence check from defect 1. It must skip where scipy is absent; that is the whole reason `_spearman` exists. |

The 5 warnings are two pandas deprecations (`concat` sort default in
`ensemble.py:138`, `Timestamp.utcnow` in a test), not model warnings.

## P6 — closed

`tools/check_copy.py` exits **0**. 1,360 user-facing strings scanned, 0 hits.

Before the rewrite it exited 1 with **39 defect-G strings** (hashrate 16, active
addresses 11, difficulty 6, LTH/STH 4, implied probability 2) and **92
cutover-fatal strings** (eleven signals 29, logistic map 17, Puell 15, MVRV-Z 9,
RHODL 7, terminal price 7, five factor families 6, supply in profit 2). All 131
were rewritten in both languages; none was waived, and no pattern was removed
from `FORBIDDEN` to make a claim pass.

The checker reports `site serves: mixed`, which is correct and intended: the page
loads `v3.js` for the score and still loads `data.js` for the v2 history chart
and the forward simulator. `README.md` states that the two carry **different
scales** and must never be compared or quoted interchangeably.

`model/v3/tests/test_copy_p6.py` carries the tripwire. It used to assert
`hits_fatal_now > 0` — it existed to stop anyone weakening the checker instead of
fixing the copy. Now that the copy is fixed it asserts `hits_fatal_now == 0`,
`total_hits == 0`, `main() == 0`, **and** that `{"uncomputed: hashrate",
"eleven signals"} <= set(cc.FORBIDDEN)`. That last clause is the part that
matters: it fails if someone deletes a pattern to make a new claim pass, which is
the cheap way to make P6 green without making it true.

## Next

Nothing is blocked. The daily 06:05 cron appends one row a day; the ship gates
and the gate report run with it.

Still open, deliberately, and all of them recorded in `CLAUDE.md` and
`docs/CHANGELOG_v3.md` rather than pending here:

- **P9 gate 2** — `CapMVRVCur` has no second construction. `rcap_alt.py` detects
  restatements; it never votes.
- **Defect B** — effective rank 1.59 of 4. One dominant factor plus a volatility
  modifier, never "multifactor".
- **2025-10-06** — passes reach by +0.0035, with only 52 % of the 33 ensemble
  members agreeing. Quote it with that context or not at all.
- **The holdout** — complete at n=365 and still **design-contaminated**.
  2026-06-30 at rank 0.104 is one observation inside a window motivated by the
  very failure it is being used to test. Real out-of-sample begins at the next
  ATH after the tape is frozen.

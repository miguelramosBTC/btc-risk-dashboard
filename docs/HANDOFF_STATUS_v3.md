# v3.0 hand-off status

**As of this commit, v3 is documentation only. It cannot run.** The live site,
the API, the email engine and the X bot all still read v2, exactly as
`docs/BOOTSTRAP_RUNBOOK.md` step 1 requires.

This file exists because the alternative — a repo that reads as if v3 were
installed — would be the same class of defect the project spends ten rules
avoiding. `CLAUDE.md` rule 8: *report negative results as results*.

---

## What is committed

| Path | State |
|---|---|
| `CLAUDE.md` | hand-off file, **verbatim** |
| `docs/CHANGELOG_v3.md` | hand-off file, **verbatim** — the authoritative record |
| `docs/BOOTSTRAP_RUNBOOK.md` | hand-off file, **verbatim** |
| `docs/gate_report_v3.0.txt` | hand-off file, **verbatim** — the 10/10 run on the 5,191-row tape |
| `etl/daily_v3.py` | hand-off file, **verbatim** |
| `.github/workflows/btc-data-v3.yml` | hand-off file, **verbatim** |
| `docs/MODEL_v3.md` | **written for this commit**, derived from the four documents above, every claim labelled with its source |
| `docs/SCHEMA_v3.0.md` | **written for this commit**, derived from `_row_to_json()` in `etl/daily_v3.py` |
| `docs/HANDOFF_STATUS_v3.md` | this file |

Untouched, per runbook step 1: `index.html`, `app.js`, `data.json`, `data.js`,
`style.css`, `btc_risk_model_v2.py`, `functions/`, `lib/`, `bot/`, `db/`,
`send-notifications.mjs`, `btc-risk-weekly.mjs`, `.github/workflows/btc-data.yml`,
`.github/workflows/btc-notify.yml`, `.github/workflows/x-daily-post.yml`.
**The live site keeps serving v2.**

---

## What is missing

Everything below is referenced by a committed file and was **not in the
hand-off**. Nothing here has been reconstructed, stubbed or guessed.

### 1. The model itself — `model/v3/`

`etl/daily_v3.py` line 45 imports eight modules plus `constants`; the workflow
additionally runs `python3 -m model.v3.validate` and
`python3 -m pytest model/v3/tests`. None exist.

| Module | Symbols the committed code calls | Documented in |
|---|---|---|
| `model/v3/constants.py` | `NEED_V3`, `SCHEMA_VERSION`, `BLEND_V`, `BLEND_G`; also `HALVING_DATES` (kept for charts, not an input — decision 7) | `docs/MODEL_v3.md` §2, §4 |
| `model/v3/features.py` | `prepare_frame()`, `build_raw()` | §2, §4 |
| `model/v3/growth.py` | `growth_params()`; `huber_fit_design()` | §4 (G) |
| `model/v3/pillars.py` | `build_pillars()` | §4 |
| `model/v3/compute.py` | `build_daily()`, `WEIGHTS` | §5 |
| `model/v3/ensemble.py` | `ensemble_band()` | §6.1 |
| `model/v3/growth_specs.py` | `all_residuals()`, `mapped_G()` | §6.2 |
| `model/v3/outcome.py` | `forward_outcomes()`, `climatology()` | §6.3 |
| `model/v3/rcap_alt.py` | `fetch_rcap_alt()`, `mvrv_alt()`, `alt_hash()`, `gap_report()`; `GAP_BAND_LOG` | §6.4 |
| `model/v3/validate.py` | run as `__main__`; `DOCUMENTED_INVERSIONS`, `NAMED_CHECKS` | §7 |
| `model/v3/tests/` | **111 tests** | — |
| `model/v3/__init__.py` | package marker | — |

### 2. P6 enforcement

| Path | Referenced by |
|---|---|
| `tools/check_copy.py` | `CLAUDE.md` rule 5 and "Useful commands"; runbook step 5; decision 31 |
| `.github/workflows/copy-audit.yml` | runbook step 1; decision 31 |

Consequence: the **39 defect-G strings and 92 cutover-fatal strings** are still
live on the site and **nothing is measuring them**. The runbook expects the copy
audit to *fail* on this push — that expected-failure signal is absent because the
check is absent.

### 3. Series

| Path | Note |
|---|---|
| `series/v2_frozen.json` | The frozen v2 tape, SHA-256 `0de9ddd6…67067c`, 2011-01-13 → 2026-09-10. Runbook step 1. |
| `series/README.md` | Runbook step 1. |
| `series/v3.0.jsonl` | **Must not be created here.** See below. |
| `series/v3.0_last90.json` | Derived from the tape by the ETL. |

### 4. Referenced documents

| Path | Referenced by |
|---|---|
| `docs/AUDIT_v3.0.md` | `CLAUDE.md` rule 10 cites its §6 as maintained |
| `docs/phase3_appendix.md` | decision 14 — the frozen window, horizon and sample for the nested baseline |
| *BTC Risk Model — v2 Audit and v3 Design Roadmap* (11 Sep 2026) | the spec every `§` reference points at (§7.5 `conf`, §8.4 the P9 table, §9 operations, §12.1 the gates, §14 residual limits) |

### 5. Staged Phase 5 frontend

`v3.js`, the gauge, the band, the matrix caption and the exports are described as
*"built and staged but deliberately unmerged"* (runbook §6). Not in the hand-off,
and **correctly not committed** — the cutover is gated on closing defect G first.

---

## The tape is not here, and must not be invented

`docs/gate_report_v3.0.txt` is the output of a 5,191-row tape spanning
**2012-03-07 → 2026-05-23**. That tape was not in the hand-off, and
`docs/BOOTSTRAP_RUNBOOK.md` is explicit about why it must stay that way:

> Commit the **code only**. Do not create `series/v3.0.jsonl` by hand and do not
> upload the `SAMPLE_*` files from the earlier hand-off: `--bootstrap` refuses to
> run once the tape exists, and append-only means a truncated tape could never be
> repaired.

So the gate report committed here is **evidence of a run that happened
elsewhere**, not something reproducible from this repository today. The official
tape end stays **2026-05-23**, the holdout stays **UNSIGNED**, and `2026-06-30`
prints **`PENDING`**.

**No sentence of the form "validated through 2026" may be written** *(decision 16)*.

---

## What the committed workflow does today

`.github/workflows/btc-data-v3.yml` is committed verbatim, cron `5 6 * * *`
(06:05 UTC). With `model/v3/` absent it behaves like this:

| Step | Outcome |
|---|---|
| checkout, setup-python, `pip install` | pass |
| `python3 -m pytest model/v3/tests -q` | **fails** — no such directory |
| *everything after it* | **never runs** — no compute, no append, no commit, no push |

**This is safe.** The test step exists precisely so that *"a broken model must not
write a row"*, and an absent model is the broadest possible break. There is no
path by which this job can write a tape, and `--bootstrap` is not in the
scheduled invocation regardless.

**But it will fail once a day and email the maintainer**, and those are the same
notifications that later carry *"refusing to push a rewrite"* and the stale-feed
alerts. Training yourself to ignore this workflow's mail is a real cost.

Two ways to stop the noise; **the choice is the maintainer's**, because the
workflow is a verbatim hand-off artifact and editing it silently is worse than
the noise *(`CLAUDE.md` rule 9: ask instead of picking)*:

1. **Land `model/v3/`** and the noise ends by itself. Preferred.
2. **Comment out the `schedule:` block**, leaving `workflow_dispatch` live, and
   restore it in the same commit that lands `model/v3/`. Reversible, one line, and
   it does not touch the guarantees — the dry run in runbook step 2 is a
   `workflow_dispatch` anyway.

---

## Open questions raised by reading the hand-off

These are inconsistencies **within the committed material**. They are recorded
rather than resolved, because resolving them means choosing, and
`CLAUDE.md` rule 9 reserves that for the maintainer.

### O1 — M's demotion is not in the decision log

The constitution locked five pillars, **V 0.31 · G 0.24 · T 0.15 · M 0.10 ·
Σ 0.20 = 1.00**. Four independent committed sources say M no longer votes:

- `CLAUDE.md` rule 6 — *"T_dd's 0.0675 and M's 0.10 were removed, not recycled.
  Active family weight is 0.90."*
- `docs/gate_report_v3.0.txt` — collinearity and price-elasticity lines carry
  **four** families (V, G, T, S).
- `etl/daily_v3.py` — `"mctc_mod": opt("mctc_mod", 6)` is annotated
  `# demoted: published, does not vote`, and the emitted row has no `M`.
- decision 29's ensemble weight vectors all sum to **0.90**
  (`0.225×4`; `0.25/0.2/0.15/0.3`).

But the decision log committed here runs 0 → 32 and **contains no entry recording
the demotion**, while the changelog's own header says a change to anything under
*Constitution* is a new `schema_version` and a new tape file. Either an entry is
missing from the hand-off, or the constitution table needs correcting — and
`schema_version` should be checked against whichever it is. **This is the single
most load-bearing ambiguity in the hand-off**, because the weight vector is the
model.

### O2 — the constitution's Output row is superseded but not struck

The constitution still reads `risk01 = risk100/100` and
`risk_lo/hi = risk100 ± round(10·s_t)`. Decision 18 replaced the first
(`risk01 = F_exp(EMA(raw))`) and decision 20 the second (the image of the
disagreement interval, because `10·s_t` *"can never exceed ±5, and measured ±1"*).
A reader who stops at the constitution table gets both wrong.
`docs/MODEL_v3.md` §5 and `docs/SCHEMA_v3.0.md` flag it; the changelog itself
does not.

### O3 — `thermo_stale_days` is computed but not emitted

The constitution says it *"is stored on the row"*. `build_tape` joins it into
`extras`, but `_row_to_json` emits `input_stale_days` only. **Resolve before the
bootstrap**: the tape is append-only, so a field omitted on day one can never be
added to the committed rows.

### O4 — `docs/CHANGELOG_v3.md` still carries pre-rank baseline numbers

Decision 14 reports the nested baseline as v3 **+0.0160**; decision 19 then
records that the switch to ranks moved it to **−0.0562**, and
`docs/gate_report_v3.0.txt` prints −0.0562. Decision 19 is explicit that it is
withdrawing the earlier claim, so this is intended history rather than an error —
but anyone quoting decision 14's table in isolation will quote a superseded
number.

---

## The one irreversible action

`etl/daily_v3.py --bootstrap` founds the tape. It runs **once, ever**.

**An agent must not run it** *(`CLAUDE.md`)*. Open the PR, run the dry run, report
the row count and the gate report — then stop and let a human decide.

The abort condition is stated in code and in the runbook: if the dry run reports
**5,191 rows through 2026-05-23**, the API top-up failed silently and only the
lagging CSV was read. Bootstrapping there would freeze a truncated tape
**permanently**. Expected on a healthy run: **~5,300 rows**, ending yesterday or
today.

---

## Order of work from here

1. **Land `model/v3/`** (11 modules + 111 tests), `tools/check_copy.py`,
   `.github/workflows/copy-audit.yml`, `series/README.md`,
   `series/v2_frozen.json`. Resolve **O1** while doing it.
2. **`pytest model/v3/tests -q`** → 111 pass; **`python3 -m model.v3.validate`**
   → reproduce `docs/gate_report_v3.0.txt`.
3. **Runbook step 2** — dry run in Actions. Check the row count against §2's
   table. **Stop if it is 5,191.**
4. **Runbook step 3** — the maintainer, not an agent, bootstraps.
5. **Runbook step 4** — sign the holdout. `2026-06-30` must stop printing
   `PENDING`; if the June 2026 low does **not** land in the bottom quintile,
   *that is a real finding about the model and must be recorded, not explained
   away.*
6. **Runbook step 5** — close defect G. **No waiver list; change the sentences.**
7. **Runbook step 6** — only then the dashboard, and only after `v3.js` is
   mapped through the `composite` grid.

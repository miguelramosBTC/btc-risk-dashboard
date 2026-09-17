# CLAUDE.md — working rules for this repository

Read this before changing anything under `model/`, `etl/`, `series/` or
`.github/workflows/`. `docs/CHANGELOG_v3.md` is the authoritative record; this
file is the short version of what must not be relitigated.

This project publishes a **risk score for Bitcoin**. Its value is not that the
number is clever. It is that the number means what it says, that yesterday's
number never changes, and that the model's limits are written down rather than
smoothed over. Several of the most valuable results here were **negative** — a
layer that shipped dark, a family that was rejected, a gate that was reported as
a knife-edge instead of a pass. An agent whose success signal is "tests green,
gates pass" would have shipped all three the wrong way.

## What the score is, and is not

It ranks **how extended the market is** versus its own causal history. `82` means
"more extended than 82 % of history up to that morning". It is **not** a forecast.
Measured directly: conditional forward-outcome probabilities scored **worse than
the base rate** (skill −0.131, reliability inverted), and realised P(drawdown >
30 % in 180 d) is **flat across rank bands**. Never add implied probabilities,
never describe it as predicting anything, never tune it against forward returns.
It exists to support dynamic DCA: buy more when extension is low. On that metric
it earns **+17.1 %** BTC-per-dollar over flat DCA on four-year windows, against
+15.9 % for MVRV percentile alone and −0.2 % for a noise control.

## Ten rules that are not up for negotiation

1. **The tape is append-only.** A committed row is never rewritten, reordered or
   reformatted. Expanding CDFs mean a recompute today would move 2017's rank;
   that is why the row is frozen and a recompute is a diagnostic.
2. **A version bump is a new file.** Any change to weights, a family, a map,
   Nmin, the EMA span or the blend ratios ships as `v3.1.jsonl` with its own
   `schema_version`. It never edits `v3.0.jsonl`.
3. **Never retune to pass.** If a gate fails, fix the pillar or change the
   version. Do not widen Nmin, shrink a weight, drop a date from the reach list,
   search for a weight at which a candidate passes, or move a policy threshold
   to catch a score that will not print.
4. **Causality is structural.** No value at date *t* may use a print with
   timestamp > *t*. Every map is tested for it, including the detectors.
5. **Name only what you compute** (P6). `tools/check_copy.py` enforces it and
   now exits 0: the 39 defect-G strings and the 92 describing v2 machinery were
   rewritten in both languages. Fix the sentences; do not add a waiver list, and
   do not delete a pattern from `FORBIDDEN` to make a new claim pass —
   `test_copy_p6.py` asserts the checker is still armed.
6. **Retired weight is retired.** T_dd's 0.0675 and M's 0.10 were removed, not
   recycled. Active family weight is 0.90 and is disclosed on every row.
7. **Pre-register before measuring.** The ensemble grid, the growth
   specifications and the outcome layer's activation rule were all fixed before
   any output was inspected. Adding a level after seeing results — or dropping
   one that widens the published band — is a retune, and a worse one than most.
8. **Report negative results as results.** Dark layers, rejected families and
   boundary gates go in the changelog with their numbers.
9. **Ask instead of picking.** When an implementation detail is not in the spec
   and the choice is material, stop and ask. Several such choices straddled a
   gate.
10. **Never claim the model has no limitations.** `docs/AUDIT_v3.0.md` §6 and
    the changelog's residual-limits section are maintained, not decorative.

## Known-open, deliberately

- **P9 gate 2 unsatisfied**: `CapMVRVCur` has no second construction. A vendor
  API is a second phone number, not a second method. `rcap_alt.py` is a
  restatement *detector*; it never votes and never substitutes.
- **Defect B is structural**: effective rank 1.59 of 4, 78 % of variance in one
  component. Bitcoin has one cycle and every valuation metric is a view of it.
  Describe the model as "one dominant factor plus a volatility modifier", never
  as multifactor.
- **2025-10-06 is a knife-edge**: the incumbent passes reach by +0.0035, and only
  **52 %** of equally defensible constitutions agree. Report it with that
  context.
- **The holdout is design-contaminated**, not unseen. Real out-of-sample starts
  at the next ATH after the tape is frozen.

## The one irreversible action

`etl/daily_v3.py --bootstrap` founds the tape. It runs **once, ever**: it refuses
to run twice, and append-only means a tape founded on stale inputs can never be
repaired. A freshness guard aborts if the newest row is more than 3 days old,
because a silent API failure looks exactly like success in the log.

**An agent must not run the bootstrap.** Open the PR, run the dry run, report the
row count and the gate report — then stop and let a human decide. Everything
else in this repository is recoverable; this is not.

## Useful commands

```
python3 -m pytest model/v3/tests -q          # 111 tests
python3 -m model.v3.validate                 # ship gates, exit non-zero on fail
python3 tools/check_copy.py --inventory      # the P6 worklist
python3 etl/daily_v3.py --dry-run            # compute and report, write nothing
```

## Dependencies: numpy and pandas only. This is load-bearing.

The process that writes the tape must run on numpy + pandas. `statsmodels` and
`scipy` are test-only and must stay behind `importorskip`.

The trap that already caught us once: **`Series.corr(method="spearman")` imports
scipy.** With scipy absent it raised inside a gate, which turned a dependency
failure into a non-zero exit on the step whose job is to append a row — so no
row was ever committed and the log looked like a gate failure. Spearman is
Pearson on the ranks; use `validate._spearman`, which is exact to 12 decimal
places including ties. Before adding any import to `model/v3/` or `etl/`, ask
whether the daily append needs it. It almost never does.

## Everything a run writes stays inside the path it was given

`--tape /tmp/x.jsonl` must not write a window into the real `series/`. Use
`etl.window_for(tape_path)`. A test run that leaves artifacts in the working
tree is one `git add -A` away from committing a fixture as production data.

Likewise `load_inputs` defaults to the live Coin Metrics dump and uses a local
CSV only when one is passed by name. A stray `btc.csv` must never silently
freeze the daily job on stale inputs while every log line reads "success".

## One input loader, in `etl/inputs.py`

Everything that needs the model's inputs goes through `load_inputs`. There was a
second path — `validate.py` called `pd.read_csv` directly — and it read a
**staler source**: the Coin Metrics dump has been frozen at 2026-05-24 since
May, so every recompute-based gate scored four months behind the tape and would
have kept doing so indefinitely. That is a standing condition, not one-off
staleness, and it is the class of bug a duplicated loader produces.

`model/v3/` must not import from `etl/`. `validate.py` is the single exception,
at the edge: it is a reporting tool, nothing under `model/` imports it except
its own tests, and the import is **lazy, inside `main()`** — so `import
model.v3.validate` still pulls no ETL code. `etl/inputs.py` imports only stdlib,
pandas and `model.v3.constants` (itself import-free), which keeps that arrow
cycle-free. If the lazy import fails, the report degrades to a bare CSV read and
**says so in its header**; a silent downgrade to staler inputs is the bug.

## Gates read the committed tape wherever the tape can answer

Reach, order, bottom, the holdout **and collinearity** score the committed rows.
The tape carries `V`, `G`, `T`, `S` and `price_usd` on every row, so
collinearity needs no recompute at all — and reading them measures the pillars
that were *published* rather than a parallel calculation of them.

Only the ensemble, growth-specification and outcome gates genuinely cannot:
they rebuild *alternatives* from raw series. Those get the topped-up frame from
`etl/inputs.py`, and the gate report prints the source of each so a silent swap
is visible.

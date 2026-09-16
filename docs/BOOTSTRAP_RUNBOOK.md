# Bootstrap runbook — signing the holdout

Everything in `model/v3/` and `etl/` has been built and tested against a Coin
Metrics CSV snapshot that **stops at 2026-05-23**. The community API is
unreachable from the build sandbox, so three things have never executed:

1. the API top-up (the tail from 2026-05-24 to today),
2. the `price_alt` second-source fetch,
3. the `--with-rcap-alt` realized-cap detector.

Until this runbook is followed, the official tape end is **2026-05-23**, the
holdout is **UNSIGNED**, and `2026-06-30` — the first v3 out-of-sample bottom —
prints `PENDING` in the gate report. No sentence of the form "validated through
2026" may be written before then.

---

## Order matters

Commit the **code only**. Do not create `series/v3.0.jsonl` by hand and do not
upload the `SAMPLE_*` files from the earlier hand-off: `--bootstrap` refuses to
run once the tape exists, and append-only means a truncated tape could never be
repaired.

### 1. Commit

```
model/v3/            all modules and tests
etl/daily_v3.py
tools/check_copy.py
.github/workflows/btc-data-v3.yml
.github/workflows/copy-audit.yml
docs/CHANGELOG_v3.md
series/README.md, series/v2_frozen.json
```

Leave `index.html`, `app.js`, `data.json`, `data.js` and everything under
`functions/` untouched. The live site keeps serving v2.

Expect the **Copy audit (P6)** workflow to fail on this push. That is correct
and is not a regression — see step 5.

### 2. Dry run

Actions → *Refresh v3 risk tape (daily)* → Run workflow:

```
bootstrap: true     dry_run: true     with_rcap_alt: false
```

Read the log. Four things must be true before going further:

| Check | Expected |
|---|---|
| `[data] API top-up:` | `2026-05-2x -> ` a date within a day or two of today |
| `[compute] N causal rows through …` | **~5,300 rows**, ending yesterday or today — **not** 5,191 through 2026-05-23 |
| `[ensemble] 33 members` | present; band a few points wide |
| `[dry-run] would append N row(s)` | N equals the row count; nothing written |

If the row count is still 5,191 the API top-up silently failed and the CSV alone
was used. **Stop.** Bootstrapping on that would freeze a short tape permanently.

### 3. Bootstrap for real

Same, with `dry_run: false`. This writes and commits `series/v3.0.jsonl` and
`series/v3.0_last90.json`. It happens **once, ever**. From then on the daily cron
appends one row; never bootstrap again.

Verify in the committed file:

- last `asof_date` is today or yesterday;
- `price_alt` is non-null on the last row, and `price_alt_flag` is 0 (if it is 1,
  the two price sources disagree by more than 3 % — investigate before trusting
  the row, and do not average them);
- `csv_vintage` and `api_vintage` are both populated and differ;
- `ens_lo` / `ens_hi` and `g_spec_lo` / `g_spec_hi` are present.

### 4. Sign the holdout

Run the gates on the full tape:

```
Actions → Refresh v3 risk tape (daily) → Run workflow  (dry_run: true)
```

and read the **Ship gates** step. The two lines that change:

- `named 2026-06-30:` must stop saying `PENDING` and report a causal rank. The
  June 2026 low is the first v3 out-of-sample bottom; if it does **not** land in
  the bottom quintile, that is a real finding about the model and must be
  recorded, not explained away.
- the holdout `INCOMPLETE:` line should disappear.

The holdout stays labelled **design-contaminated**, not "unseen" — expanding
CDFs and pillar Σ were both motivated by v2's failure at the 2025 ATH, and a
one-year embargo cannot un-know that. Real out-of-sample begins at the next ATH
after the tape is frozen.

Also check the **reach** line for `2025-10-06`. The incumbent passes by +0.0035,
and the specification ensemble says only about half of equally defensible
constitutions agree. Whatever it prints on the full tape, report it with that
context; it is a knife-edge, not a clean pass.

### 5. Close defect G, then the cutover

`python3 tools/check_copy.py --inventory` lists the exact strings. Two classes:

- **39 strings are defect G today** — hashrate, difficulty, active addresses,
  long/short-term holder positioning, implied crash probabilities. No version of
  this model has ever computed these; v2 does not either. This fails now.
- **92 more become fatal at the cutover** — "eleven signals", logistic maps,
  Puell, MVRV-Z, RHODL, terminal price, supply-in-profit. Accurate for v2,
  false for v3.

Do not add a waiver list. The fix is to change the sentences.

### 6. Only then, the dashboard

Phase 5 frontend work (`v3.js`, gauge, band, matrix caption, exports) is built
and staged but deliberately unmerged. One known gap: `v3.js` inverts into
*blend* space while the published score is now a *rank*, so the matrix widget
must map through the `composite` quantile grid that `map_support` already ships.
Fix that before the cutover, or the site will have two different current-risk
objects again — the exact defect F this project exists to kill.

---

## Two fetch paths that have never run

`price_alt` (CoinGecko daily close) and `--with-rcap-alt` (bitcoin-data.com
realized cap) both degrade cleanly when unreachable — they log, write nulls, and
leave the tape unaffected. Neither has executed successfully. Exercise
`--with-rcap-alt` once by hand before letting anything depend on it, and check
the one thing the sandbox could not: that the alternative realized cap uses the
same daily close as Coin Metrics' market cap. If it does not, a clock mismatch
will masquerade as a methodology gap.

`GAP_BAND_LOG` is `None` on purpose: the detector publishes the gap and alerts on
nothing until a real historical gap series exists. A guessed alert threshold is
worse than no threshold.

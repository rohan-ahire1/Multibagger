# Corporate-action rebuild and panel re-baseline

## Summary

Resolve the 538 quarantined adjustment rows, apply the corporate actions the
current panel is missing, rebuild `data/nse_adjusted/ohlcv_all.parquet`, and
re-establish the SEPA parity baseline against the corrected data.

This is the fix for the class of bug that made NIFTYBEES's 2019-12-19 1:10
split invisible, which in turn made the benchmark −4.07% CAGR instead of
+10.58% and fabricated ~16 points of alpha across every strategy in the repo.
One instance was found and patched by hand; this plan works the rest.

## Findings this plan is built on

Measured, not assumed:

- 538 quarantined rows across 522 symbols in
  `reports/data_audit/adjustment_quarantine.csv`.
- **302** have a corporate action in `data/reference/nse_corporate_actions.parquet`
  within ±3 days. The CA is known; the adjustment simply is not reflected.
- **92** have no CA record but an unmistakable round split ratio: 57× 1:10,
  25× 1:5, 7× 1:100, 2× 1:4, 1× 1:50. Predominantly ETFs.
- **144** have neither. Some are near-miss splits (AXISGOLD 0.0120,
  MAJESCO 0.0124); others are genuine collapses that must be preserved.
- **Root cause of the ETF cases:** NSE's equity corporate-action feed does not
  carry ETF splits. GOLDBEES, HDFCMFGETF, SETFGOLD, AXISGOLD, LICMFGOLD,
  BSLGOLDETF, IVZINGOLD all sit at `ret ≈ −0.99` with no CA row.
- **ETFs are identifiable deterministically by ISIN prefix**: `INF` = fund
  units, `INE` = equity. The ISIN is present in every raw bhavcopy row.

Two corrections to earlier reasoning, recorded so they are not retried:

1. `PREVCLOSE` in the bhavcopy is the **unadjusted** prior close. Verified on
   NIFTYBEES 2019-12-19 (`CLOSE=130.20`, `PREVCLOSE=1292.54`, equal to the prior
   raw close) and on IRCTC 2022-10-28. It therefore cannot be used to derive
   adjustment factors — `PREVCLOSE(t)/CLOSE(t-1)` is always ≈1.0.
2. The `config/corporate_actions.csv` row `IRCTC,split,2022-10-28,factor 0.2`
   shows no price discontinuity on that date (741.25 → 739.40). The six
   `source=seed` rows are unverified and must be validated or deleted, not
   trusted.

## Decisions already made

- **Auto-inference is restricted to ETFs** (ISIN prefix `INF`). Equities always
  go to manual review. A wrongly inferred split on a real equity collapse
  erases a true loss and manufactures a recovery — the precise failure this
  work exists to prevent.
- **Re-baseline and record both.** Run parity before and after, commit both
  numbers with the difference explained, and make the post-rebuild figure the
  new gate.

## Implementation Changes

### 1. Security master — `scripts/build_security_master.py` (new)

Scan every raw bhavcopy archive under `data/nse_bhavcopy_raw/**/*.zip` and emit
`data/reference/security_master.parquet` with one row per
`(isin, symbol, series)`:

```
isin, symbol, series, first_seen, last_seen, sessions, instrument_class
```

`instrument_class` is derived from the ISIN prefix: `INF` → `fund`,
`INE` → `equity`, anything else → `other` (government securities, `IN0`/`IN9`).

This is the prerequisite for the ETF rule, and it independently closes the
ticker-identity gap: a renamed symbol keeps one ISIN, so the master doubles as
the rename map.

### 2. Reconciliation — `scripts/reconcile_corporate_actions.py` (new)

Recompute close-to-close ratios from the **adjusted** panel, join against the
CA feed and the security master, and classify every jump outside
`[1/4, 4]` into exactly one bucket:

| Bucket | Rule | Action |
|---|---|---|
| `ca_known` | CA within ±3 days of the jump | Apply the CA's factor |
| `etf_inferred` | `instrument_class == "fund"`, no CA, ratio within 3% of a round `1:d` or `m:1` | Emit an inferred CA row |
| `manual` | Everything else | Write to a review queue, apply nothing |

Outputs:

- `config/corporate_actions.csv` gains rows with `source=inferred_etf`, each
  carrying the observed ratio and the matched round ratio in `description`.
- `reports/data_audit/ca_manual_queue.csv` — the `manual` bucket, with symbol,
  date, ratio, nearest CA (if any) and instrument class, sorted by absolute
  return so the largest distortions are adjudicated first.

Round-ratio candidates: `1:2, 1:3, 1:4, 1:5, 1:10, 1:20, 1:25, 1:50, 1:100,
1:1000` and the reverse-split inverses `2:1, 3:1, 4:1, 5:1, 10:1`. Tolerance
3% relative. **A candidate matching more than one round ratio inside tolerance
is sent to `manual`, never guessed.**

### 3. Seed-row validation

For each of the six `source=seed` rows in `config/corporate_actions.csv`,
assert a price discontinuity exists within ±3 days of the stated `ex_date`. Any
row without one is deleted and reported. IRCTC 2022-10-28 is already known to
fail this check.

### 4. Guardrail — `scripts/nse_build_adjusted_panel.py` (modify)

`_quarantine_bad_jumps` currently reports every large jump, so the file mixes
real errors with legitimate collapses and nobody can tell them apart. Change it
to classify using the same buckets as §2 and write
`symbol, date, ratio, bucket, nearest_ca_date`. **The build fails loudly if any
row lands in `ca_known`** — that means a known corporate action was not applied,
which is a pipeline bug rather than a data gap.

Keep the existing two-sided ratio test unchanged; it is correct.

### 5. Rebuild and re-baseline

1. Record the pre-rebuild parity: `uv run python optimize_joint_wf.py --parity`.
2. Rebuild the panel.
3. Diff old vs new panel: count rows changed and symbols affected, and confirm
   the changed set is a subset of the symbols carrying new or corrected CAs.
   **A symbol changing without a corresponding CA is a bug — stop.**
4. Record the post-rebuild parity.
5. Write both into `docs/BACKTEST_TRUTH.md` with the date, the diff, and the
   reason, and update the committed baseline the parity test asserts.
6. Re-run the five lab strategies and record the before/after in each
   `strategies/*/README.md`.

## Test Plan

- **Security master**: a symbol renamed mid-history resolves to one ISIN; ISIN
  prefixes classify as expected; every symbol in the panel appears in the master.
- **Round-ratio matcher**: 0.1 → `1:10`; 0.0999 → `1:10` (inside 3%); 0.107 →
  no match; 0.0120 → no match (near-miss must not be forced); a value matching
  two candidates returns ambiguous.
- **Bucketing**: NIFTYBEES 2019-12-19 classifies `ca_known` once its manual row
  is present; GOLDBEES 2019-12-19 classifies `etf_inferred`; a genuine equity
  collapse with no CA classifies `manual` and stays unadjusted.
- **Guardrail**: a panel with a deliberately unapplied known CA fails the build.
- **Regression**: full suite green (374 currently). The parity test asserts the
  **new** committed baseline; the old value is recorded in
  `docs/BACKTEST_TRUTH.md`, not silently discarded.

## Assumptions

1. ISIN prefix `INF` reliably identifies fund units on NSE. Verified on a 2019
   bhavcopy (82 `INF` vs 1,848 `INE`).
2. A ±3-day window is enough to associate a price jump with its ex-date.
3. ETF splits are never accompanied by a same-day move large enough to push the
   observed ratio outside 3% of the true round ratio. The 144 near-misses
   suggest this fails occasionally, which is why they route to `manual`.
4. The panel's adjustment mode (price-return vs total-return) is unchanged by
   this work; dividends are out of scope.

## Risks

- **The manual queue is the long pole.** ~144 rows, and the plan deliberately
  does not automate them. Adjudicate largest-first and stop when the tail stops
  mattering; the queue file records what was left.
- **Parity will move.** That is expected and is the point. The guard against a
  bad rebuild is step §5.3, not parity stability.
- **Inferred rows are a new source of truth.** Tag them `inferred_etf` so they
  can be revoked wholesale if the rule proves wrong.

## Acceptance Criteria

1. `reports/data_audit/adjustment_quarantine.csv` contains **zero** `ca_known`
   rows after rebuild.
2. Every `etf_inferred` row is an `INF` ISIN with a single unambiguous ratio.
3. The panel diff touches only symbols with new or corrected CAs.
4. Both parity figures recorded in `docs/BACKTEST_TRUTH.md`.
5. Full suite green against the new baseline.
6. Unverified `source=seed` rows removed.

## Out of scope

Dividend/total-return treatment, delisting terminal values, point-in-time index
membership. Separate items.

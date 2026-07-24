# NSE Daily Bhavcopy Backfill + Corporate-Action Adjustment

## Summary

Replace the survivorship-biased, adjustment-drifting yfinance price panel with a
**self-built, reproducible, survivorship-free daily panel** sourced from the free official
NSE bhavcopy archive plus an NSE-sourced corporate-action adjustment layer.

Motivation (from `reports/data_audit/AUDIT_REPORT.md`): the current panel
`data/yf_adjusted/ohlcv_all.parquet` omits ~16.8% of securities that ever traded (587
genuine delistings, 216 once-liquid, ~87 distressed blow-ups such as DHFL/SINTEX/ALBK), and
its incremental updates cause split-adjustment drift and negative adjusted prices. The
canonical 348-trade `--parity` backtest is drawn entirely from survivors, so its 21.18% CAGR
is an optimistic upper bound.

NSE daily bhavcopy is the as-of-date record of **every** symbol trading each session,
delisted names included → survivorship is solved by construction. The cost is that bhavcopy
is **raw/unadjusted**, so we must build our own deterministic corporate-action adjustment
(the existing `corporate_actions` DuckDB table holds only 1,571 splits, no ISIN, no
bonus/dividend/face-value — insufficient and must be rebuilt).

The build is delivered in phases so value lands early and the canonical backtest is never
silently disturbed:

- **Phase 0** — Raw daily backfill (immutable) + parsed raw panel. Delivers survivorship-free
  raw prices.
- **Phase 1** — Corporate-action fetch + split/bonus/face-value back-adjustment → adjusted
  panel (price-return). Delivers the survivorship-corrected backtest number.
- **Phase 2** — Dividend layer → total-return panel matching the current dividend-adjusted
  basis, for apples-to-apples parity and fair TRI comparison.
- **Phase 3** — Cutover: config switch, validation harness, audit test suite.

The existing yfinance panel is **left in place and untouched** until Phase 3 validation
passes; the new panel is written to a separate path and selected via a config flag.

## Non-goals

- No change to strategy logic, `config.yaml` strategy parameters, exit engine, or Rust core.
- No intraday data; daily bars only.
- No paid data vendor.
- No overwrite of `data/yf_adjusted/ohlcv_all.parquet` (kept as a fallback/comparison source).

## Key decisions (baked in; rationale given)

1. **Source = NSE daily bhavcopy**, both formats already handled by
   `src/sepa_scanner/data_sources/nse_bhavcopy.py` (`parse_bhavcopy_zip` UDiFF ≥2024,
   `parse_legacy_bhavcopy_zip` <2024, `NseBhavcopyClient.download_for_date`). Reuse as-is.
2. **Series scope = `EQ` + `BE`** for ingestion (store the `series` column). Rationale:
   distressed names migrate to the `BE`/surveillance series before delisting; ingesting `BE`
   captures the terminal decline that most inflates survivorship. The strategy universe
   filter still restricts to `EQ` via `config.universe.allowed_series` (unchanged) — `BE`
   rows are available for exit valuation and audit but excluded from entry selection.
3. **Identity = ISIN**, not ticker. Build a security master so renamed tickers
   (ZOMATO→ETERNAL, MINDTREE→LTIM, CADILAHC→ZYDUSLIFE) keep continuous history under one
   canonical id. Bhavcopy carries ISIN from 2011+; pre-2011 rows fall back to ticker.
4. **Adjustment = back-adjustment** (anchor most-recent bar at raw, scale history by a
   cumulative factor), matching yfinance semantics so indicators/breakouts remain comparable.
   Split + bonus + face-value split in Phase 1; dividends added in Phase 2. Rationale:
   deterministic, reproducible, no drift, no negative prices.
5. **Adjusted panel is a NEW artifact** at `data/nse_adjusted/ohlcv_all.parquet` selected by a
   new `data.source` config flag (`yfinance` default → `nse_bhavcopy` after validation). The
   canonical backtest is only cut over once Phase 3 passes. Rationale: protect
   `BACKTEST_TRUTH.md` reproducibility during the migration.
6. **Dividend treatment (the one real fork):** the current panel uses yfinance Adj Close =
   split+dividend adjusted, and performance is compared vs Nifty500 **TRI**. To keep the
   comparison fair and the backtest comparable, the *final* panel must be dividend-adjusted
   (Phase 2). Phase 1 ships price-return first (split+bonus only) to isolate and measure the
   pure survivorship effect before dividends are layered on. **Recommended and assumed unless
   overridden.**

## Implementation changes

### Phase 0 — Raw daily backfill (immutable)

- **New script** `scripts/nse_bhav_backfill.py`:
  - Enumerate NSE trading sessions from `start` (default `2005-01-01`) to today using the
    existing trading-calendar helper (`data/cache/nse_holidays_trading.json` /
    `previous_trading_days`); do **not** iterate raw calendar days.
  - For each session call `NseBhavcopyClient.download_for_date(trade_date, raw_dir=...)`
    into an **immutable** raw store `data/nse_bhavcopy_raw/<YYYY>/<YYYY-MM-DD>.zip`
    (never rewritten; content-addressed via existing `sha256_file`/`maybe_archive_previous`).
  - Politeness: reuse `settings.data` retry/backoff; `sleep_seconds` throttle (default 0.3–0.5s);
    resumable (skip existing zips unless `--force`); `--start/--end/--workers` flags; structured
    log of downloaded/missing/failed sessions to `reports/data_audit/bhav_backfill_log.csv`.
  - Missing session = record and continue (do not fabricate rows).
- **New store** `src/sepa_scanner/storage/bhav_raw_store.py` (mirrors `ParquetOhlcvStore`):
  - `build_raw_panel()` parses every zip via the existing parsers (dispatch on `LEGACY_CUTOFF`),
    concatenates, keeps columns `date, symbol, series, isin, open, high, low, close, volume,
    turnover`, dedups on `(date, symbol)`, sorts `(symbol, date)`, writes
    `data/nse_raw/ohlcv_raw.parquet` (zstd). (Extend the two parsers to also select `ISIN`/
    `TckrSymb`-ISIN where present; legacy pre-2011 → null ISIN.)
- **Manifest**: write `data/nse_raw/manifest.json` (dataset_version, created_at, session count,
  row/symbol counts, date range, per-source-zip sha256, code commit).

### Phase 1 — Corporate actions + split/bonus adjustment

- **New fetcher** `scripts/nse_corporate_actions_fetch.py`:
  - Pull NSE corporate actions (equities) via the NSE corporate-actions API
    (`/api/corporates-corporateActions?index=equities&from_date=..&to_date=..`) using the
    existing `build_nse_session()` bootstrap pattern; page by date windows across full history.
  - Parse the free-text `subject`/`purpose` into structured events: `split` (Face Value Split
    From Rs X to Rs Y), `bonus` (Bonus a:b), `dividend` (Dividend Rs Z / z%), `rights`,
    keeping `symbol, isin, ex_date, action_type, ratio_num, ratio_den, old_face_value,
    new_face_value, dividend_amount, raw_purpose, source`.
  - Persist to a **rebuilt** `corporate_actions` table (extend schema in
    `storage/duckdb_store.py` + `schema.sql`: add `dividend_amount`, `rights_ratio`,
    `raw_purpose`; populate `isin`). Keep the old yfinance-splits data only as a
    cross-check source column (`source`).
  - Cross-validate parsed splits/bonuses against `data/reference/yfinance_splits_cache.json`
    and flag disagreements to `reports/data_audit/ca_reconciliation.csv`.
- **New module** `src/sepa_scanner/data_sources/adjustment.py`:
  - `build_adjustment_factors(ca_df)` → per-ISIN cumulative back-adjustment factor time series
    from split × bonus × face-value events (dividends excluded in Phase 1).
    - split/face-value: factor multiplies by `new_fv/old_fv` (or `ratio_den/ratio_num`).
    - bonus a:b: factor multiplies by `b/(a+b)`.
    - Anchor = 1.0 at the latest bar; propagate backward (prices before an ex-date scaled down).
  - `apply_adjustment(raw_panel, factors)` → adjusted `open/high/low/close = raw × factor`;
    `volume = raw / factor` (inverse split adjustment); `turnover = raw_close × raw_volume`
    (kept raw and **documented as raw**, fixing the current silent inconsistency — DQ-007).
  - Guardrails: reject//flag factors producing non-positive or >N× single-day jumps unbacked
    by a CA record (fixes DQ-003/DQ-004); quarantine offending ISIN-windows to
    `reports/data_audit/adjustment_quarantine.csv`.
- **New store output** `data/nse_adjusted/ohlcv_all.parquet` with the panel schema the app
  already expects (`date, symbol, open, high, low, close, volume, turnover`) plus a sidecar
  `data/nse_adjusted/security_master.parquet` (`canonical_id/ISIN, symbol, effective_from,
  effective_to, event_type`).

### Phase 2 — Dividend (total-return) layer

- Extend `adjustment.py` with an optional dividend leg: on each dividend ex-date multiply the
  back-adjustment factor by `(1 − dividend/prev_close)` (standard total-return back-adjust).
- Produce the final dividend+split adjusted panel (same path, versioned in manifest). This is
  the basis that matches the existing yfinance Adj Close and keeps the Nifty500-TRI comparison
  fair.

### Phase 3 — Cutover, config, validation, tests

- **Config**: add `data.source: yfinance | nse_bhavcopy` (default `yfinance`) and
  `data.panel_path` resolution in `settings.py`; `ParquetOhlcvStore` root selected from it.
  No strategy params change.
- **Wire the daily update path** (`scripts/yf_daily_update.py` equivalent) to append the latest
  daily bhavcopy + re-derive factors, **replacing the 4-day incremental yfinance update** that
  caused adjustment drift (DQ-002). Factors are recomputed from the full CA table each run, so
  no drift.
- **Read-only audit test suite** `tests/test_data_quality.py` (fail-loud, from audit §29):
  PK uniqueness, OHLC invariants (float tolerance), positive prices, zero-volume-with-price-
  move = 0, adjustment-factor continuity (no >2× jump absent a CA record), warm-up gate,
  chronological sort, survivorship canary (fixed list incl. DHFL/SINTEX/ALBK must be present),
  determinism (rebuild → identical checksum given frozen raw store).

## Test plan

1. **Unit**: adjustment math (split 1:2, bonus 1:1, face-value 10→2, combined, dividend) on
   synthetic series with known expected factors; parser ISIN extraction both formats.
2. **CA reconciliation**: reconstruct expected adjusted close for ~20 known actions
   (e.g. GOLDBEES 2019 split, BAJFINANCE, a bonus, a face-value split) and compare to stored;
   emit `reports/data_audit/ca_reconciliation.csv` (pass/fail per event).
3. **Integrity re-audit**: rerun the audit battery on `data/nse_adjusted/ohlcv_all.parquet`;
   assert 0 negative prices, OHLC violations only float-rounding, no unexplained >5× jumps.
4. **Survivorship canary**: assert the 216 once-liquid vanished ISINs
   (`reports/data_audit/survivorship_liquid_exits.csv`) are now present with history.
5. **Backtest delta (headline deliverable)**: run
   `uv run python optimize_joint_wf.py --parity` against (a) current yfinance panel and
   (b) new Phase-1 and (c) Phase-2 panels; report ΔCAGR / ΔMaxDD / Δtrades. Expectation:
   corrected CAGR ≤ current 21.18% (survivorship removes hidden losers). Document in
   `reports/data_audit/survivorship_backtest_delta.md`.
6. **Determinism**: rebuild adjusted panel twice from the frozen raw store → identical sha256.
7. `uv run pytest tests/` green (including new `test_data_quality.py`).

## Assumptions

- NSE bhavcopy archive + corporate-actions API remain publicly reachable (rate-limited but
  free); the existing `build_nse_session()` bootstrap handles 403s.
- Bhavcopy ISIN present from ~2011; pre-2011 identity falls back to ticker (accepted; the
  survivorship quantification already used the 2011+ ISIN era).
- Dividend history from the NSE CA feed is adequate for Phase 2; if coverage is poor, ship
  Phase 1 (price-return) and compare against a Nifty500 **price** index instead of TRI until
  dividends are complete.
- The delisting/merger terminal-price handling in `backtest/engine.py` (currently closes as
  `incomplete_data`) is addressed separately (audit DQ-011); this plan makes the terminal bars
  *available* but does not itself rewrite exit valuation.

## Risks & acceptance criteria

- **Risk: NSE free-text CA parsing errors** → wrong factors → false jumps. Mitigation: the
  continuity guardrail + `ca_reconciliation.csv` gate; quarantine unmatched windows; keep
  yfinance splits cache as cross-check. **Acceptance:** ≥99% of parsed factors reconcile
  within 0.5% of an independent source on the sampled events.
- **Risk: silently changing the canonical backtest.** Mitigation: new panel is opt-in via
  `data.source`; yfinance panel untouched; cutover only after Phase 3 tests pass.
  **Acceptance:** current `--parity` on the yfinance panel still reproduces
  21.18/16.63/1.27/348 unchanged.
- **Risk: BE-series contamination of the entry universe.** Mitigation: universe filter keeps
  `allowed_series: ["EQ"]`. **Acceptance:** universe symbol counts per date within tolerance
  of the yfinance-era counts for overlapping survivors.
- **Definition of done:** survivorship-free adjusted panel builds reproducibly from free NSE
  data; audit test suite green; ΔCAGR from survivorship documented; config flag flips the
  whole app (scanner, backtest, UI) to the new source with no strategy-param changes.

## Suggested sequencing for Codex

Implement Phase 0 → validate raw panel coverage vs `survivorship_liquid_exits.csv` → Phase 1
→ run CA reconciliation + integrity re-audit → run backtest delta (Phase 1) → Phase 2 →
backtest delta (Phase 2) → Phase 3 cutover + tests. Stop after each phase for review.

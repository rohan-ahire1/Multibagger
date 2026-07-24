# SEPA Scanner NSE Design

**Date:** 2026-03-07

## Goal

Build a local, ultrafast, alert-only NSE swing-trading scanner that ingests official NSE CM-UDiFF bhavcopy data, computes Minervini/SEPA-inspired daily signals, persists results locally, and sends WATCH and BREAKOUT alerts through console and optional Telegram.

## Product Constraints

- Market: NSE cash market, daily EOD only
- Output: alerts only, no order placement or broker integration
- Runtime target: a few seconds to scan roughly 1,000-1,500 liquid symbols on a MacBook Air M2
- Primary data source: official NSE merged-daily-reports API discovering the CM-UDiFF bhavcopy zip
- Compute engine: Polars for the hot path, DuckDB only for runs and signals
- Reliability: rerunnable downloads, partition-safe upserts, non-fatal Telegram fallback

## Architecture

The scanner is organized into three layers:

1. Ingestion and storage
  - `data_sources/nse_bhavcopy.py` bootstraps an NSE session, discovers the daily bhavcopy zip, falls back to direct URL templates for backfill, downloads and caches raw zips, and parses the UDiFF CSV into a normalized OHLCV schema.
  - `storage/parquet_store.py` owns Parquet partition upserts under `data/ohlcv_parquet/year=YYYY/month=MM/`.
  - `storage/duckdb_store.py` owns the `runs` and `signals` tables in `data/db/sepa.duckdb`.
2. Strategy and analytics
  - `indicators/`* expose Polars expressions or small pure helpers for SMA, highs/lows, volume metrics, RS, tightness, and ATR.
  - `strategy/*` implements market filter, trend template, VCP-style setup, ranking, and the end-to-end `signal_engine`.
  - ATR Wilder smoothing is the only allowed per-symbol recurrence and is implemented with a narrow NumPy loop.
3. Interfaces
  - `main.py` exposes `backfill`, `update`, `scan`, `run`, and `health` via Typer.
  - `alerts/*` formats messages and sends them to console and Telegram.
  - `settings.py` loads YAML config plus `.env` overrides.

## Data Flow

### Update / Backfill

1. Resolve target trading dates.
2. Create a requests session and warm NSE cookies by loading `https://www.nseindia.com/`.
3. For current-day discovery, call `https://www.nseindia.com/api/merged-daily-reports?key=favCapital`, select the item named `CM-UDiFF Common Bhavcopy Final (zip)`, and use its archive link.
4. For backfill or fallback, try direct archive URLs using `F` first and `P` second when provisional downloads are allowed.
5. Cache each raw archive as `data/raw/YYYY-MM-DD.zip`.
6. Parse the zipped CSV with Polars, normalize columns, filter allowed series, drop invalid rows, deduplicate `(date, symbol)`, and sort by `symbol,date`.
7. Upsert only the affected year/month Parquet partitions by replacing rows for incoming dates and rewriting the compact partition file with zstd compression.

### Scan

1. Resolve scan date `D`.
2. Load only the required lookback window from Parquet using Polars lazy scans: `max(min_history_days, 252, 200, base_lookback) + indicator buffer`.
3. Compute all rolling features by symbol:
  - SMA 50, 150, 200
  - 52-week high/low
  - traded value and ADV50
  - Vol10, Vol50, VolDryUp
  - Range10
  - BaseHigh, BaseLow, BaseDepth
  - Pivot and NearPivot
  - ATR%, ATR% moving averages, and ATR contraction inputs
4. Build the benchmark series for `NIFTYBEES`, compute the benchmark market filter, and compute RS score and RS rank against the benchmark.
5. Build the liquid universe on `D` using data sufficiency, minimum price, ADV50, and Vol50, then cap to the top `max_symbols` by ADV50.
6. Exclude the benchmark symbol from candidate generation.
7. Apply trend template, RS, setup, WATCH, and BREAKOUT rules.
8. Compute entry and stop framing, discard invalid risk rows, rank signals, and limit alerts.
9. Persist signals to DuckDB and `data/signals/YYYY-MM-DD_signals.parquet`, then emit console and Telegram alerts.

## Storage Design

### Parquet

- Root: `data/ohlcv_parquet/`
- Partitioning: `year=YYYY/month=MM/ohlcv.parquet`
- Compression: zstd
- Source of truth for historical OHLCV

### DuckDB

- File: `data/db/sepa.duckdb`
- Tables:
  - `runs(run_id, started_at, finished_at, scan_date, market_filter_on, num_symbols, num_eligible, num_watch, num_breakout, status, error)`
  - `signals(signal_date, symbol, signal_type, close, pivot, near_pivot, volume, vol50, vol_mult, rs_rank, range10, voldry, atr_pct, adv50, entry_ref, stop, r_per_share, created_at)`
- Constraint: unique `(signal_date, symbol, signal_type)`

## Signal Logic

### Market Filter

On benchmark symbol `NIFTYBEES`, the market filter is on when all are true on `D`:

1. `close(D) > sma200(D)`
2. `sma50(D) > sma200(D)`
3. `sma200(D) > sma200(D-20)`

If `require_market_filter` is true, BREAKOUT alerts are suppressed when the filter is off. WATCH alerts are still allowed.

### Universe

Universe eligibility on `D` requires:

1. At least `min_history_days`
2. `close(D) >= min_price_inr`
3. `ADV50(D) >= min_adv50_inr`
4. `Vol50(D) >= min_avg_volume_shares`

After eligibility, keep only the top `max_symbols` by ADV50 descending.

### Trend Template

Trend template pass on `D` requires all nine rules:

1. `close > sma150`
2. `close > sma200`
3. `sma150 > sma200`
4. `sma200 > sma200.shift(20)`
5. `sma50 > sma150`
6. `sma50 > sma200`
7. `close > sma50`
8. `close >= low52 * 1.30`
9. `close >= high52 * 0.75`

### Relative Strength

RS uses exact weighted relative-performance lookbacks:

- `Rel_63 = (1 + Ret_sym_63) / (1 + Ret_bm_63) - 1`
- `Rel_126 = (1 + Ret_sym_126) / (1 + Ret_bm_126) - 1`
- `Rel_252 = (1 + Ret_sym_252) / (1 + Ret_bm_252) - 1`
- `RSscore = 0.4*Rel_63 + 0.3*Rel_126 + 0.3*Rel_252`

`RSrank` is computed cross-sectionally among eligible symbols on `D` using percentile scaling `100 * (rank - 1) / (N - 1)` with a safe `N=1` branch.

### Setup

Setup pass on `D` requires:

1. `Range10(D) <= max_range10_pct`
2. `VolDryUp(D) <= max_vol_dryup_ratio`
3. `SMA10(ATRpct)(D) < SMA20(ATRpct)(D)`
4. `ATRpct(D) <= ATRpct(D-20) * atr_contraction_factor`
5. `close(D) <= sma50(D) * (1 + max_extension_from_50sma_pct)`
6. `BaseDepth(D) <= base_max_depth_pct`

### Pivot

Pivot excludes the current day:

- `pivot(D) = rolling_max(high, base_len).shift(1)`
- `near_pivot(D) = (pivot(D) - close(D)) / pivot(D)`

### WATCH

WATCH on `D` requires:

1. `trend_template_pass(D)`
2. `rs_pass(D)`
3. `setup_pass(D)`
4. `near_pivot(D) <= pre_alert_proximity_pct`
5. Not selected as a BREAKOUT alert for the same symbol/date

### BREAKOUT

BREAKOUT on `D` requires:

1. `trend_template_pass(D)`
2. `rs_pass(D)`
3. `setup_pass(D-1)`
4. Market filter is off only if `require_market_filter` is false, otherwise it must be on
5. `close(D) >= pivot(D) * (1 + breakout_buffer_pct)`
6. `volume(D) >= vol50(D) * breakout_vol_mult`
7. `close(D-1) < pivot(D) * (1 + breakout_buffer_pct)` to emit only the initial breakout day

The setup gate intentionally uses `D-1` so breakout-day volume and range expansion do not invalidate the pre-breakout contraction pattern.

### Risk Framing

For each signal:

- `entry_ref = pivot * (1 + entry_buffer_pct)`
- `struct_stop = rolling_min(low, 10)` ending on `D`
- `max_loss_stop = entry_ref * (1 - max_stop_pct)`
- `stop = max(struct_stop, max_loss_stop)`
- `r_per_share = entry_ref - stop`

Rows with `r_per_share <= 0` are discarded.

### Ranking

Signals are ranked by:

1. `rs_rank` descending
2. `vol_mult` descending
3. `range10` ascending

The scanner keeps the top `max_alerts_per_day` per signal type.

## Reliability Choices

- Telegram credentials are optional. If missing, the run continues in console-only mode and logs that Telegram is disabled.
- Missing benchmark data on the scan date is a fatal scan error because market filter and RS would be invalid.
- Corporate actions adjustment is optional, disabled by default, and documented as a limitation for raw-history indicators around splits and bonuses.

## Testing Strategy

- `tests/test_pivot_calc.py`: pivot excludes current day
- `tests/test_trend_template.py`: each trend-template rule independently toggles pass/fail
- `tests/test_atr_wilder.py`: exact Wilder ATR recurrence
- `tests/test_rs_rank.py`: deterministic RS score and percentile ranking on a toy universe
- `tests/test_setup_gate.py`: tightness, volume dry-up, ATR contraction, and base-depth checks
- fixture coverage for UDiFF bhavcopy parsing with a small sample file

## Non-Goals

- No auto trading
- No intraday scanning
- No broker APIs
- No portfolio management or position sizing


# SEPA Backtest Design

**Date:** 2026-03-08

## Goal

Add a local backtesting workflow for the existing scanner so the user can evaluate how persisted BREAKOUT signals would have behaved as executable trades, both from the CLI and from the local Streamlit UI.

## Scope

- Backtest `BREAKOUT` signals only in V1.
- Consume existing persisted data:
  - `signals` from DuckDB
  - OHLCV from `data/ohlcv_parquet/`
- Expose backtest results through:
  - a new `sepa backtest` CLI command
  - a new `Backtest` tab in the Streamlit UI
- Keep V1 read-only:
  - no portfolio simulation
  - no capital allocation logic
  - no persisted backtest-run tables

## Non-Goals

- Portfolio sizing or capital constraints
- Position overlap optimization across symbols
- Separate backtests for `WATCH` signals
- Broker-style intraday reconstruction from lower timeframe data

## Approaches Considered

### 1. On-demand backtest engine shared by CLI and UI

This option adds a dedicated backtest package that reads persisted signals and OHLCV, computes trades on demand, and returns both trade rows and summary metrics.

**Pros**
- Smallest surface-area change
- Easy to verify with deterministic tests
- Reuses the same engine for CLI and UI
- Avoids schema/versioning work for stored backtest runs

**Cons**
- Recomputes results whenever parameters change

### 2. Persisted backtest runs in DuckDB

This option stores every parameterized backtest run and its results in DuckDB.

**Pros**
- Fast repeat viewing
- Historical comparison of parameter sets

**Cons**
- Requires new schemas and cache invalidation rules
- Adds much more V1 complexity than the current codebase needs

### 3. Full portfolio simulator from the start

This option adds capital allocation, concurrency limits, portfolio equity, and position sizing from day one.

**Pros**
- More realistic portfolio-level output

**Cons**
- Many extra assumptions
- Much larger test and design surface
- Higher risk of mixing strategy validation with portfolio policy

## Selected Approach

Use **Approach 1**: an on-demand trade simulator shared by CLI and UI.

This fits the current scanner architecture, keeps the first version small, and produces actionable trade-level evidence without committing to portfolio assumptions too early.

## Trade Model

### Signal eligibility

- Only `BREAKOUT` rows are eligible for backtesting in V1.
- A signal on date `D` becomes actionable starting on trading day `D+1`.

### Entry logic

Each signal is tracked for a configurable pending-entry window.

- If next-day `open >= entry_ref`, the trade fills at `open`.
- Else if any pending day `high >= entry_ref`, the trade fills at `entry_ref`.
- Else if the pending window expires without a trigger, the signal is marked `not_filled`.

### Exit logic

After a trade is filled:

- Hard stop uses the persisted `stop`.
- Optional profit target is expressed in `R` multiples and is disabled by default.
- A configurable max holding period acts as a time stop.
- If a day gaps below the stop, exit at that day’s `open`.
- If both stop and target are touched on the same candle, assume the worse outcome.
- If no stop or target is hit before the time stop, exit at the last holding day’s `close`.

### Overlap rule

Backtests evaluate trades independently across symbols, but same-symbol overlap is suppressed:

- if a later `BREAKOUT` signal appears while an earlier trade in the same symbol is still open, the later signal is skipped

This keeps V1 behavior deterministic without introducing portfolio management.

## Default Parameters

- Pending entry window: `5` trading days
- Max holding period: `20` trading days
- Profit target: disabled by default
- Optional profit target: configurable `R` multiple, with `2R` as a convenient preset

## Outputs

### Trade rows

Each simulated trade row should include at least:

- symbol
- signal date
- entry date
- entry price
- exit date
- exit price
- exit reason
- holding days
- return percent
- `R` multiple
- signal metadata such as `pivot`, `entry_ref`, and `stop`

Signals that never fill should remain visible in the results with a status like `not_filled`.

### Summary metrics

The backtest summary should include:

- total signals
- filled trades
- not-filled signals
- stopped trades
- target exits
- time-stop exits
- win rate
- average return percent
- average `R`
- median `R`
- expectancy
- payoff ratio
- cumulative `R`
- max drawdown
- longest losing streak

### Time series

The engine should also expose:

- equity curve on a fixed `1R` per trade basis
- drawdown series aligned to the equity curve

## Architecture

Add a new package:

- `src/sepa_scanner/backtest/models.py`
- `src/sepa_scanner/backtest/engine.py`
- `src/sepa_scanner/backtest/metrics.py`

### Responsibilities

- `models.py`
  - typed parameter model
  - trade row model
  - summary/result model
- `engine.py`
  - load eligible signals
  - load symbol OHLCV slices
  - simulate entries and exits
  - enforce same-symbol overlap rule
- `metrics.py`
  - aggregate trades into summary metrics
  - produce equity and drawdown series

The backtest engine should remain separate from `signal_engine.py`. It must consume persisted signals rather than recomputing strategy logic.

## CLI

Add a new command:

```bash
sepa backtest --start YYYY-MM-DD --end YYYY-MM-DD
```

Optional flags:

- `--pending-days`
- `--max-hold-days`
- `--target-r`
- `--symbol`
- `--export-csv`

CLI output should provide:

- a concise summary block
- optional CSV export of the trade table

## UI

Add a `Backtest` tab to the Streamlit app with:

- start and end date controls
- parameter controls for pending days, max hold days, and optional target `R`
- optional symbol filter
- top-level summary metrics
- equity curve chart
- drawdown chart
- trades table
- selected-trade detail view that reuses the existing price chart and overlays entry, stop, and exit

## Error Handling

- If no `BREAKOUT` signals exist in the requested range, show a friendly empty state.
- If symbol OHLCV is incomplete after a signal date, mark that trade with a clear terminal status rather than crashing the whole run.
- If a backtest range is invalid, fail fast with a user-facing validation message.

## Testing Strategy

- Unit tests for entry logic:
  - gap-through entry
  - intraday trigger entry
  - not-filled expiration
- Unit tests for exit logic:
  - stop hit
  - gap-below-stop
  - target hit
  - same-day stop/target ambiguity
  - time stop
- Unit tests for overlap handling:
  - later same-symbol signal skipped while prior trade is open
- Metrics tests:
  - expectancy
  - payoff ratio
  - drawdown
  - losing streak
- CLI tests for argument routing
- UI query/presentation tests for backtest outputs where they materially protect behavior

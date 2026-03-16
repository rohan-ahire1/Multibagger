# YFinance Runtime Cleanup Design

**Problem**

The repository has partially migrated to `yfinance` for adjusted OHLCV, but the runtime path still contains bhavcopy-based update/backfill logic and still relies on a bhavcopy client for scan-day holiday loading. The current `yfinance` scripts also compute `turnover` from adjusted close times raw volume, which leaves split-era volume and liquidity metrics internally inconsistent.

**Goal**

Make `yfinance` the runtime OHLCV source for update/backfill/run flows, remove bhavcopy dependencies from the active execution path, and normalize adjusted price plus split-adjusted volume so scanner/backtest inputs remain coherent.

## Recommended Approach

Create a shared `yfinance` normalization module inside `src/sepa_scanner/data_sources/` and route both the CLI runtime and the standalone scripts through it.

This module will:

- download raw Yahoo history with `auto_adjust=False` and `actions=True`
- derive adjusted OHLC from `Adj Close / Close`
- derive split-adjusted historical volume from `Stock Splits`
- preserve liquidity by computing `turnover` from raw close times raw volume

The CLI will:

- use the shared `yfinance` module for `update`, `backfill`, and `run`
- use a plain NSE HTTP session for holiday loading instead of instantiating the bhavcopy client
- resolve the full symbol universe from `config/universe.yaml` when present, otherwise from the existing yfinance parquet store

## Alternatives Considered

**1. Keep using `yf.download(... auto_adjust=True)` and patch only turnover**

Rejected. This still hides adjustment behavior behind a library default and does not expose split metadata needed to repair historical volume correctly.

**2. Remove bhavcopy everywhere, including optional validation code**

Rejected for now. Bhavcopy can still provide a useful same-day raw-price validation path. The cleanup target is the runtime path, not every optional utility.

## Data Rules

- Stored `open/high/low/close` are adjusted for splits and dividends.
- Stored `volume` is adjusted for splits only.
- Stored `turnover` is based on raw close times raw volume.
- Scanner and backtester continue reading the single parquet store at `data/yf_adjusted/ohlcv_all.parquet`.

## Testing Strategy

- unit tests for yfinance frame normalization, especially split-adjusted volume
- runtime tests for update flow using yfinance instead of bhavcopy-specific code paths
- existing CLI tests kept green


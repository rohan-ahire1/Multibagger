# Multibagger (v1)

An end-of-day scanner, backtester and research terminal for Minervini-style SEPA/VCP breakouts on NSE (India) stocks.

> **Earlier version; see [multibagger-v2](https://github.com/rohan-ahire1/multibagger-v2).** This repo is the first
> version of the system and is kept as a reference. The live research platform at
> [multibagger.rohanahire.com](https://multibagger.rohanahire.com) runs on v2, which was rebuilt from the lessons
> learned here.

> Research and paper-trading software only. It does not place orders and is not financial advice.

## What it does

Every trading day it pulls NSE price data, runs a full-universe scan for stocks in a strong uptrend that are
tightening into a base, and flags two kinds of signal: `WATCH` (setup forming) and `BREAKOUT` (price clears the
pivot). Signals are stored, sent as alerts, and can be reviewed and backtested in a local React terminal.

## Key features

- **NSE data pipeline.** Downloads daily bhavcopy archives, builds a point-in-time price panel keyed by ISIN, and
  handles splits, bonuses and other corporate actions (with a curated override file and audit reports in `reports/`).
- **SEPA scanner.** Polars implementation of the trend template (moving-average stack, distance from 52-week
  high/low), relative-strength ranking, sector RS, volatility contraction (VCP), volume dry-up, pivot detection and a
  market-regime filter. Every threshold lives in `config/config.yaml`.
- **Backtesting.** A portfolio simulator with position sizing, sector caps, a regime gate and several exit engines
  (the default is a phased Minervini-style exit), plus a "lab" harness (`sepa lab run`) for comparing strategy configs.
- **Optional Rust core.** `rust_core/` is a PyO3 extension (Rayon, DuckDB, Parquet) that runs the same simulation in
  parallel for fast parameter sweeps.
- **Research terminal.** FastAPI backend and a React/TypeScript UI (`sepa-terminal/`) with a scanner triage view,
  TradingView lightweight-charts, keyboard shortcuts, shortlist/reject decisions, and a backtest view with equity,
  drawdown and per-trade charts.
- **Alerts.** Console and Telegram channels.
- **Other strategy experiments** in `strategies/`: mean reversion, momentum ("stocks on the move"), daily factors
  and episodic pivots.
- **Tests.** About 45 pytest modules covering indicators, signal rules, the exit engine, corporate-action handling
  and the backtest engine.

## Tech stack

Python 3.12 · Polars · NumPy · PyArrow/Parquet · DuckDB · Pydantic · Typer · FastAPI/Uvicorn · Rust (PyO3, Rayon) ·
React 18 · TypeScript · Vite · Tailwind CSS · TanStack Query/Table · Zustand · lightweight-charts · pytest

## Architecture

```
NSE bhavcopy zips ──► raw panel ──► identity + corporate-action layers ──► point-in-time panel (Parquet)
                                                                                │
                                                     Polars indicators + SEPA signal engine
                                                                                │
                              ┌─────────────────────────┬──────────────────────┴───────────┐
                              ▼                         ▼                                  ▼
                   DuckDB (runs, signals,      alerts (console,              backtest engine / lab
                   watchlist decisions)        Telegram)                     (Python or rust_core)
                              │                                                            │
                              └──────────────► FastAPI ──► React terminal ◄───────────────┘
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/STRATEGY.md](docs/STRATEGY.md),
[docs/DATA_LAYERS.md](docs/DATA_LAYERS.md).

### What v1 taught (and why v2 exists)

Over time this repo grew several backtest engines, settings that had different defaults in code and config, and a
signal database that kept appending old scans, which inflated backtest results. The numbers in the older docs here
(`PERFORMANCE_SUMMARY.md`, `LEADERBOARD.md`, parts of `docs/`) should not be trusted. v2 was rebuilt around one
engine, strict configs, immutable data and reproducible run records, and it reproduces v1's corrected reference run
trade for trade.

## Run locally

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and Node.js (for the UI).

```bash
uv sync --extra dev
cp .env.example .env          # all values optional
uv run sepa --help
```

Environment variables (`.env.example`):

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts (console-only if unset) |
| `POLARS_MAX_THREADS` | Cap Polars threads |
| `SEPA_DATA_DIR` | Use a different data directory |

Typical commands:

```bash
uv run sepa backfill --start 2025-01-01 --end 2025-03-31   # load price history
uv run sepa run                                           # update data, then scan the latest day
uv run sepa scan --date 2026-03-06 --no-telegram          # scan a specific day
uv run sepa backtest --start 2024-01-01 --end 2026-03-06  # simulate stored BREAKOUT signals
uv run sepa lab run --help                                # strategy lab
uv run sepa ui                                            # API + React terminal
uv run sepa health
uv run pytest -q                                          # fast tests (add -m slow for full-history checks)
```

Market data, DuckDB files and signal snapshots live in `data/`, which is git-ignored, so a fresh clone needs a
backfill before scanning.

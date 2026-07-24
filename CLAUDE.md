# CLAUDE.md — multibagger codebase guide

## Overview

`multibagger` (package name `sepa-scanner-nse`) is a local, alert-only quantitative
trading system for the Indian (NSE) equity market. It scans the daily universe for
Minervini/SEPA + VCP-style long swing setups (`WATCH` and `BREAKOUT` signals), persists
them, simulates trades with a portfolio backtester, and exposes everything through a
Typer CLI, a FastAPI server, and a React/TypeScript terminal UI. A large parallel
research surface (multiple `optimize_*.py` scripts, Optuna studies, alternate strategies)
exists for tuning and validating the strategy. There is also a performance-critical Rust
core (`rust_core`, a pyo3/maturin extension) that re-implements the hot backtest loop.

Domain: NSE daily OHLCV (survivorship-free bhavcopy panel, corporate-action adjusted,
total-return basis) → indicators → signals → portfolio backtest →
metrics/reports. Alert-only; no live order execution.

## Architecture

### Python package — `src/sepa_scanner/` (installable as `sepa`)
- `main.py` — Typer CLI entrypoint (`sepa` command): `backfill`, `update`, `scan`, `run`, `backtest`, `ui`, `health`, `build-universe`.
- `data_sources/` — `nse_bhavcopy` (**primary** OHLCV source since the 2026-07-19 cutover) plus `adjustment` (corporate-action adjustment), `nse_universe`/`nse_meta` (symbol discovery), `yfinance_adjusted` (legacy, still used by `sepa update`), `vix`, `fundamentals`.
- `storage/` — `parquet_store` (`ParquetOhlcvStore`, history in `data/nse_adjusted/ohlcv_all.parquet`; legacy panel at `data/yf_adjusted/`), `bhav_raw_store` (raw bhavcopy snapshots) and `duckdb_store` (runs/signals/watchlist in `data/db/sepa.duckdb`, `schema.sql`).
- `indicators/` — Polars indicator builders: `atr`, `sma`, `rs` (relative strength ranks), `contraction`, `tightness`, `highs_lows`, `volume`.
- `strategy/` — the signal engine and its parts: `signal_engine` (orchestrates the scan), `trend_template` (Minervini trend), `vcp_setup`, `patterns`, `ranking`, `sector_rs`, `market_filter`/`market_regime`, `explainer`.
- `backtest/` — trade simulation: `engine` (entry/exit simulation, optional Rust path), `portfolio` (`PortfolioManager`, sizing/sector caps), `exit_engine` (phased exits), `regime_gate`, `metrics`, `signal_analysis`/`signal_scorecard`, `models`, `queries`.
- `alerts/` — `console` + `telegram` sinks, `formatters`.
- `api/` — FastAPI app (`app.py`) and routes (`scanner`, `backtest`, `ohlcv`, `analysis`); served by `sepa ui`.
- `settings.py` — Pydantic settings loaded from `config/config.yaml` (single source of truth).

### Rust core — `rust_core/`
A `pyo3` extension built with `maturin`, importable as `multibagger_core`. `src/lib.rs`
exposes `run_portfolio_backtest_rust`, `run_bulk_backtest_rust_v2`,
`run_backtest_from_db_rust`, `run_optimization_grid_rust`, `health_check`. It mirrors the
Python portfolio/exit logic (rayon-parallel) for fast optimization sweeps. Python imports
it optionally — `backtest/engine.py` does `try: import multibagger_core` and only uses it
when `use_rust=True` is requested; otherwise the pure-Python path runs. Rust is an
accelerator, not a hard dependency.

### Top-level optimizers & research
- `optimize_joint_wf.py` — **canonical** joint entry+exit walk-forward Optuna optimizer (see Backtest Truth below). Studies in `optuna_joint_wf.db`; output `best_joint_params.json`.
- `optimize_exits_v2.py` / `optimize_exits.py` — phased-exit (`ExitConfig`) tuning. Studies in `optuna_exits_v2.db`; output `best_exit_params.json`.
- `optimize_engine.py` — broader engine param search (`optuna_fast_study.db`).
- `diagnose_entry_exit.py`, `regime_forensics.py` — analysis helpers.
- `scripts/` — ~50 research/utility scripts: data pipeline (`yf_bulk_download`, `yf_daily_update`, `backfill_*`), validation (`survivorship_test`, `alpha_vs_beta`, `walkforward_validation`), the production diagnostics engine `portfolio_diagnostics.py` (the harness `optimize_joint_wf.py` builds on), and sweeps.
- `strategies/` — alternate/parallel strategies, each self-contained with its own engine, config, and `output/`: `mean_reversion/` (full strategy + live paper runner + tests), `episodic_pivots/`, `daily_factors/` (monthly-rebalance factor portfolios), `autonomous_research/`.

### Other directories
- `config/` — `config.yaml` (primary params), `universe.yaml`, `alert_channels.yaml`, `glossary.yaml`, `corporate_actions.csv`.
- `data/` — local artifacts: `nse_adjusted/` (primary parquet OHLCV), `yf_adjusted/` (legacy panel), `db/` (DuckDB), `signals/`, `reference/` (sector maps, universe), `optuna/`, `us/`/`us_small/` (US experiments), `cache/`, `tmp/`.
- `reports/` — generated CSV/markdown/PNG analysis output (`portfolio_diagnostics`, `walkforward`, `sector_cap_sweep`, `mean_reversion`, etc.).
- `sepa-terminal/` — React/TypeScript + MUI 7 terminal UI (Vite, Node 22), the frontend launched by `sepa ui`.
- `tradingview/`, `sepa_entry_indicator.pine` — Pine Script ports of the entry logic for TradingView.
- `docs/` — `BACKTEST_TRUTH.md`, `STRATEGY.md`, `BACKTESTING.md`, `UI_BIBLE.md`, `plans/`, `research/`.

## Backtest ground truth (READ BEFORE TOUCHING BACKTESTS)

`docs/BACKTEST_TRUTH.md` is authoritative. Summary:
- `config/config.yaml` is the **single source of truth** for parameters everywhere (terminal, paper runner, UI, harness). Do not fork params into JSON sidecars or per-script constants.
- The **only** authoritative backtest command:
  `uv run python optimize_joint_wf.py --parity`
  Expected committed baseline: **CAGR 12.48% / MaxDD 24.28% / Calmar 0.51 / 281 trades** (full history 2010-01-04 → 2026-03-17, strategy-only with `cash_yield_apy: 0`).
  With a 6% idle-cash sleeve the total-portfolio figures are 17.20% / 15.08% / 1.14 — tracked separately, never counted as SEPA alpha.
- **Ignore** the CLI `sepa backtest` headline CAGR — it uses a serial-R-compound metric (`metrics.estimate_compounded_cagr`) that inflates results. Use the equity-curve CAGR from `--parity`.
- Do NOT create new backtest scripts or new param JSON sidecars. Any number not from the canonical harness is invalid and must not justify config changes.
- `best_joint_params.json`'s "27% holdout candidate" was tested and **rejected** (sub-window artifact). Pyramiding was tested and rejected (`pyramid_max_adds: 0`).
- When `exit_mode` is a `baseline_*` mode, the phased `ExitConfig` knobs are inert — tuning them does nothing.

## Workflows

### Install / build
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"   # Python deps via uv (uv.lock)
cp .env.example .env                                    # Telegram creds optional
```
Rust core (separate build, optional accelerator) — built with maturin from `rust_core/`:
```bash
cd rust_core && maturin develop --release               # builds & installs multibagger_core
```

### Run the scanner / pipeline
```bash
./.venv/bin/sepa update            # refresh latest yfinance bars
./.venv/bin/sepa scan              # compute + persist signals, send alerts
./.venv/bin/sepa run               # update then scan
./.venv/bin/sepa ui                # FastAPI + React terminal
```

### Optimization
```bash
uv run python optimize_joint_wf.py --parity     # reproduce committed baseline (canonical)
uv run python optimize_joint_wf.py --trials 200 # run Optuna study (optuna_joint_wf.db)
uv run python optimize_joint_wf.py --report     # plateau pick + holdout report
uv run python optimize_exits_v2.py --trials 100 # phased-exit tuning (optuna_exits_v2.db)
```
Optuna studies persist to SQLite `.db` files at repo root; resume with `load_if_exists`.

### Tests
```bash
uv run pytest tests/        # ~34 test files; ignore any test_ui_* (dead Streamlit refs)
```
`pyproject.toml` sets `pythonpath = ["src"]`, `testpaths = ["tests"]`. The `mean_reversion`
strategy has its own tests under `strategies/mean_reversion/tests/`.

## Key data / artifacts
- `data/nse_adjusted/ohlcv_all.parquet` — survivorship-free, corporate-action-adjusted OHLCV (the price panel).
- `data/yf_adjusted/ohlcv_all.parquet` — legacy yfinance panel (survivor-biased; kept for comparison).
- `data/db/sepa.duckdb` — runs, signals, watchlist (use `read_only=True` for concurrent reads).
- `optuna_joint_wf.db`, `optuna_exits_v2.db`, `optuna_fast_study.db` — Optuna study stores.
- `best_joint_params.json`, `best_exit_params.json` — optimizer outputs (informational; config.yaml remains authoritative).
- `LEADERBOARD.md`, `PERFORMANCE_SUMMARY.md` — historical performance logs (some entries are aspirational/experimental, not the committed config — trust `BACKTEST_TRUTH.md`).
- `reports/` — generated analysis artifacts.

## Conventions (from AGENTS.md / GEMINI.md / README)
- The self-built NSE bhavcopy panel is the source of truth for OHLCV (`config.data.source: nse_bhavcopy`); yfinance is legacy and still feeds `sepa update` until a daily bhavcopy refresh is wired. Corporate-action adjustment is applied by `data_sources/adjustment.py` (rebuilt via `scripts/nse_corporate_actions_fetch.py` + `nse_build_adjusted_panel.py`). NSE tickers use `.NS` suffix.
- Scanner and backtester always operate on the **full** symbol universe — never add single-symbol filtering to the default UI flow.
- Charts render a selectable timeframe window centred on the trade (`CandleChart.chartVisibleRange`), preserving the requested duration at either edge of history; indicator overlays are still computed from full data.
- Update `docs/STRATEGY.md` whenever strategy behavior changes; `docs/UI_BIBLE.md` when UI patterns change.
- When given a `.plan.md`, implement exactly; never edit the plan file. Workspace `CLAUDE.md` (in `Personal_Projects/`) says Claude plans, Codex implements.
- Commit messages: no AI/Claude/co-author trailers (user override).
- Machine: macOS ARM, 8 cores, 16GB; Python via `uv`; frontend MUI 7 / Node 22 / TypeScript.

## Command cheat-sheet
```bash
uv pip install -e ".[dev]"                       # install
cd rust_core && maturin develop --release        # build Rust core
sepa scan / update / run / ui / health           # pipeline + UI
sepa backtest --start ... --end ...              # CLI backtest (headline CAGR is inflated)
uv run python optimize_joint_wf.py --parity      # canonical backtest truth
uv run pytest tests/                             # tests
```

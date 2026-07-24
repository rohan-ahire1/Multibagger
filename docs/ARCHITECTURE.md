# Architecture

This document describes how `multibagger` moves data from raw prices through signal
generation, portfolio backtesting, optimization, and reporting, and how to extend it.
For parameter/results authority, defer to [`BACKTEST_TRUTH.md`](./BACKTEST_TRUTH.md);
for strategy semantics, [`STRATEGY.md`](./STRATEGY.md).

## Pipeline at a glance

```
                         config/config.yaml  (single source of truth)
                                   │
                                   ▼
 yfinance (.NS)  ──►  data_sources/yfinance_adjusted
   (adjusted              │
    OHLCV)                ▼
                  storage/parquet_store  ──►  data/yf_adjusted/ohlcv_all.parquet
                                   │
                                   ▼
                  indicators/*  (Polars: atr, sma, rs, contraction,
                                 tightness, volume, highs_lows)
                                   │
                                   ▼
                  strategy/signal_engine
                   trend_template ─ vcp_setup ─ sector_rs ─ ranking ─ market_filter
                                   │
                       WATCH / BREAKOUT signals + diagnostics
                                   │
                ┌──────────────────┴───────────────────┐
                ▼                                       ▼
   storage/duckdb_store  (runs,             backtest/  (BREAKOUT signals only)
     signals, watchlist)                     filter_signals (rs/quality/per-day)
                │                                 │
                ▼                                 ▼
       alerts/ (console,            PortfolioManager ─ exit_engine ─ regime_gate
        telegram)                        │  (pure-Python  OR  rust_core accelerator)
                │                        ▼
                ▼                  trades + daily_equity curve
        api/ (FastAPI)                   │
                │                        ▼
                ▼                  metrics (equity-curve CAGR / MaxDD / Calmar)
        sepa-terminal (React)            │
                                         ▼
                          optimize_*.py (Optuna walk-forward)
                                 optuna_*.db studies
                                         │
                                         ▼
                          reports/ , best_*_params.json
```

## Stage responsibilities

### 1. Raw data → price panel
`data_sources/yfinance_adjusted` downloads split/dividend-adjusted daily OHLCV for the NSE
universe (tickers suffixed `.NS`). `turnover = close * volume` is added (yfinance omits it).
`storage/parquet_store.ParquetOhlcvStore` normalizes and upserts into the single panel
`data/yf_adjusted/ohlcv_all.parquet`. Bhavcopy sources (`nse_bhavcopy`, `nse_universe`) are
used only for symbol discovery (`sepa build-universe`) and optional price validation;
yfinance is the sole runtime price source. Corporate-action adjustment logic was removed
because yfinance data is pre-adjusted.

Fundamental data is an optional, disabled side input. `data_sources/fundamentals.py`
can collect NSE financial-result listing metadata and headline detail values. It derives
`available_date` from the exchange broadcast timestamp using the 16:10 IST EOD-scanner
cutoff; filings after that cutoff become usable on the next calendar date (and therefore
the next trading session in an as-of join). The experimental growth gate supports a strict
mode that raises if actual availability metadata is absent or precedes the period end.
The old `period_end + reporting_lag_days` approximation is legacy research only.

### 2. Indicators (Polars)
`indicators/` builds derived columns with lazy Polars expressions: ATR, SMAs (50/150/200),
relative-strength ranks (`rs`, percentile across the universe per date), volatility
contraction and tightness (VCP), volume dry-up, rolling highs/lows and pivots. These are
pure column transforms over the panel and feed the strategy layer.

### 3. Strategy / signal engine
`strategy/signal_engine.py` orchestrates a scan for a given date:
- `trend_template` applies the Minervini trend filter (price vs 50/150/200 SMA, 52-week range, SMA200 slope).
- `vcp_setup` / `patterns` detect the volatility-contraction base and setup quality.
- `rs` ranks + `sector_rs` apply relative-strength and sector-RS gating.
- `market_filter` / `market_regime` classify the broad-market regime (benchmark `NIFTYBEES`).
- `ranking` orders candidates.

If the experimental fundamental gate is enabled, it is applied later by the canonical
`filter_signals` step. It is disabled in committed config because the current local
Screener history is partial/survivorship-biased and corrected fold/holdout tests worsened.

It emits two signal classes with bitmask failure-reason diagnostics:
- **WATCH** — setup quality met on day D (pre-breakout watchlist).
- **BREAKOUT** — trend + RS on day D, setup quality on day D-1, price above pivot, volume confirmation; one-time (prior close below threshold). Only BREAKOUT signals are tradable.

Output: a `ScanResult` (signals + watchlist candidates + diagnostics) persisted to DuckDB
and exported to `data/signals/YYYY-MM-DD_signals.parquet`.

### 4. Backtest / portfolio simulation
`backtest/` replays persisted BREAKOUT signals as a portfolio:
- `filter_signals` (in `scripts/portfolio_diagnostics.py` / engine) applies the **second**
  filter layer — `rs_rank_min/max`, quality profile/overrides, `max_signals_per_day`,
  sector ranking. The canonical path always runs this filter; raw signals are not traded directly.
- `PortfolioManager` (`portfolio.py`) handles sizing (`risk_per_trade_pct`), position caps
  (`max_positions`, `max_name_pct`), and sector caps (`max_sector_positions`).
- `exit_engine.py` runs the exit model. The committed config uses a `baseline_ema_hybrid`
  exit (EMA21 after 3R, EMA10 after a 5R peak close). Original and breakeven hard
  stops execute on an intraday touch; EMA failures are close-confirmed and fill at the
  next symbol-session open. A second close-confirmed rule recycles a trade after 15
  post-entry symbol sessions only when MFE stayed below +0.4R and the close is at/below
  entry; it also fills next open;
  a richer **phased** `ExitConfig` (breakeven activation, partial profit, bull/bear trail,
  time stop, trail-widen) exists but is **inert unless `use_extended_exit_model: true`**.
- `regime_gate.py` optionally gates/sizes entries by exogenous benchmark regime.
- Friction (slippage + STT/charges + DP fee) is applied to produce an honest result.
  Assumed idle-cash income is disabled and excluded from strategy CAGR.

Two metric paths exist and historically disagreed (see Known Traps below). The trustworthy
one is the **equity-curve** CAGR/MaxDD/Calmar computed from the real `daily_equity` curve of
`simulate_portfolio_python`. The CLI `sepa backtest` prints an inflated serial-R-compound
number — ignore it.

### 5. The Rust core (`rust_core` → `multibagger_core`)
A `pyo3`/`maturin` extension (`crate-type = ["cdylib"]`) that re-implements the hot
backtest loop in Rust with `rayon` parallelism, for fast optimization sweeps. Exposed
functions: `run_portfolio_backtest_rust`, `run_bulk_backtest_rust_v2`,
`run_backtest_from_db_rust`, `run_optimization_grid_rust`, `health_check`.

The Python↔Rust boundary is **optional and JSON-based**: `backtest/engine.py` does
`try: import multibagger_core` (falls back to `None`), and only calls into Rust when a
caller passes `use_rust=True`. Signals/params are serialized to JSON, price arrays passed
as numpy/typed vectors; Rust returns a trades JSON blob that Python re-parses into
`BacktestTrade` models. If the extension is not built, everything still runs in pure Python.
The Rust `ExitMode` enum mirrors the Python exit modes (`minervini_phased`, `v2_trail_*`,
`v3_trail_pct`, `advanced_sepa`).

### 6. Optimization (Optuna, walk-forward)
The **canonical** optimizer is `optimize_joint_wf.py`, a *joint entry + exit* walk-forward
study built directly on the production engine (`scripts/portfolio_diagnostics.py`) so any
winning params transfer 1:1 to the committed backtest and live sheet. Its anti-overfit design:
- Objective scored across **3 rolling in-sample regime folds** (GFC recovery / mid-cycle bull /
  COVID era), not a single window.
- Trial score = `mean(Calmar) − 0.5·std(Calmar)` — rewards consistency across regimes.
- A hard **holdout 2023-01-01 → end** is never seen during optimization.
- Final selection is a **plateau pick** (robust param neighborhood by normalized-L1 density),
  not the single best trial, to avoid knife-edge overfits.
- Tuned knobs — entry: `rs_rank_min`, `vol_mult_min`, `voldry_min`, `max_signals_per_day`;
  exit (phased): `breakeven_activation_r`, `partial_profit_r`, `trail_pct_bull/bear`,
  `time_stop_days`, `trail_widen_r`.
- Modes: `--parity` (reproduce committed baseline), `--trials N` (run study into
  `optuna_joint_wf.db`), `--report` (plateau pick + holdout/full comparison → `best_joint_params.json`).

Secondary optimizers: `optimize_exits_v2.py` / `optimize_exits.py` (phased-exit `ExitConfig`
only, maximize Calmar on a fixed window, `optuna_exits_v2.db`); `optimize_engine.py`
(broader engine sweep, `optuna_fast_study.db`). Additional walk-forward/validation lives in
`scripts/walkforward_validation.py`, `scripts/survivorship_test.py`, `scripts/alpha_vs_beta.py`.

> Authority rule: only `optimize_joint_wf.py --parity` numbers are valid. Optimizer JSON
> outputs are informational; `config/config.yaml` remains the single source of truth, and a
> candidate is adopted only after it beats the committed config on **every** window (not just
> the holdout). See `BACKTEST_TRUTH.md` for rejected candidates.

### 7. Serving & reporting
- `api/` (FastAPI) exposes scanner/backtest/ohlcv/analysis routes; `sepa ui` launches it
  with the `sepa-terminal/` React (MUI 7) frontend — a Scanner workspace (daily triage,
  shortlist/reject) and a Backtest workspace (per-trade review, equity/drawdown charts).
- `alerts/` pushes signals to console and Telegram (console-only if creds absent).
- `reports/` collects generated CSV/markdown/PNG analysis (portfolio diagnostics, walk-forward,
  sector-cap sweeps, mean-reversion, skew). `LEADERBOARD.md` / `PERFORMANCE_SUMMARY.md` are
  historical logs — some entries are experimental, not the committed config.

## Known traps (why backtest numbers used to differ)
1. **Multiple engines.** `simulate_portfolio_python` (canonical, has equity curve) vs
   `engine._simulate_with_portfolio` (no equity curve) vs per-symbol simulators vs the Rust
   path. Only the canonical harness is trustworthy.
2. **Fake CAGR.** `metrics.estimate_compounded_cagr` compounds `1 + risk·R` serially as if
   every trade reinvests the full bankroll → inflated. The CLI prints this; ignore it.
3. **Two signal filters.** Raw `signals` table vs `filter_signals` (rs/quality/per-day). The
   canonical path always applies `filter_signals` with the committed `rs_rank_min`.
4. **Dead exit knobs.** With a committed `baseline_*` exit mode, phased `ExitConfig`
   overrides are inert; the hybrid reads `backtest.baseline_tighten_r` instead.
5. **Fundamental availability.** Reporting periods and statutory deadlines are not
   decision dates. Strict fundamental research must use actual NSE dissemination dates.

## Strategy abstraction & extension points

There are two layers of "strategy":

1. **Core SEPA signal engine** (`src/sepa_scanner/strategy/`) — the production strategy,
   parameterized entirely by `config/config.yaml` (trend template, RS, VCP, breakout, risk,
   exit, regime, sizing sections). Most tuning is a config change, not new code. To add a new
   *filter or pattern*: add a Polars indicator under `indicators/`, consume it in
   `strategy/signal_engine.py` (extend the trend/setup logic and the failure-reason bitmasks),
   expose its knobs in `config.yaml` + `settings.py`, then run `optimize_joint_wf.py --parity`
   to confirm no parity regression before tuning.

2. **Alternate self-contained strategies** (`strategies/`) — each is an independent engine
   with its own `config.yaml`, signal/exit/simulation modules, `output/`, and (for
   `mean_reversion`) a live paper runner and tests. To add a new strategy, mirror the
   `strategies/mean_reversion/` layout:
   ```
   strategies/<name>/
     config.yaml          # strategy params
     indicators.py        # feature computation
     signal_engine.py     # entry/exit signal generation
     simulation.py        # backtest/sim loop (reads the shared parquet panel)
     exit_engine.py       # exit rules
     live/                # optional paper/live runner
     tests/               # strategy-specific pytest
     output/              # generated reports
   ```
   Alternate strategies reuse the shared `data/yf_adjusted` panel but keep their own params
   and results; they do not affect the committed SEPA config.

Both layers read the same price panel and use Polars; the difference is that the core engine
is the deployed product (CLI/UI/alerts) while `strategies/` is a research sandbox.

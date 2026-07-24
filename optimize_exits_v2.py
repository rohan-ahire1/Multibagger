"""
Optuna optimizer for the phased exit engine (ExitConfig parameters).

Objective: maximise Calmar ratio (CAGR / MaxDD) on a fixed in-sample window.
Data is loaded once at module level to keep each trial fast.

Usage:
    python optimize_exits_v2.py [--trials N] [--jobs J] [--study NAME]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import optuna
import polars as pl

from sepa_scanner.backtest import (
    BacktestParams,
    ExitMode,
    simulate_portfolio_python,
    build_entry_controls,
    compute_regime_inputs,
    filter_signals,
)
from sepa_scanner.backtest.queries import (
    get_backtest_diagnostics,
    get_backtest_ohlcv,
    get_backtest_signals,
)
from sepa_scanner.settings import load_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.ranking import load_sector_map
import duckdb

logging.getLogger("sepa_scanner").setLevel(logging.WARNING)
optuna.logging.set_verbosity(optuna.logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
IN_SAMPLE_START = date(2015, 1, 1)
IN_SAMPLE_END = date(2023, 12, 31)

# Minimum bars / trades to avoid pruning
MIN_FILLED_TRADES = 30
MIN_CAGR = 0.05          # 5% — prune if strategy barely does anything

# ---------------------------------------------------------------------------
# Load shared data once
# ---------------------------------------------------------------------------
print("Loading settings and data...")
_settings = load_settings()
_parquet_store = ParquetOhlcvStore(_settings.runtime.data_dir / "yf_adjusted")
_conn = duckdb.connect(str(_settings.runtime.data_dir / "db" / "sepa.duckdb"), read_only=True)

_signals_raw = get_backtest_signals(_conn, start_date=IN_SAMPLE_START, end_date=IN_SAMPLE_END, symbol=None)
_symbols = _signals_raw["symbol"].unique().to_list()
_diagnostics = get_backtest_diagnostics(_conn, start_date=IN_SAMPLE_START, end_date=IN_SAMPLE_END, symbols=_symbols)
_ohlcv_end = IN_SAMPLE_END + timedelta(days=(_settings.backtest.pending_days + 90) * 2)
_ohlcv = get_backtest_ohlcv(_parquet_store, symbols=_symbols, start_date=IN_SAMPLE_START, end_date=_ohlcv_end)
_conn.close()

# Adjust signal prices to OHLCV adjusted basis (same logic as diagnostics)
_sig_adj = _signals_raw.join(
    _ohlcv.select(["date", "symbol", "close"]).rename({"close": "ohlcv_close", "date": "signal_date"}),
    on=["signal_date", "symbol"],
    how="inner",
).with_columns(
    (pl.col("ohlcv_close") / pl.col("close")).alias("_adj_factor")
).with_columns(
    (pl.col("entry_ref") * pl.col("_adj_factor")).alias("entry_ref"),
    (pl.col("stop") * pl.col("_adj_factor")).alias("stop"),
    (
        pl.when(pl.col("pivot").is_not_null())
        .then(pl.col("pivot") * pl.col("_adj_factor"))
        .otherwise(None)
    ).alias("pivot"),
).drop(["ohlcv_close", "_adj_factor"])

_sector_csv = Path(_settings.runtime.data_dir).parent / "reference" / "nifty500_constituents.csv"
_sector_map = load_sector_map(_sector_csv)

_base_params = BacktestParams(
    pending_days=_settings.backtest.pending_days,
    max_hold_days=max(_settings.backtest.max_hold_days, 120),
    target_r=_settings.risk.target_r,
    exit_mode=ExitMode(str(_settings.backtest.exit_mode)),
    baseline_tighten_r=_settings.backtest.baseline_tighten_r,
    trail_activation_r=_settings.exit_strategy.trail_activation_r,
    trail_pct=_settings.exit_strategy.trail_pct,
    dead_money_days=_settings.exit_strategy.dead_money_days,
    dead_money_extend_days=_settings.exit_strategy.dead_money_extend_days,
    dead_money_min_open_r=_settings.exit_strategy.dead_money_min_open_r,
    dead_money_min_mfe_r=_settings.exit_strategy.dead_money_min_mfe_r,
    dead_money_extend_to_breakeven=_settings.exit_strategy.dead_money_extend_to_breakeven,
)

_base_portfolio = {
    "initial_capital": _settings.portfolio.initial_capital,
    "risk_per_trade_pct": _settings.portfolio.risk_per_trade_pct,
    "max_positions": _settings.portfolio.max_positions,
    "max_sector_positions": _settings.portfolio.max_sector_positions,
    "max_sector_pct": _settings.portfolio.max_sector_pct,
    "max_heat_pct": _settings.portfolio.max_heat_pct,
    "drawdown_half_pct": _settings.portfolio.drawdown_half_pct,
    "drawdown_stop_pct": _settings.portfolio.drawdown_stop_pct,
    "drawdown_close_pct": _settings.portfolio.drawdown_close_pct,
    "regime_gate_enabled": _settings.portfolio.regime_gate_enabled,
    "regime_high_volatility_threshold": _settings.portfolio.regime_high_volatility_threshold,
    "regime_cooldown_days": _settings.portfolio.regime_cooldown_days,
    "breadth_soft_floor_pct": _settings.portfolio.breadth_soft_floor_pct,
    "breadth_soft_size_mult": _settings.portfolio.breadth_soft_size_mult,
    "pyramid_max_adds": _settings.portfolio.pyramid_max_adds,
    "pyramid_trigger_r": _settings.portfolio.pyramid_trigger_r,
    "pyramid_add_interval_r": _settings.portfolio.pyramid_add_interval_r,
    "pyramid_risk_frac": _settings.portfolio.pyramid_risk_frac,
}

_filtered_signals = filter_signals(
    _sig_adj,
    settings=_settings,
    diagnostics=_diagnostics,
    sector_map=_sector_map,
    rs_rank_min=_settings.backtest.rs_rank_min,
    rs_rank_max=_settings.backtest.rs_rank_max,
    ranking_mode=_settings.backtest.signal_ranking_mode,
    max_signals_per_day=_settings.backtest.max_signals_per_day,
)

print(f"Signals loaded: {_filtered_signals.height}")
print(f"OHLCV rows: {_ohlcv.height}")
print("Data ready. Starting optimization...\n")


# ---------------------------------------------------------------------------
# Helper: compute stats from daily_equity
# ---------------------------------------------------------------------------

def _compute_calmar(daily_equity: pl.DataFrame, initial_capital: float) -> tuple[float, float, float]:
    """Returns (cagr, max_dd_pct, calmar)."""
    if daily_equity.is_empty():
        return 0.0, 1.0, 0.0

    eq = daily_equity.sort("date")
    first_date = eq["date"][0]
    last_date = eq["date"][-1]
    years = max((last_date - first_date).days / 365.25, 0.01)

    final_equity = float(eq["equity"][-1])
    total_return = (final_equity / initial_capital) - 1.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0

    # Max drawdown
    peak = eq["equity"].cum_max()
    dd = (eq["equity"] - peak) / peak
    max_dd = float(dd.min())  # negative number

    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0
    return cagr, abs(max_dd), calmar


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

def objective(trial: optuna.Trial) -> float:
    # --- Search space ---
    # Phase transition thresholds
    breakeven_r = trial.suggest_float("breakeven_activation_r", 0.8, 3.0, step=0.1)
    partial_r = trial.suggest_float("partial_profit_r", 1.5, 5.0, step=0.25)
    partial_pct = trial.suggest_float("partial_profit_pct", 0.25, 0.60, step=0.05)

    # Trailing stops
    trail_pct = trial.suggest_float("trail_pct", 0.10, 0.30, step=0.02)
    trail_pct_bull = trial.suggest_float("trail_pct_bull", 0.15, 0.40, step=0.02)
    trail_pct_bear = trial.suggest_float("trail_pct_bear", 0.08, 0.20, step=0.02)
    trail_widen_r = trial.suggest_float("trail_widen_r", 2.0, 5.0, step=0.5)

    # Time controls
    time_stop_days = trial.suggest_int("time_stop_days", 10, 25)
    time_stop_min_r = trial.suggest_float("time_stop_min_r", 0.0, 0.5, step=0.05)
    max_hold_before_tighten = trial.suggest_int("max_hold_before_tighten", 40, 90, step=5)

    # Soft expiry rotation
    soft_expiry_days = trial.suggest_int("soft_expiry_days", 25, 75, step=5)
    soft_expiry_gain = trial.suggest_float("soft_expiry_gain", 0.10, 0.40, step=0.05)

    # Constraint: partial_r must be >= breakeven_r
    if partial_r < breakeven_r:
        raise optuna.TrialPruned()

    exit_config = {
        "breakeven_activation_r": breakeven_r,
        "partial_profit_r": partial_r,
        "partial_profit_pct": partial_pct,
        "trail_pct": trail_pct,
        "trail_pct_bull": trail_pct_bull,
        "trail_pct_bear": trail_pct_bear,
        "trail_widen_r": trail_widen_r,
        "time_stop_days": time_stop_days,
        "time_stop_min_r": time_stop_min_r,
        "max_hold_before_tighten": max_hold_before_tighten,
        "soft_expiry_days": soft_expiry_days,
        "soft_expiry_gain": soft_expiry_gain,
    }

    try:
        result = simulate_portfolio_python(
            _filtered_signals,
            _ohlcv,
            _base_params,
            _base_portfolio,
            _sector_map,
            exit_config=exit_config,
        )
    except Exception as e:
        raise optuna.TrialPruned() from e

    trades = result["trades"]
    daily_equity = result["daily_equity"]

    filled = [t for t in trades if t.entry_date is not None]
    if len(filled) < MIN_FILLED_TRADES:
        raise optuna.TrialPruned()

    initial_capital = float(_base_portfolio["initial_capital"])
    cagr, max_dd, calmar = _compute_calmar(daily_equity, initial_capital)

    if cagr < MIN_CAGR:
        raise optuna.TrialPruned()

    # Win stats for reporting
    r_multiples = [t.r_multiple for t in filled if t.r_multiple is not None]
    winners = [r for r in r_multiples if r > 0]
    losers = [r for r in r_multiples if r <= 0]
    win_rate = len(winners) / len(r_multiples) if r_multiples else 0.0
    avg_winner = float(np.mean(winners)) if winners else 0.0
    avg_loser = float(np.mean(losers)) if losers else 0.0
    expectancy = (win_rate * avg_winner) + ((1 - win_rate) * avg_loser)

    trial.set_user_attr("cagr_pct", round(cagr * 100, 2))
    trial.set_user_attr("max_dd_pct", round(max_dd * 100, 2))
    trial.set_user_attr("calmar", round(calmar, 3))
    trial.set_user_attr("n_trades", len(filled))
    trial.set_user_attr("win_rate", round(win_rate * 100, 1))
    trial.set_user_attr("expectancy_r", round(expectancy, 3))
    trial.set_user_attr("avg_winner_r", round(avg_winner, 3))

    return calmar


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize phased exit engine parameters")
    parser.add_argument("--trials", type=int, default=200, help="Number of trials (default: 200)")
    parser.add_argument("--jobs", type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--study", type=str, default="phased_exit_v2", help="Study name")
    parser.add_argument("--db", type=str, default="sqlite:///optuna_exits_v2.db", help="Storage URL")
    args = parser.parse_args()

    study = optuna.create_study(
        study_name=args.study,
        storage=args.db,
        load_if_exists=True,
        direction="maximize",
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=15),
    )

    print(f"Study: {args.study} | Trials: {args.trials} | Workers: {args.jobs}")
    print(f"In-sample: {IN_SAMPLE_START} → {IN_SAMPLE_END}\n")

    try:
        study.optimize(objective, n_trials=args.trials, n_jobs=args.jobs, show_progress_bar=True)
    except KeyboardInterrupt:
        print("\n[Interrupted — saving results]")

    print("\n" + "=" * 60)
    print("OPTIMIZATION COMPLETE")
    print("=" * 60)

    try:
        best = study.best_trial
        print(f"\nBest Calmar: {best.value:.3f}")
        attrs = best.user_attrs
        print(f"  CAGR:        {attrs.get('cagr_pct', '?')}%")
        print(f"  Max DD:      {attrs.get('max_dd_pct', '?')}%")
        print(f"  Trades:      {attrs.get('n_trades', '?')}")
        print(f"  Win Rate:    {attrs.get('win_rate', '?')}%")
        print(f"  Expectancy:  {attrs.get('expectancy_r', '?')} R")
        print(f"  Avg Winner:  {attrs.get('avg_winner_r', '?')} R")
        print("\nBest Parameters:")
        for k, v in best.params.items():
            print(f"  {k}: {v}")

        # Print top 5 by Calmar
        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        top5 = sorted(completed, key=lambda t: t.value, reverse=True)[:5]
        print("\n--- Top 5 Trials ---")
        for i, t in enumerate(top5, 1):
            a = t.user_attrs
            print(
                f"#{i} Calmar={t.value:.3f}  CAGR={a.get('cagr_pct','?')}%"
                f"  DD={a.get('max_dd_pct','?')}%"
                f"  Trades={a.get('n_trades','?')}"
                f"  WR={a.get('win_rate','?')}%"
                f"  E={a.get('expectancy_r','?')}R"
            )

        # Dump best params to JSON for easy copy-paste into settings
        out_path = Path("best_exit_params.json")
        out_path.write_text(json.dumps(best.params, indent=2))
        print(f"\nBest params saved to {out_path}")

    except ValueError:
        print("No successful trials completed.")


if __name__ == "__main__":
    main()

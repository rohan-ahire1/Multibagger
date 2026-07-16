import optuna
import numpy as np
import logging
from pathlib import Path
import copy
from datetime import date
import polars as pl
import json
import sys

from sepa_scanner.settings import load_settings
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.metrics import summarize_backtest
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.backtest.portfolio import PortfolioManager
from sepa_scanner.backtest.regime_gate import compute_regime_gate

# Suppress engine logs
logging.getLogger("sepa_scanner").setLevel(logging.WARNING)
optuna.logging.set_verbosity(optuna.logging.INFO)

# 1. SHARED DATA LAYER (Loaded once)
settings = load_settings()
store = ParquetOhlcvStore(settings.runtime.data_dir / "yf_adjusted")
db = DuckDbStore(settings.runtime.data_dir / "db" / "sepa.duckdb")

# Load signals for the compressed window (plus some buffer)
ALL_SIGNALS = db._connect().execute(
    "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' AND signal_date BETWEEN '2021-01-01' AND '2023-06-01'"
).pl()

# Load OHLCV data
ALL_OHLCV = store.read_frame(start_date=date(2021, 1, 1), end_date=date(2023, 6, 1))

def build_portfolio_equity_curve(trades, initial_capital, risk_per_trade_pct):
    closed_trades = sorted(
        [t for t in trades if t.exit_date and t.r_multiple is not None],
        key=lambda t: (t.exit_date, t.symbol)
    )
    if not closed_trades:
        return pl.DataFrame()
    
    equity = initial_capital
    peak = initial_capital
    rows = []
    
    for t in closed_trades:
        # Match PortfolioManager drawdown circuit breakers
        drawdown_pct = (peak - equity) / peak if peak > 0 else 0
        
        size_mult = 1.0
        if drawdown_pct >= 0.25: # drawdown_stop_pct
            size_mult = 0.0
        elif drawdown_pct >= 0.15: # drawdown_half_pct
            size_mult = 0.5
            
        pnl = equity * risk_per_trade_pct * size_mult * float(t.r_multiple)
        equity += pnl
        peak = max(peak, equity)
        
        rows.append({
            "date": t.exit_date,
            "equity": equity,
            "drawdown_pct": (equity / peak) - 1 if peak > 0 else 0
        })
    return pl.DataFrame(rows)

def objective(trial):
    test_settings = copy.deepcopy(settings)
    
    # 1. FREEZE Portfolio Risk
    test_settings.portfolio.max_heat_pct = 0.25 
    test_settings.portfolio.regime_heat_mult_strong = 1.2
    test_settings.market_filter.breadth_strong_pct = 0.60
    
    # 2. ISOLATE Search Space
    test_settings.risk.struct_stop_atr_mult = trial.suggest_float("struct_stop_atr_mult", 1.5, 3.5, step=0.1)
    test_settings.exit_strategy.breakeven_activation_r = trial.suggest_float("breakeven_activation_r", 1.0, 3.0, step=0.1)
    test_settings.exit_strategy.trail_activation_r = trial.suggest_float("trail_activation_r", 1.5, 4.0, step=0.1)
    test_settings.exit_strategy.trail_pct = trial.suggest_float("trail_pct", 0.05, 0.25, step=0.01)

    start = date(2021, 4, 1)
    end = date(2023, 4, 1)
    
    signals = ALL_SIGNALS.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
    
    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=settings.backtest.max_hold_days,
        exit_mode=ExitMode.MINERVINI_PHASED,
        trail_activation_r=test_settings.exit_strategy.trail_activation_r,
        trail_pct=test_settings.exit_strategy.trail_pct,
        dead_money_days=test_settings.exit_strategy.dead_money_days,
    )
    
    pm = PortfolioManager(
        initial_capital=test_settings.portfolio.initial_capital,
        risk_per_trade_pct=test_settings.portfolio.risk_per_trade_pct,
        max_positions=test_settings.portfolio.max_positions,
        max_sector_positions=test_settings.portfolio.max_sector_positions,
        max_sector_pct=test_settings.portfolio.max_sector_pct,
        max_heat_pct=test_settings.portfolio.max_heat_pct,
        regime_heat_mult_strong=test_settings.portfolio.regime_heat_mult_strong,
        drawdown_half_pct=test_settings.portfolio.drawdown_half_pct,
        drawdown_stop_pct=test_settings.portfolio.drawdown_stop_pct,
        drawdown_close_pct=test_settings.portfolio.drawdown_close_pct,
    )
    
    gate_df = compute_regime_gate(
        store,
        benchmark_symbol=test_settings.market.benchmark_symbol,
        start_date=start,
        end_date=end,
        breadth_weak_pct=test_settings.market_filter.breadth_weak_pct,
        high_volatility_threshold=test_settings.portfolio.regime_high_volatility_threshold
    )
    entry_gate = {row["date"]: row["entries_allowed"] for row in gate_df.to_dicts()}

    result = simulate_breakout_backtest(
        signals_df=signals,
        ohlcv_df=ALL_OHLCV,
        params=params,
        portfolio_manager=pm,
        use_rust=False,
        entry_gate=entry_gate
    )
    
    summary = summarize_backtest(result.trades)
    curve = build_portfolio_equity_curve(
        result.trades, 
        test_settings.portfolio.initial_capital, 
        test_settings.portfolio.risk_per_trade_pct
    )
    
    if curve.is_empty():
        raise optuna.TrialPruned()

    final_equity = curve["equity"].item(-1)
    total_return = (final_equity / test_settings.portfolio.initial_capital) - 1
    years = (end - start).days / 365.25
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    mdd = abs(curve["drawdown_pct"].min()) * 100

    n_trades = summary.filled_trades
    expectancy = summary.expectancy

    # Minimal pruning
    if n_trades < 5:
        raise optuna.TrialPruned()

    mar_ratio = (cagr * 100) / mdd if mdd > 0 else 0
    fitness = expectancy * np.log(n_trades) * mar_ratio
    
    trial.set_user_attr("Expectancy", expectancy)
    trial.set_user_attr("Trades", n_trades)
    trial.set_user_attr("MAR", mar_ratio)

    return fitness

if __name__ == "__main__":
    study = optuna.create_study(
        study_name="sepa_exits_only_v1", 
        storage="sqlite:///optuna_fast_study.db", 
        load_if_exists=True,
        direction="maximize",
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=5)
    )
    
    print("Starting Optimization (100 trials, parallel workers)...")
    try:
        study.optimize(objective, n_trials=100, n_jobs=-1)
    except KeyboardInterrupt:
        print("\n[Optimization Paused]")
        sys.exit(0)

    print("\n" + "="*50)
    print("OPTIMIZATION COMPLETE")
    print("="*50)
    
    try:
        print(f"Best Fitness Score: {study.best_value:.4f}")
        print("Best Parameters:", json.dumps(study.best_params, indent=2))
    except ValueError:
        print("No successful trials completed.")

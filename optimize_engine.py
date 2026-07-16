import optuna
import numpy as np
import logging
from pathlib import Path
import copy
from datetime import date
import polars as pl
import json

from sepa_scanner.settings import load_settings
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.metrics import summarize_backtest, build_equity_curve
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.backtest.portfolio import PortfolioManager
from sepa_scanner.backtest.regime_gate import compute_regime_gate

# Suppress engine logs
logging.getLogger("sepa_scanner").setLevel(logging.WARNING)

# 1. SHARED DATA LAYER (Loaded once)
settings = load_settings()
store = ParquetOhlcvStore(settings.runtime.data_dir / "yf_adjusted")
db = DuckDbStore(settings.runtime.data_dir / "db" / "sepa.duckdb")

# Load all signals once
ALL_SIGNALS = db._connect().execute(
    "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' AND signal_date BETWEEN '2010-01-01' AND '2026-03-31'"
).pl()

# Load all OHLCV data once
ALL_OHLCV = store.read_frame()

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
    
    # State machine for drawdown multipliers (matching PortfolioManager logic)
    for t in closed_trades:
        # Calculate current drawdown multiplier
        drawdown_pct = (peak - equity) / peak if peak > 0 else 0
        
        size_mult = 1.0
        if drawdown_pct >= 0.25: # drawdown_stop_pct
            size_mult = 0.0
        elif drawdown_pct >= 0.15: # drawdown_half_pct
            size_mult = 0.5
            
        # PnL = Equity * Risk% * Multiplier * R_Multiple
        pnl = equity * risk_per_trade_pct * size_mult * float(t.r_multiple)
        
        equity += pnl
        peak = max(peak, equity)
        
        rows.append({
            "date": t.exit_date,
            "equity": equity,
            "drawdown_pct": (equity / peak) - 1 if peak > 0 else 0
        })
    return pl.DataFrame(rows)

def run_simulation(test_settings, start_date, end_date):
    # Filter data for range
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    
    signals = ALL_SIGNALS.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
    
    # We need the full OHLCV for indicators/trailing but simulate_breakout_backtest handles the windowing
    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=settings.backtest.max_hold_days,
        exit_mode=ExitMode(test_settings.backtest.exit_mode),
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
    
    # Optional regime gate
    entry_gate = None
    if test_settings.portfolio.regime_gate_enabled:
        gate_df = compute_regime_gate(
            store,
            benchmark_symbol=test_settings.market.benchmark_symbol,
            start_date=start,
            end_date=end,
            breadth_weak_pct=test_settings.market_filter.breadth_weak_pct,
            high_volatility_threshold=test_settings.portfolio.regime_high_volatility_threshold
        )
        entry_gate = {row["date"]: row["entries_allowed"] for row in gate_df.to_dicts()}

    # Use Python engine because Rust isn't compiled for the new logic yet
    result = simulate_breakout_backtest(
        signals_df=signals,
        ohlcv_df=ALL_OHLCV,
        params=params,
        portfolio_manager=pm,
        use_rust=False,
        entry_gate=entry_gate
    )
    
    summary = summarize_backtest(result.trades)
    
    # Calculate CAGR and Max Drawdown from equity curve
    curve = build_portfolio_equity_curve(
        result.trades, 
        test_settings.portfolio.initial_capital,
        test_settings.portfolio.risk_per_trade_pct
    )
    
    # Manual metric calculation
    if not curve.is_empty():
        final_equity = curve["equity"][-1]
        total_return = (final_equity / test_settings.portfolio.initial_capital) - 1
        years = (end - start).days / 365.25
        cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        mdd = curve["drawdown_pct"].min()
    else:
        cagr = 0
        mdd = -1.0

    return {
        "expectancy": summary.expectancy,
        "n_trades": summary.filled_trades,
        "cagr": cagr * 100, # Percentage
        "mdd": abs(mdd) * 100 # Positive percentage
    }

def objective(trial):
    # 1. Clone settings
    test_settings = copy.deepcopy(settings)
    
    # 2. Suggest Parameters
    test_settings.risk.struct_stop_atr_mult = trial.suggest_float("struct_stop_atr_mult", 1.5, 3.5, step=0.1)
    test_settings.exit_strategy.breakeven_activation_r = trial.suggest_float("breakeven_activation_r", 1.0, 3.0, step=0.1)
    test_settings.exit_strategy.trail_activation_r = trial.suggest_float("trail_activation_r", 1.5, 4.5, step=0.1)
    test_settings.exit_strategy.trail_pct = trial.suggest_float("trail_pct", 0.05, 0.25, step=0.01)
    
    test_settings.portfolio.max_heat_pct = trial.suggest_float("max_heat_pct", 0.15, 0.50, step=0.05)
    test_settings.portfolio.regime_heat_mult_strong = trial.suggest_float("regime_heat_mult_strong", 1.0, 2.0, step=0.1)
    
    # 3. Run In-Sample (2021-2026)
    res = run_simulation(test_settings, "2021-01-01", "2026-03-01")
    
    # 4. Guardrails
    if res["n_trades"] < 40:
        raise optuna.TrialPruned()
    if res["mdd"] > 30.0:
        raise optuna.TrialPruned()
    if res["expectancy"] <= 0:
        return -1.0

    # 5. Fitness
    mar_ratio = res["cagr"] / res["mdd"] if res["mdd"] > 0 else 0
    fitness = res["expectancy"] * np.log(res["n_trades"]) * mar_ratio
    
    trial.set_user_attr("Expectancy", res["expectancy"])
    trial.set_user_attr("Trades", res["n_trades"])
    trial.set_user_attr("CAGR", res["cagr"])
    trial.set_user_attr("MDD", res["mdd"])
    
    return fitness

def validate_oos(best_params):
    print("\n" + "="*50)
    print("RUNNING OUT-OF-SAMPLE VERIFICATION (2015-2019)")
    print("="*50)
    
    test_settings = copy.deepcopy(settings)
    for k, v in best_params.items():
        if k in ["struct_stop_atr_mult"]:
            test_settings.risk.__setattr__(k, v)
        elif k in ["max_heat_pct", "regime_heat_mult_strong"]:
            test_settings.portfolio.__setattr__(k, v)
        elif k in ["breakeven_activation_r", "trail_activation_r", "trail_pct"]:
            test_settings.exit_strategy.__setattr__(k, v)

    res = run_simulation(test_settings, "2015-01-01", "2019-12-31")
    
    print(f"OOS Expectancy: {res['expectancy']:.3f} R")
    print(f"OOS Total Trades: {res['n_trades']}")
    print(f"OOS CAGR: {res['cagr']:.2f}%")
    print(f"OOS Max Drawdown: {res['mdd']:.2f}%")
    
    if res["expectancy"] > 0.1 and res["mdd"] < 35.0:
        print("\n[VERDICT: EDGE VERIFIED. The parameters survived the 2018 crash.]")
    else:
        print("\n[VERDICT: CURVE FIT DETECTED. Edge collapsed in out-of-sample data.]")

if __name__ == "__main__":
    study = optuna.create_study(direction="maximize")
    print("Starting Optimization (500 trials, 8 parallel workers)...")
    study.optimize(objective, n_trials=500, n_jobs=-1)
    
    print("\n" + "="*50)
    print("OPTIMIZATION COMPLETE")
    print("="*50)
    print(f"Best Fitness Score: {study.best_value:.4f}")
    print("Best Parameters:", json.dumps(study.best_params, indent=2))
    
    validate_oos(study.best_params)

import json
import time
import polars as pl
import numpy as np
import sys
import os
from datetime import date
from sepa_scanner.backtest.models import BacktestParams, ExitMode, BacktestTrade
from sepa_scanner.backtest.metrics import summarize_backtest
import multibagger_core

def run_strategy_battle():
    print("--------------------------------------------------")
    print("⚔️  STRATEGY BATTLE: COMPARING EXIT MODES")
    print("--------------------------------------------------")
    
    db_path = "data/db/sepa.duckdb"
    ohlcv_path = "data/yf_adjusted/ohlcv_all.parquet"
    
    print(f"Loading data...")
    ohlcv_df = pl.read_parquet(ohlcv_path).sort(["symbol", "date"])
    
    symbol_indices = {}
    current_idx = 0
    counts = ohlcv_df.group_by("symbol", maintain_order=True).len()
    for row in counts.iter_rows():
        sym, count = row[0], row[1]
        symbol_indices[sym] = (current_idx, current_idx + count)
        current_idx += count

    all_dates = [d.strftime("%Y-%m-%d") for d in ohlcv_df["date"]]
    opens = ohlcv_df["open"].to_numpy().astype(np.float64)
    highs = ohlcv_df["high"].to_numpy().astype(np.float64)
    lows = ohlcv_df["low"].to_numpy().astype(np.float64)
    closes = ohlcv_df["close"].to_numpy().astype(np.float64)

    import duckdb
    conn = duckdb.connect(db_path, read_only=True)
    signals_df = conn.execute("SELECT symbol, signal_date, entry_ref, stop, \"pivot\", rs_rank FROM signals WHERE signal_type = 'BREAKOUT'").pl()
    
    rust_signals = []
    for sig in signals_df.to_dicts():
        rust_signals.append({
            "symbol": str(sig["symbol"]),
            "signal_date": sig["signal_date"].strftime("%Y-%m-%d"),
            "entry_ref": float(sig["entry_ref"]),
            "stop": float(sig["stop"]),
            "pivot": float(sig["pivot"]) if sig.get("pivot") is not None else None,
            "rs_rank": float(sig.get("rs_rank", 80.0))
        })
    signals_json = json.dumps(rust_signals)

    # 1. Define the Strategies to Battle
    modes_to_test = [
        ExitMode.V1_FIXED,
        ExitMode.V2_TRAIL_10D,
        ExitMode.V3_TRAIL_PCT,
        ExitMode.MINERVINI_PHASED,
        ExitMode.ADVANCED_SEPA
    ]
    
    battle_results = []

    for mode in modes_to_test:
        print(f"\nEvaluating: {mode.value}...")
        
        params = BacktestParams(
            pending_days=8,
            max_hold_days=150,
            dead_money_days=20,
            trail_activation_r=1.5,
            trail_pct=0.15, # 15% trail for V3
            exit_mode=mode
        )
        
        params_json = json.dumps({
            "pending_days": params.pending_days,
            "max_hold_days": params.max_hold_days,
            "target_r": params.target_r,
            "exit_mode": params.exit_mode.value,
            "trail_activation_r": params.trail_activation_r,
            "trail_pct": params.trail_pct,
            "dead_money_days": params.dead_money_days
        })

        portfolio_json = json.dumps({
            "initial_capital": 1000000.0,
            "risk_per_trade_pct": 0.05,
            "max_positions": 30,
            "max_heat_pct": 0.30,
            "drawdown_half_pct": 0.20,
            "drawdown_stop_pct": 0.25,
            "drawdown_close_pct": 0.35,
        })

        res_json = multibagger_core.run_portfolio_backtest_rust(
            signals_json, params_json, portfolio_json, all_dates,
            opens, highs, lows, closes, symbol_indices
        )
        
        full_res = json.loads(res_json)
        raw_trades = full_res["trades"]
        initial_cap = full_res["initial_capital"]
        final_eq = full_res["final_equity"]

        trades = [BacktestTrade(
            symbol=rt["symbol"],
            signal_date=date.fromisoformat(rt["signal_date"]),
            status=rt["status"],
            entry_ref=rt["entry_ref"],
            stop=rt["stop"],
            pivot=rt["pivot"],
            entry_date=date.fromisoformat(rt["entry_date"]) if rt["entry_date"] else None,
            entry_price=rt["entry_price"],
            exit_date=date.fromisoformat(rt["exit_date"]) if rt["exit_date"] else None,
            exit_price=rt["exit_price"],
            holding_days=rt["holding_days"],
            return_pct=rt["return_pct"],
            r_multiple=rt["r_multiple"],
            mae_price=rt["mae_price"],
            mfe_price=rt["mfe_price"],
        ) for rt in raw_trades]
        
        summary = summarize_backtest(trades, exit_mode=params.exit_mode)
        battle_results.append({
            "mode": mode.value,
            "expectancy": summary.expectancy,
            "cum_r": summary.cumulative_r,
            "win_rate": summary.win_rate,
            "final_equity": final_eq,
            "total_return": (final_eq - initial_cap) / initial_cap * 100
        })
        print(f" -> Result: Expectancy={summary.expectancy:+.3f}R, Return={battle_results[-1]['total_return']:.2f}%")

    # Final Leaderboard
    print("\n--------------------------------------------------")
    print("🏆 STRATEGY LEADERBOARD (Portfolio Mode)")
    print("--------------------------------------------------")
    ranked = sorted(battle_results, key=lambda x: x["expectancy"], reverse=True)
    for i, res in enumerate(ranked):
        print(f"{i+1}. {res['mode']:<20} | Exp: {res['expectancy']:+.4f}R | Return: {res['total_return']:>6.2f}% | Final: ₹{res['final_equity']:,.0f}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    run_strategy_battle()

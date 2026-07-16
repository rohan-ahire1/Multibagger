import json
import time
import polars as pl
import numpy as np
from datetime import date
import sys
import os

# Add multibagger to path
sys.path.append(os.path.abspath("../multibagger/src"))

from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.engine import _simulate_signal

# Import our new Rust core
sys.path.append(os.path.abspath("../multibagger/rust_core"))
import multibagger_core

def test_rust_vs_python():
    print("--------------------------------------------------")
    print("Loading test data...")
    df = pl.read_parquet("../data/yf_adjusted/ohlcv_all.parquet")
    
    # Pick a symbol with enough data
    symbol = "TCS"
    symbol_df = df.filter(pl.col("symbol") == symbol).sort("date")
    
    # Create a dummy signal that is guaranteed to fill
    # Look for a price increase shortly after
    signal_idx = 100
    signal_date = symbol_df["date"][signal_idx]
    
    # Set entry_ref slightly above current close to ensure it fills
    entry_ref = symbol_df["close"][signal_idx] * 1.01
    # Set stop significantly below to avoid immediate stop
    stop = symbol_df["close"][signal_idx] * 0.90
    
    signal = {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_ref": entry_ref,
        "stop": stop,
        "pivot": entry_ref,
        "rs_rank": 90.0
    }
    
    params = BacktestParams(
        pending_days=10, # Give it more time to fill
        max_hold_days=90,
        exit_mode=ExitMode.V1_FIXED
    )
    
    # Convert types for JSON serialization (dates to strings)
    signal_json = json.dumps({
        "symbol": signal["symbol"],
        "signal_date": signal["signal_date"].strftime("%Y-%m-%d"),
        "entry_ref": signal["entry_ref"],
        "stop": signal["stop"],
        "pivot": signal["pivot"],
        "rs_rank": signal["rs_rank"]
    })
    
    params_json = json.dumps({
        "pending_days": params.pending_days,
        "max_hold_days": params.max_hold_days,
        "target_r": params.target_r,
        "exit_mode": params.exit_mode.value,
        "trail_activation_r": params.trail_activation_r,
        "trail_pct": params.trail_pct,
        "dead_money_days": params.dead_money_days
    })
    
    dates = [d.strftime("%Y-%m-%d") for d in symbol_df["date"]]
    opens = symbol_df["open"].to_numpy().astype(np.float64)
    highs = symbol_df["high"].to_numpy().astype(np.float64)
    lows = symbol_df["low"].to_numpy().astype(np.float64)
    closes = symbol_df["close"].to_numpy().astype(np.float64)

    # --- 1. Python Simulation ---
    print("\n[1] Running Python simulation...")
    symbol_prices = symbol_df.to_dicts()
    start_time = time.time()
    py_trade = _simulate_signal(signal, symbol_prices, params)
    py_time = time.time() - start_time
    print(f" -> Python Result: {py_trade.status} at {py_trade.exit_price} on {py_trade.exit_date}")
    print(f" -> Python Time:   {py_time:.6f} seconds")

    # --- 2. Rust Simulation ---
    print("\n[2] Running Rust simulation...")
    start_time = time.time()
    rust_trade_json = multibagger_core.run_signal_backtest_rust(
        signal_json, params_json, dates, opens, highs, lows, closes
    )
    rust_trade = json.loads(rust_trade_json)
    rust_time = time.time() - start_time
    print(f" -> Rust Result:   {rust_trade['status']} at {rust_trade['exit_price']} on {rust_trade['exit_date']}")
    print(f" -> Rust Time:     {rust_time:.6f} seconds")

    print("\n--------------------------------------------------")
    print("Verification:")
    if abs(py_trade.exit_price - rust_trade['exit_price']) < 1e-4:
        print("✅ SUCCESS: Python and Rust exit prices match!")
    else:
        print(f"❌ MISMATCH: Python={py_trade.exit_price}, Rust={rust_trade['exit_price']}")
        
    speedup = py_time / rust_time
    print(f"🚀 Speedup: {speedup:.1f}x (for single signal)")
    print("--------------------------------------------------")

if __name__ == "__main__":
    test_rust_vs_python()

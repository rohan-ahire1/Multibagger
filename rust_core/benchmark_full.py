import json
import time
import polars as pl
import numpy as np
import sys
import os
from datetime import date

# Add multibagger to path
sys.path.append(os.path.abspath("../src"))

from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.engine import _simulate_signal

# Import our new Rust core
import multibagger_core

def run_benchmarks():
    print("--------------------------------------------------")
    print("Loading DATA (Signals + OHLCV)...")
    
    ohlcv_df = pl.read_parquet("../data/yf_adjusted/ohlcv_all.parquet")
    all_symbols = ohlcv_df["symbol"].unique().to_list()
    
    print(f"Loaded {ohlcv_df.height:,} OHLCV rows across {len(all_symbols)} symbols.")
    
    # Generate 2000 random signals for a massive benchmark
    print(f"Generating 2,000 synthetic signals...")
    signals = []
    for i in range(2000):
        sym = all_symbols[i % len(all_symbols)]
        # Pick a random date from the middle of the dataset
        signals.append({
            "symbol": sym,
            "signal_date": date(2024, 1, 1), # Simplified for bench
            "entry_ref": 100.0, # Will be adjusted per stock in loop
            "stop": 95.0,
            "pivot": 100.0,
            "rs_rank": 90.0
        })

    params = BacktestParams(
        pending_days=10,
        max_hold_days=90,
        exit_mode=ExitMode.V1_FIXED
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

    # Prepare data for Rust (Organize OHLCV by symbol)
    print("Pre-processing OHLCV data for Rust...")
    symbol_data = {}
    
    # We need to pass dictionaries of lists for the bulk function
    all_dates = {}
    all_opens = {}
    all_highs = {}
    all_lows = {}
    all_closes = {}
    
    for sym in all_symbols:
        df_sym = ohlcv_df.filter(pl.col("symbol") == sym).sort("date")
        
        # For Rust bulk
        all_dates[sym] = [d.strftime("%Y-%m-%d") for d in df_sym["date"]]
        all_opens[sym] = df_sym["open"].to_numpy().astype(np.float64).tolist()
        all_highs[sym] = df_sym["high"].to_numpy().astype(np.float64).tolist()
        all_lows[sym] = df_sym["low"].to_numpy().astype(np.float64).tolist()
        all_closes[sym] = df_sym["close"].to_numpy().astype(np.float64).tolist()
        
        # For Python (keep as dicts)
        symbol_data[sym] = df_sym.to_dicts()

    # --- 1. Python Benchmark ---
    print(f"\n[1] Running Python Backtest (2,000 signals)...")
    start_time = time.time()
    py_results = []
    # Make a copy of signals to adjust them
    py_signals = [sig.copy() for sig in signals]
    for sig in py_signals:
        sym = sig["symbol"]
        if sym in all_closes:
            # Adjust entry/stop to be realistic for this stock
            first_price = all_closes[sym][0]
            sig["entry_ref"] = first_price * 1.05
            sig["stop"] = first_price * 0.95
            
            res = _simulate_signal(sig, symbol_data[sym], params)
            py_results.append(res)
    py_time = time.time() - start_time
    print(f" -> Python Time: {py_time:.4f} seconds")

    # --- 2. Rust Benchmark ---
    print(f"\n[2] Running Rust Bulk Parallel Backtest (2,000 signals)...")
    rust_signals = [sig.copy() for sig in signals]
    for sig in rust_signals:
        sym = sig["symbol"]
        if sym in all_closes:
            first_price = all_closes[sym][0]
            sig["entry_ref"] = first_price * 1.05
            sig["stop"] = first_price * 0.95
            sig["signal_date"] = sig["signal_date"].strftime("%Y-%m-%d")

    start_time = time.time()
    rust_results_json = multibagger_core.run_bulk_backtest_rust(
        json.dumps(rust_signals),
        params_json,
        all_dates,
        all_opens,
        all_highs,
        all_lows,
        all_closes
    )
    rust_results = json.loads(rust_results_json)
    rust_time = time.time() - start_time
    print(f" -> Rust Time:   {rust_time:.4f} seconds")

    print("\n--------------------------------------------------")
    speedup = py_time / rust_time
    print(f"🚀 Overall Speedup: {speedup:.1f}x")
    print(f"   (Processed 2,000 signals across {len(all_symbols)} stocks)")
    print("--------------------------------------------------")

if __name__ == "__main__":
    run_benchmarks()

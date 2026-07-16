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
    
    # Sort by symbol then date to ensure contiguous blocks
    ohlcv_df = pl.read_parquet("../data/yf_adjusted/ohlcv_all.parquet").sort(["symbol", "date"])
    all_symbols = ohlcv_df["symbol"].unique(maintain_order=True).to_list()
    
    print(f"Loaded {ohlcv_df.height:,} OHLCV rows across {len(all_symbols)} symbols.")
    
    # Generate 5,000 synthetic signals for a massive benchmark
    num_signals = 5000
    print(f"Generating {num_signals:,} synthetic signals...")
    signals = []
    for i in range(num_signals):
        sym = all_symbols[i % len(all_symbols)]
        signals.append({
            "symbol": sym,
            "signal_date": date(2024, 1, 1),
            "entry_ref": 100.0,
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

    # Prepare data for Rust V2 (Contiguous Numpy Arrays)
    print("Pre-processing for Zero-Copy Rust V2...")
    all_dates = [d.strftime("%Y-%m-%d") for d in ohlcv_df["date"]]
    opens = ohlcv_df["open"].to_numpy().astype(np.float64)
    highs = ohlcv_df["high"].to_numpy().astype(np.float64)
    lows = ohlcv_df["low"].to_numpy().astype(np.float64)
    closes = ohlcv_df["close"].to_numpy().astype(np.float64)

    # Calculate indices for each symbol
    symbol_indices = {}
    current_idx = 0
    # Group by symbol to get counts efficiently
    counts = ohlcv_df.group_by("symbol", maintain_order=True).count()
    for row in counts.iter_rows():
        sym = row[0]
        count = row[1]
        symbol_indices[sym] = (current_idx, current_idx + count)
        current_idx += count

    # Pre-adjust signals to be realistic
    print("Finalizing signal data...")
    rust_signals = []
    for sig in signals:
        sym = sig["symbol"]
        if sym in symbol_indices:
            start, end = symbol_indices[sym]
            first_price = closes[start]
            sig["entry_ref"] = first_price * 1.05
            sig["stop"] = first_price * 0.95
            
            sig_copy = sig.copy()
            sig_copy["signal_date"] = sig_copy["signal_date"].strftime("%Y-%m-%d")
            rust_signals.append(sig_copy)

    # --- 1. Python Benchmark ---
    print(f"\n[1] Running Python Backtest ({num_signals:,} signals)...")
    # For Python, we'll use a slice of the dataframe to be as fast as possible
    start_time = time.time()
    py_results = []
    for sig in signals:
        sym = sig["symbol"]
        if sym in symbol_indices:
            start, end = symbol_indices[sym]
            # Convert to dicts only once per symbol if possible (optimization)
            # but for fairness we use the existing engine logic
            sym_ohlcv = ohlcv_df.slice(start, end - start).to_dicts()
            res = _simulate_signal(sig, sym_ohlcv, params)
            py_results.append(res)
    py_time = time.time() - start_time
    print(f" -> Python Time: {py_time:.4f} seconds")

    # --- 2. Rust V2 Benchmark ---
    print(f"\n[2] Running Rust V2 Zero-Copy Parallel Backtest ({num_signals:,} signals)...")
    start_time = time.time()
    rust_results_json = multibagger_core.run_bulk_backtest_rust_v2(
        json.dumps(rust_signals),
        params_json,
        all_dates,
        opens,
        highs,
        lows,
        closes,
        symbol_indices
    )
    rust_results = json.loads(rust_results_json)
    rust_time = time.time() - start_time
    print(f" -> Rust Time:   {rust_time:.4f} seconds")

    print("\n--------------------------------------------------")
    speedup = py_time / rust_time
    print(f"🚀 ZERO-COPY Speedup: {speedup:.1f}x")
    print(f"   (Processed {num_signals:,} signals across {len(all_symbols)} stocks)")
    print("--------------------------------------------------")

if __name__ == "__main__":
    run_benchmarks()

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
from sepa_scanner.backtest.engine import simulate_breakout_backtest

# Import our new Rust core
import multibagger_core

def run_benchmarks():
    print("--------------------------------------------------")
    db_path = os.path.abspath("../data/db/sepa.duckdb")
    ohlcv_path = os.path.abspath("../data/yf_adjusted/ohlcv_all.parquet")
    
    print(f"Loading DATA from {ohlcv_path}...")
    ohlcv_df = pl.read_parquet(ohlcv_path).sort(["symbol", "date"])
    
    # Prepare indices
    symbol_indices = {}
    current_idx = 0
    counts = ohlcv_df.group_by("symbol", maintain_order=True).len()
    for row in counts.iter_rows():
        sym, count = row[0], row[1]
        symbol_indices[sym] = (current_idx, current_idx + count)
        current_idx += count

    all_dates = [d.strftime("%Y-%m-%d") for d in ohlcv_df["date"]]
    opens = ohlcv_df["open"].to_numpy().astype(np.float64).tolist()
    highs = ohlcv_df["high"].to_numpy().astype(np.float64).tolist()
    lows = ohlcv_df["low"].to_numpy().astype(np.float64).tolist()
    closes = ohlcv_df["close"].to_numpy().astype(np.float64).tolist()

    params = BacktestParams(exit_mode=ExitMode.V1_FIXED)
    params_json = json.dumps({
        "pending_days": params.pending_days,
        "max_hold_days": params.max_hold_days,
        "target_r": params.target_r,
        "exit_mode": params.exit_mode.value,
        "trail_activation_r": params.trail_activation_r,
        "trail_pct": params.trail_pct,
        "dead_money_days": params.dead_money_days
    })

    # --- 1. Python Benchmark (Full simulate_breakout_backtest) ---
    print("\n[1] Running FULL Python Backtest (Loading from DF)...")
    # We need to load signals from DuckDB for Python too to be fair
    import duckdb
    conn = duckdb.connect(db_path)
    signals_df = conn.execute("SELECT * FROM signals WHERE signal_type = 'BREAKOUT'").pl()
    
    start_time = time.time()
    py_result = simulate_breakout_backtest(signals_df, ohlcv_df, params)
    py_time = time.time() - start_time
    print(f" -> Python Time: {py_time:.4f} seconds ({len(py_result.trades)} trades)")

    # --- 2. Rust Benchmark (End-to-End) ---
    print("\n[2] Running FULL Rust Backtest (Direct DuckDB + Parallel)...")
    start_time = time.time()
    rust_results_json = multibagger_core.run_backtest_from_db_rust(
        db_path,
        params_json,
        all_dates,
        opens, highs, lows, closes,
        symbol_indices
    )
    rust_results = json.loads(rust_results_json)
    rust_time = time.time() - start_time
    print(f" -> Rust Time:   {rust_time:.4f} seconds ({len(rust_results)} trades)")

    print("\n--------------------------------------------------")
    speedup = py_time / rust_time
    print(f"🚀 END-TO-END Speedup: {speedup:.1f}x")
    print("--------------------------------------------------")

if __name__ == "__main__":
    run_benchmarks()

import json
import time
import polars as pl
import numpy as np
import sys
import os
from datetime import date

# Add multibagger to path
sys.path.append(os.path.abspath("src"))

import multibagger_core

def run_super_optimization():
    print("--------------------------------------------------")
    print("🚀 STARTING SUPER-FAST RUST GRID SEARCH (2,600+ COMBOS)")
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
            "sector": "UNKNOWN",
            "signal_date": sig["signal_date"].strftime("%Y-%m-%d"),
            "entry_ref": float(sig["entry_ref"]),
            "stop": float(sig["stop"]),
            "pivot": float(sig["pivot"]) if sig.get("pivot") is not None else None,
            "rs_rank": float(sig.get("rs_rank", 80.0))
        })
    signals_json = json.dumps(rust_signals)

    # 3. Build the Massive Grid
    pending_range = [3, 5, 8, 10, 12]
    hold_range = [40, 60, 90, 120, 150, 180, 200]
    trail_r_range = [1.0, 1.5, 2.0, 2.5, 3.0]
    dead_money_range = [10, 15, 20, 25, 30]
    modes = ["v3_trail_pct", "minervini_phased", "advanced_sepa"]
    
    params_grid = []
    for m in modes:
        for p in pending_range:
            for h in hold_range:
                for t_r in trail_r_range:
                    for dm in dead_money_range:
                        params_grid.append({
                            "pending_days": p,
                            "max_hold_days": h,
                            "target_r": None,
                            "exit_mode": m,
                            "trail_activation_r": t_r,
                            "trail_pct": 0.15,
                            "dead_money_days": dm
                        })
    
    params_grid_json = json.dumps(params_grid)
    print(f"Testing {len(params_grid):,} combinations against {len(rust_signals):,} signals...")

    start_time = time.time()
    
    # 4. ONE SINGLE CALL TO RUST FOR THE ENTIRE GRID
    res_json = multibagger_core.run_optimization_grid_rust(
        signals_json,
        params_grid_json,
        all_dates,
        opens, highs, lows, closes,
        symbol_indices
    )
    
    grid_results = json.loads(res_json)
    duration = time.time() - start_time
    
    print(f"Grid search complete in {duration:.4f} seconds.")
    print(f"Throughput: {len(params_grid) * len(rust_signals) / duration:,.0f} sims/sec")

    # 5. Analyze Results
    # Result format: (p_idx, expectancy, total_r)
    best_res = max(grid_results, key=lambda x: x[1])
    p_idx, best_exp, total_r = best_res
    best_params = params_grid[p_idx]

    print("\n" + "="*50)
    print("🏆 ABSOLUTE OPTIMAL PARAMETERS FOUND")
    print("="*50)
    print(f" -> Exit Mode:        {best_params['exit_mode']}")
    print(f" -> Pending Days:     {best_params['pending_days']}")
    print(f" -> Max Hold Days:    {best_params['max_hold_days']}")
    print(f" -> Trail Activation: {best_params['trail_activation_r']}R")
    print(f" -> Dead Money Days:  {best_params['dead_money_days']}")
    print(f" -> Expectancy (Avg): {best_exp:+.4f}R")
    print("="*50)

if __name__ == "__main__":
    run_super_optimization()

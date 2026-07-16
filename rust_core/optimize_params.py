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
import matplotlib.pyplot as plt

def run_advanced_optimization():
    print("--------------------------------------------------")
    print("🚀 STARTING ADVANCED MULTI-VARIABLE OPTIMIZATION")
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
    conn = duckdb.connect(db_path)
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

    # 3. Define an Expanded Grid
    pending_range = [5, 8, 10]
    hold_range = [60, 90, 120, 150]
    trail_r_range = [1.5, 2.0, 2.5]
    dead_money_range = [10, 15, 20]
    
    # We will fix P=8 and T=1.5 for the Heatmap visualization of Hold vs Dead Money
    # to keep it 2D for the chart.
    
    results = []
    total_combos = len(pending_range) * len(hold_range) * len(trail_r_range) * len(dead_money_range)
    print(f"Testing {total_combos} combinations...")

    start_time = time.time()
    
    for p_days in pending_range:
        for h_days in hold_range:
            for t_r in trail_r_range:
                for dm_days in dead_money_range:
                    params = BacktestParams(
                        pending_days=p_days,
                        max_hold_days=h_days,
                        trail_activation_r=t_r,
                        dead_money_days=dm_days
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

                    res_json = multibagger_core.run_bulk_backtest_rust_v2(
                        signals_json, params_json, all_dates,
                        opens, highs, lows, closes, symbol_indices
                    )
                    
                    raw_trades = json.loads(res_json)
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
                    results.append({
                        "pending": p_days,
                        "hold": h_days,
                        "trail": t_r,
                        "dead_money": dm_days,
                        "expectancy": summary.expectancy or 0.0,
                        "cum_r": summary.cumulative_r
                    })

    print(f"Optimization complete in {time.time() - start_time:.2f}s")
    
    # Sort and Find Best
    best = max(results, key=lambda x: x["expectancy"])
    print(f"\n🏆 BEST: P={best['pending']}, H={best['hold']}, T={best['trail']}, DM={best['dead_money']} -> {best['expectancy']:.3f}R")

    # --- TASK 3: Visualization (Heatmap of Hold Days vs Dead Money) ---
    # We filter results where Pending=8 and Trail=1.5
    print("\n📊 Generating Heatmap: Hold Days vs Dead Money (Fixed P=8, T=1.5)...")
    
    viz_data = [r for r in results if r['pending'] == 8 and r['trail'] == 1.5]
    
    # Create Matrix
    x_labels = sorted(list(set(r['hold'] for r in viz_data)))
    y_labels = sorted(list(set(r['dead_money'] for r in viz_data)))
    
    matrix = np.zeros((len(y_labels), len(x_labels)))
    for r in viz_data:
        x_idx = x_labels.index(r['hold'])
        y_idx = y_labels.index(r['dead_money'])
        matrix[y_idx, x_idx] = r['expectancy']

    plt.figure(figsize=(10, 6))
    im = plt.imshow(matrix, cmap='RdYlGn', aspect='auto')
    plt.colorbar(im, label='Expectancy (R)')
    
    plt.xticks(np.arange(len(x_labels)), x_labels)
    plt.yticks(np.arange(len(y_labels)), y_labels)
    plt.xlabel('Max Hold Days')
    plt.ylabel('Dead Money Days')
    plt.title('Strategy Performance Heatmap (Expectancy)')
    
    # Annotate values
    for i in range(len(y_labels)):
        for j in range(len(x_labels)):
            plt.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", color="black")

    output_path = "rust_core/optimization_heatmap.png"
    plt.savefig(output_path)
    print(f"✅ Heatmap saved to {output_path}")

if __name__ == "__main__":
    run_advanced_optimization()

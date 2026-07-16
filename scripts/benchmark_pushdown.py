import time
import polars as pl
from pathlib import Path

def run_benchmark():
    flat_path = Path("data/yf_adjusted/ohlcv_all.parquet")
    if not flat_path.exists():
        print(f"Error: flat path {flat_path} does not exist.")
        return

    # Load unique symbols to pick a test set
    df_symbols = pl.read_parquet(flat_path, columns=["symbol"]).unique()
    symbols = df_symbols["symbol"].head(10).to_list()

    print(f"Benchmarking query times over {len(symbols)} test symbols...\n")

    # Method 1: Before (Full file load, then filter)
    t0 = time.perf_counter()
    for sym in symbols:
        df = pl.read_parquet(flat_path)
        _ = df.filter(pl.col("symbol") == sym)
    t_before = (time.perf_counter() - t0) / len(symbols)

    # Method 2: After (Lazy scan with predicate pushdown)
    t1 = time.perf_counter()
    for sym in symbols:
        df_lazy = pl.scan_parquet(flat_path)
        _ = df_lazy.filter(pl.col("symbol") == sym).collect()
    t_after = (time.perf_counter() - t1) / len(symbols)

    speedup = t_before / t_after
    improvement_pct = (1.0 - (t_after / t_before)) * 100

    print(f"Method 1 (Full Read & In-Memory Filter): {t_before:.4f}s / query")
    print(f"Method 2 (Lazy Scan & Predicate Pushdown): {t_after:.4f}s / query")
    print("-" * 55)
    print(f"Result: {speedup:.1f}x speedup ({improvement_pct:.1f}% reduction in query time)")

if __name__ == "__main__":
    run_benchmark()

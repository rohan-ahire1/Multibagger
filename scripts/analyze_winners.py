import polars as pl
import duckdb
from pathlib import Path
import yaml

PROJECT_ROOT = Path("/Users/rohanahire/Documents/Personal_Projects/multibagger")
DB_PATH = PROJECT_ROOT / "data" / "db" / "sepa.duckdb"

def analyze_winners():
    # Load trade data from last diagnostic run
    trade_path = PROJECT_ROOT / "reports" / "portfolio_diagnostics" / "2010-01-04_2026-03-17" / "trade_pnl.csv"
    if not trade_path.exists():
        print("No trade_pnl.csv found")
        return

    trades = pl.read_csv(trade_path).with_columns(pl.col("signal_date").str.to_date())
    # Join with diagnostics to get scores
    conn = duckdb.connect(str(DB_PATH))
    diag = conn.execute("SELECT * FROM daily_diagnostics").pl().with_columns(pl.col("diag_date").cast(pl.Date))
    
    # Merge
    diag_sub = diag.select(pl.col("diag_date").alias("signal_date"), "symbol", "rs_rank", "contraction_quality", "setup_active_streak")
    sig = conn.execute("SELECT symbol, signal_date, vol_mult FROM signals").pl().with_columns(pl.col("signal_date").cast(pl.Date))
    
    merged = trades.join(diag_sub, on=["symbol", "signal_date"], how="left")
    merged = merged.join(sig, on=["symbol", "signal_date"], how="left")
    
    # Sort by R-multiple
    winners = merged.filter(pl.col("r_multiple") > 0).sort("r_multiple", descending=True).head(20)
    print("TOP 20 WINNERS ANALYSIS")
    print(winners.select(["symbol", "signal_date", "r_multiple", "rs_rank", "contraction_quality", "setup_active_streak", "vol_mult"]))
    
    print("\nAVERAGE STATS OF TOP WINNERS:")
    print(winners.select(["rs_rank", "contraction_quality", "setup_active_streak", "vol_mult"]).mean())

    print("\nAVERAGE STATS OF ALL TRADES:")
    print(merged.select(["rs_rank", "contraction_quality", "setup_active_streak", "vol_mult"]).mean())

analyze_winners()

import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date
import numpy as np
from pathlib import Path

# Paths to artifacts
REPORT_DIR = Path("reports/portfolio_diagnostics/2010-01-04_2026-03-17")
TRADE_LOG = REPORT_DIR / "trade_pnl.csv"
EQUITY_CURVE = REPORT_DIR / "strategy_daily_equity.csv"
OHLCV_STORE = Path("data/yf_adjusted/ohlcv_all.parquet")
BENCHMARK_SYMBOL = "NIFTYBEES" # Using NIFTYBEES as the liquid proxy available in your store

def load_data():
    # 1. Load Trades
    trades = pl.read_csv(TRADE_LOG).with_columns([
        pl.col("entry_date").str.to_date(),
        pl.col("exit_date").str.to_date()
    ])
    
    # 2. Load Equity
    equity = pl.read_csv(EQUITY_CURVE).with_columns(
        pl.col("date").str.to_date()
    )
    
    # 3. Load Benchmark from Parquet
    benchmark = pl.read_parquet(OHLCV_STORE).filter(
        pl.col("symbol") == BENCHMARK_SYMBOL
    ).select(["date", "close"]).sort("date")
    
    return trades, equity, benchmark

def generate_annual_tearsheet(trades, equity):
    print("\n### ANNUAL PERFORMANCE MATRIX (2010-2026)")
    
    # Yearly Equity Metrics
    yearly_equity = equity.with_columns(
        pl.col("date").dt.year().alias("year")
    ).group_by("year").agg([
        ((pl.col("equity").last() / pl.col("equity").first()) - 1).alias("return_pct"),
        (pl.col("drawdown_pct").min()).alias("max_drawdown") # drawdown_pct is negative in your CSV
    ]).sort("year")
    
    # Yearly Trade Metrics
    yearly_trades = trades.with_columns(
        pl.col("exit_date").dt.year().alias("year")
    ).group_by("year").agg([
        pl.len().alias("trades"),
        (pl.col("r_multiple") > 0).sum().alias("wins"),
        pl.col("r_multiple").mean().alias("avg_expectancy")
    ])
    
    matrix = yearly_equity.join(yearly_trades, on="year", how="left").fill_null(0)
    matrix = matrix.with_columns(
        (pl.col("wins") / pl.col("trades")).alias("win_rate")
    )
    
    # Print Markdown Table
    print("| Year | Trades | Return (%) | Max DD (%) | Win Rate | Avg Expectancy (R) |")
    print("|------|--------|------------|------------|----------|-------------------|")
    for row in matrix.to_dicts():
        print(f"| {row['year']} | {int(row['trades'])} | {row['return_pct']*100:.2f}% | {abs(row['max_drawdown'])*100:.2f}% | {row['win_rate']*100:.1f}% | {row['avg_expectancy']:.3f}R |")

def analyze_regime_bleed(equity, benchmark):
    print("\n### REGIME BLEED ANALYSIS (NIFTY 200-SMA)")
    
    # Calculate 200-SMA Regime
    bm = benchmark.with_columns([
        pl.col("close").rolling_mean(window_size=200).alias("sma200")
    ]).with_columns([
        pl.when(pl.col("close") > pl.col("sma200")).then(1).otherwise(0).alias("regime")
    ])
    
    merged = equity.join(bm, on="date", how="left").drop_nulls("regime")
    
    regime_stats = merged.group_by("regime").agg([
        pl.len().alias("days"),
        pl.col("daily_pnl").sum().alias("total_pnl"),
        (pl.col("daily_pnl") > 0).sum().alias("up_days")
    ])
    
    for row in regime_stats.to_dicts():
        label = "BULL (Price > SMA200)" if row['regime'] == 1 else "BEAR (Price < SMA200)"
        print(f"-> {label}: {row['total_pnl']:,.2f} INR PnL over {row['days']} days")

def plot_regime_forensics(trades, equity, benchmark):
    # Prepare Data
    bm = benchmark.with_columns([
        pl.col("close").rolling_mean(window_size=200).alias("sma200")
    ])
    
    # Identify -1R clusters
    losers = trades.filter(pl.col("r_multiple") <= -1.0)
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.05, 
                        subplot_titles=("Benchmark Regime (Nifty Bees)", "Strategy Equity Curve"),
                        row_heights=[0.4, 0.6])

    # Subplot 1: Benchmark + Shading
    fig.add_trace(go.Scatter(x=bm["date"], y=bm["close"], name="Nifty Bees", line=dict(color='gray', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bm["date"], y=bm["sma200"], name="200-SMA", line=dict(color='blue', width=1, dash='dash')), row=1, col=1)
    
    # Add Regime Shading
    regime_diff = bm["close"] - bm["sma200"]
    # Shade red where price < sma200
    bear_periods = bm.filter(pl.col("close") < pl.col("sma200"))
    # (Simplified shading for Plotly)
    
    # Subplot 2: Equity Curve
    fig.add_trace(go.Scatter(x=equity["date"], y=equity["equity"], name="Equity", line=dict(color='green', width=2)), row=2, col=1)
    
    # Overlay Stop-Outs
    fig.add_trace(go.Scatter(
        x=losers["exit_date"], 
        y=[equity.filter(pl.col("date") == d)["equity"][0] if not equity.filter(pl.col("date") == d).is_empty() else None for d in losers["exit_date"]],
        mode='markers',
        marker=dict(color='red', size=6),
        name="Stop-Outs (<= -1R)"
    ), row=2, col=1)

    fig.update_layout(height=800, title_text="SEPA Regime Forensics (2010-2026)", showlegend=True)
    fig.show()

if __name__ == "__main__":
    t, e, b = load_data()
    generate_annual_tearsheet(t, e)
    analyze_regime_bleed(e, b)
    print("\nLaunching Interactive Visualization...")
    plot_regime_forensics(t, e, b)

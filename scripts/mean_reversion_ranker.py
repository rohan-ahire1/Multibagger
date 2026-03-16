"""
Mean Reversion Stock Ranker
===========================
Computes statistical measures of mean reversion tendency for each stock
and ranks them. Stocks with strong mean reversion characteristics are
ideal candidates for pullback strategies (Williams %R, RSI(2), etc.)

Metrics computed:
  1. Hurst Exponent (< 0.5 = mean reverting, > 0.5 = trending)
  2. Autocorrelation of daily returns at lag 1 (negative = mean reverting)
  3. Variance Ratio (< 1.0 = mean reverting)
  4. Bounce rate (% of 5%+ pullbacks above 200-DMA that recover within 10 bars)

Usage:
  uv run python scripts/mean_reversion_ranker.py --data data/yf_adjusted/ohlcv_all.parquet
  uv run python scripts/mean_reversion_ranker.py --data data/yf_adjusted/ohlcv_all.parquet --universe all --top 50
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

NIFTY_100 = sorted([
    "ADANIENT", "ADANIGREEN", "ADANIPORTS", "AMBUJACEM", "APOLLOHOSP",
    "ASIANPAINT", "ATGL", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV",
    "BAJFINANCE", "BANKBARODA", "BEL", "BHARATFORG", "BHARTIARTL",
    "BOSCHLTD", "BPCL", "BRITANNIA", "CANBK", "CHOLAFIN",
    "CIPLA", "COALINDIA", "COLPAL", "DLF", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GAIL", "GODREJCP", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDEA",
    "INDHOTEL", "INDUSINDBK", "INDUSTOWER", "INFY", "IOC",
    "ITC", "JINDALSTEL", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
    "LICI", "LT", "LTIM", "LTTS", "M&M",
    "MARICO", "MARUTI", "MOTHERSON", "MUTHOOTFIN", "NESTLEIND",
    "NHPC", "NTPC", "ONGC", "PERSISTENT", "PIDILITIND",
    "PNB", "POLICYBZR", "POWERGRID", "RECLTD", "RELIANCE",
    "SBICARD", "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN",
    "SIEMENS", "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATAPOWER",
    "TATASTEEL", "TCS", "TECHM", "TITAN", "TORNTPHARM",
    "TRENT", "ULTRACEMCO", "UNIONBANK", "UNITDSPR", "VEDL",
    "WIPRO", "ZOMATO", "ZYDUSLIFE",
])


# ─── Statistical Functions ───────────────────────────────────────────


def compute_hurst(prices: np.ndarray, max_lag: int = 100) -> float:
    """Rescaled Range (R/S) Hurst exponent.

    H < 0.5 -> mean reverting, H = 0.5 -> random walk, H > 0.5 -> trending.
    """
    n = len(prices)
    if n < 50:
        return np.nan

    returns = np.diff(np.log(prices))
    lags: list[int] = []
    rs_values: list[float] = []

    for lag in range(10, min(max_lag, n // 4)):
        n_chunks = len(returns) // lag
        if n_chunks < 2:
            continue

        rs_list: list[float] = []
        for i in range(n_chunks):
            chunk = returns[i * lag : (i + 1) * lag]
            deviations = np.cumsum(chunk - np.mean(chunk))
            r = np.max(deviations) - np.min(deviations)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)

        if rs_list:
            lags.append(lag)
            rs_values.append(np.mean(rs_list))

    if len(lags) < 5:
        return np.nan

    slope, _ = np.polyfit(np.log(lags), np.log(rs_values), 1)
    return float(slope)


def compute_autocorrelation(returns: np.ndarray, lag: int = 1) -> float:
    """Autocorrelation at given lag.  Negative = mean reverting."""
    n = len(returns)
    if n < lag + 10:
        return np.nan

    mean_r = np.mean(returns)
    denom = np.sum((returns - mean_r) ** 2)
    if denom == 0:
        return np.nan
    numer = np.sum((returns[lag:] - mean_r) * (returns[:-lag] - mean_r))
    return float(numer / denom)


def compute_variance_ratio(returns: np.ndarray, period: int = 5) -> float:
    """VR(q) = Var(q-day returns) / (q * Var(1-day returns)).  < 1 = mean reverting."""
    n = len(returns)
    if n < period * 10:
        return np.nan

    var_1 = np.var(returns, ddof=1)
    if var_1 == 0:
        return np.nan

    q_returns = np.array([
        np.sum(returns[i : i + period])
        for i in range(0, n - period + 1, period)
    ])
    return float(np.var(q_returns, ddof=1) / (period * var_1))


def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) >= period:
        cs = np.cumsum(arr)
        out[period - 1 :] = (cs[period - 1 :] - np.concatenate([[0], cs[:-period]])) / period
    return out


def compute_bounce_rate(closes: np.ndarray, sma_200: np.ndarray) -> float:
    """% of 5-20% pullbacks (above 200-DMA) that recover 2%+ within 10 bars."""
    n = len(closes)
    if n < 260:
        return np.nan

    pullbacks = 0
    recoveries = 0

    for i in range(252, n - 10):
        if closes[i] < sma_200[i] or np.isnan(sma_200[i]):
            continue
        rolling_high = np.max(closes[max(0, i - 252) : i + 1])
        dd = (rolling_high - closes[i]) / rolling_high
        if dd < 0.05 or dd > 0.20:
            continue

        pullbacks += 1
        entry = closes[i]
        for j in range(i + 1, min(i + 11, n)):
            if closes[j] > entry * 1.02:
                recoveries += 1
                break

    if pullbacks < 5:
        return np.nan
    return float(recoveries / pullbacks)


# ─── Worker ──────────────────────────────────────────────────────────


def _analyze_stock(sym: str, closes: np.ndarray) -> dict | None:
    """Compute all MR metrics for a single stock. Runs in a worker process."""
    returns = np.diff(np.log(closes))
    sma_200 = _sma(closes, 200)

    hurst = compute_hurst(closes)
    ac1 = compute_autocorrelation(returns, lag=1)
    ac3 = compute_autocorrelation(returns, lag=3)
    ac5 = compute_autocorrelation(returns, lag=5)
    vr5 = compute_variance_ratio(returns, period=5)
    vr10 = compute_variance_ratio(returns, period=10)
    bounce = compute_bounce_rate(closes, sma_200)
    avg_vol = float(np.std(returns) * 100)

    def _r(v: float, d: int = 3) -> float | None:
        return round(v, d) if v is not None and not np.isnan(v) else None

    return {
        "symbol": sym,
        "bars": len(closes),
        "hurst": _r(hurst),
        "autocorr_1": _r(ac1, 4),
        "autocorr_3": _r(ac3, 4),
        "autocorr_5": _r(ac5, 4),
        "var_ratio_5": _r(vr5),
        "var_ratio_10": _r(vr10),
        "bounce_rate": _r(bounce),
        "daily_vol_pct": _r(avg_vol, 2),
    }


# ─── Main ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank stocks by mean reversion tendency")
    parser.add_argument(
        "--data",
        default="data/yf_adjusted/ohlcv_all.parquet",
        help="Path to OHLCV parquet file",
    )
    parser.add_argument(
        "--universe",
        default="all",
        choices=["nifty100", "all"],
        help="Stock universe (default: all)",
    )
    parser.add_argument("--min-bars", type=int, default=500, help="Minimum bars of history")
    parser.add_argument("--top", type=int, default=50, help="Show top N stocks")
    parser.add_argument("--output", default=None, help="Save results to CSV path")
    args = parser.parse_args()

    data_path = Path(args.data)
    print(f"\n  Loading {data_path}...")
    df = pl.read_parquet(str(data_path))

    if args.universe == "nifty100":
        df = df.filter(pl.col("symbol").is_in(NIFTY_100))
        print(f"  Filtered to Nifty 100 ({df['symbol'].n_unique()} found)")

    symbols = sorted(df["symbol"].unique().to_list())
    n_workers = min(os.cpu_count() or 4, 8)
    print(f"  Analyzing {len(symbols)} stocks (min {args.min_bars} bars, {n_workers} workers)...\n")

    tasks: list[tuple[str, np.ndarray]] = []
    for sym in symbols:
        stock = df.filter(pl.col("symbol") == sym).sort("date")
        closes = stock["close"].to_numpy().astype(float)
        if len(closes) < args.min_bars:
            continue
        if np.any(closes <= 0) or np.any(np.isnan(closes)):
            continue
        tasks.append((sym, closes))

    total = len(tasks)
    print(f"  {total} stocks pass min-bars filter. Computing...\n")

    done = 0
    results: list[dict] = []

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_analyze_stock, sym, closes): sym for sym, closes in tasks}
        for future in futures:
            row = future.result()
            if row is not None:
                results.append(row)
            done += 1
            if done % 100 == 0 or done == total:
                pct = done / total * 100
                sys.stdout.write(f"\r  Progress: {done}/{total} ({pct:.0f}%)")
                sys.stdout.flush()

    print("\n")

    res_df = pl.DataFrame(results)
    worst = float(len(res_df) + 1)

    res_df = res_df.with_columns(
        pl.col("hurst").rank("ordinal").fill_null(worst).alias("hurst_rank"),
        pl.col("autocorr_1").rank("ordinal").fill_null(worst).alias("autocorr_rank"),
        pl.col("var_ratio_5").rank("ordinal").fill_null(worst).alias("vr_rank"),
        pl.col("bounce_rate").rank("ordinal", descending=True).fill_null(worst).alias("bounce_rank"),
    ).with_columns(
        (
            (pl.col("hurst_rank") + pl.col("autocorr_rank") + pl.col("vr_rank") + pl.col("bounce_rank")) / 4.0
        ).alias("mr_score_rank")
    ).sort("mr_score_rank")

    n_total = len(res_df)

    print("=" * 105)
    print("  MEAN REVERSION STOCK RANKING")
    print("=" * 105)
    print(
        f"  {'Rank':<5} {'Symbol':<14} {'Hurst':<8} {'AC(1)':<9} {'AC(5)':<9} "
        f"{'VR(5)':<8} {'VR(10)':<8} {'Bounce%':<9} {'Vol%':<7} {'Score':<7}"
    )
    print("-" * 105)

    for i, row in enumerate(res_df.head(args.top).iter_rows(named=True)):
        h = f"{row['hurst']:.3f}" if row["hurst"] is not None else "  -  "
        a1 = f"{row['autocorr_1']:+.4f}" if row["autocorr_1"] is not None else "   -   "
        a5 = f"{row['autocorr_5']:+.4f}" if row["autocorr_5"] is not None else "   -   "
        v5 = f"{row['var_ratio_5']:.3f}" if row["var_ratio_5"] is not None else "  -  "
        v10 = f"{row['var_ratio_10']:.3f}" if row["var_ratio_10"] is not None else "  -  "
        br = f"{row['bounce_rate'] * 100:.1f}%" if row["bounce_rate"] is not None else "  -  "
        vol = f"{row['daily_vol_pct']:.1f}%"
        sc = f"{row['mr_score_rank']:.1f}" if row["mr_score_rank"] is not None else "  -  "

        hf = " *" if row["hurst"] is not None and row["hurst"] < 0.45 else ""
        af = " *" if row["autocorr_1"] is not None and row["autocorr_1"] < -0.05 else ""
        vf = " *" if row["var_ratio_5"] is not None and row["var_ratio_5"] < 0.90 else ""
        bf = " *" if row["bounce_rate"] is not None and row["bounce_rate"] > 0.60 else ""

        print(
            f"  {i+1:<5} {row['symbol']:<14} {h}{hf:<3} {a1}{af:<3} {a5:<9} "
            f"{v5}{vf:<3} {v10:<8} {br}{bf:<3} {vol:<7} {sc}"
        )

    print("-" * 105)
    print()
    print("  Legend:")
    print("    Hurst < 0.45  = strong mean reversion (*)       VR(5) < 0.90 = confirms MR (*)")
    print("    AC(1) < -0.05 = negative autocorrelation (*)    Bounce > 60% = recovers from pullbacks (*)")
    print()

    print(f"  BOTTOM 10 — most trending (worst for mean reversion):")
    print(f"  {'Rank':<5} {'Symbol':<14} {'Hurst':<8} {'AC(1)':<9} {'VR(5)':<8} {'Bounce%':<9}")
    print("-" * 60)

    for i, row in enumerate(res_df.tail(10).sort("mr_score_rank", descending=True).iter_rows(named=True)):
        h = f"{row['hurst']:.3f}" if row["hurst"] is not None else "  -  "
        a1 = f"{row['autocorr_1']:+.4f}" if row["autocorr_1"] is not None else "   -   "
        v5 = f"{row['var_ratio_5']:.3f}" if row["var_ratio_5"] is not None else "  -  "
        br = f"{row['bounce_rate'] * 100:.1f}%" if row["bounce_rate"] is not None else "  -  "
        print(f"  {n_total - 9 + i:<5} {row['symbol']:<14} {h:<8} {a1:<9} {v5:<8} {br}")

    print()

    out_path = args.output or "mean_reversion_rankings.csv"
    res_df.write_csv(out_path)
    print(f"  Results saved to {out_path}")


if __name__ == "__main__":
    main()

"""
Mean Reversion Swing Trading Backtester
========================================
Tests mean reversion strategies on NSE Nifty 100 stocks with a SEPA Stage 2
pre-filter.  Only takes pullback entries on stocks currently in a confirmed
Stage 2 uptrend (9-condition Minervini trend template) with RS ≥ 70th
percentile vs NIFTYBEES.

Strategies implemented:
  1. RSI(2) Pullback    — Buy RSI(2) < 10, sell RSI(2) > 70
  2. Williams %R         — Buy W%R < -90, sell close > yesterday's high
  3. Consecutive Down    — Buy after N red candles, sell first green candle
  4. Double Seven        — Buy 7-day low, sell 7-day high
  5. IBS (Internal Bar)  — Buy IBS < 0.2, sell IBS > 0.8

Entry filter (default):
  Stage 2 = all 9 trend template conditions pass (SMA50 > SMA150 > SMA200
  rising, close above all MAs, within 25% of 52w high, 30%+ above 52w low)
  AND cross-sectional RS percentile ≥ 70 vs NIFTYBEES benchmark.

Usage:
  # Single stock (Stage 2 + RS filter on by default)
  python mean_reversion_bt.py --symbol RELIANCE

  # Portfolio across Nifty 100
  python mean_reversion_bt.py --strategy rsi2

  # Compare with/without Stage 2 filter
  python mean_reversion_bt.py --symbol HDFCBANK --no-stage2

  # All symbols, not just Nifty 100
  python mean_reversion_bt.py --all-symbols --no-stage2
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

# ─── Nifty 100 Constituents ──────────────────────────────────────────

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


# ─── Indicator Functions ──────────────────────────────────────────────


def compute_rsi(close: np.ndarray, period: int = 2) -> np.ndarray:
    """Wilder's RSI."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)

    if len(close) > period:
        avg_gain[period] = np.mean(gain[1 : period + 1])
        avg_loss[period] = np.mean(loss[1 : period + 1])

        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(close), where=avg_loss != 0)
    rsi = 100 - 100 / (1 + rs)
    rsi[:period] = 50
    return rsi


def compute_williams_r(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 5
) -> np.ndarray:
    """Williams %R — ranges from -100 (oversold) to 0 (overbought)."""
    wr = np.full_like(close, -50.0)
    for i in range(period - 1, len(close)):
        hh = np.max(high[i - period + 1 : i + 1])
        ll = np.min(low[i - period + 1 : i + 1])
        if hh != ll:
            wr[i] = -100 * (hh - close[i]) / (hh - ll)
    return wr


def compute_sma(close: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    sma = np.full_like(close, np.nan)
    if len(close) >= period:
        cumsum = np.cumsum(close)
        sma[period - 1 :] = (
            cumsum[period - 1 :] - np.concatenate([[0], cumsum[:-period]])
        ) / period
    return sma


def compute_ibs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Internal Bar Strength: (close - low) / (high - low). Ranges 0 to 1."""
    denom = high - low
    return np.divide(
        close - low, denom, out=np.full_like(close, 0.5), where=denom != 0
    )


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's ATR."""
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = np.zeros_like(close)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, len(close)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def rolling_min(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.min(arr[i - period + 1 : i + 1])
    return out


def rolling_max(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.max(arr[i - period + 1 : i + 1])
    return out


# ─── Stage 2 (SEPA Trend Template) ──────────────────────────────────


def compute_stage2_mask(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sma200_slope_lookback: int = 66,
    min_above_52w_low_pct: float = 0.30,
    max_below_52w_high_pct: float = 0.25,
    lookback_52w: int = 252,
) -> np.ndarray:
    """Full 9-condition Minervini trend template, bar-by-bar.

    Returns a boolean array: True where all conditions pass.
    """
    n = len(close)
    sma50 = compute_sma(close, 50)
    sma150 = compute_sma(close, 150)
    sma200 = compute_sma(close, 200)

    high52 = rolling_max(high, lookback_52w)
    low52 = rolling_min(low, lookback_52w)

    mask = np.zeros(n, dtype=bool)
    warmup = max(lookback_52w, 200 + sma200_slope_lookback)

    for i in range(warmup, n):
        if np.isnan(sma50[i]) or np.isnan(sma150[i]) or np.isnan(sma200[i]):
            continue
        sma200_lagged = sma200[i - sma200_slope_lookback] if i >= sma200_slope_lookback else np.nan
        if np.isnan(sma200_lagged):
            continue

        mask[i] = (
            close[i] > sma50[i]
            and close[i] > sma150[i]
            and close[i] > sma200[i]
            and sma50[i] > sma150[i]
            and sma50[i] > sma200[i]
            and sma150[i] > sma200[i]
            and sma200[i] > sma200_lagged
            and close[i] >= low52[i] * (1.0 + min_above_52w_low_pct)
            and close[i] >= high52[i] * (1.0 - max_below_52w_high_pct)
        )

    return mask


# ─── Cross-Sectional RS Percentile Ranking ───────────────────────────

BENCHMARK_SYMBOL = "NIFTYBEES"
RS_LOOKBACKS = [63, 126, 189, 252]
RS_WEIGHTS = [0.4, 0.2, 0.2, 0.2]
RS_SKIP_RECENT = 21
RS_MIN_PERCENTILE = 70.0


def precompute_rs_percentiles(df: pl.DataFrame) -> pl.DataFrame:
    """Compute per-bar RS percentile rank for every (symbol, date).

    Returns DataFrame with columns [date, symbol, rs_pctile].
    Requires NIFTYBEES rows in df for benchmark returns.
    """
    sorted_df = df.sort(["symbol", "date"])

    ret_cols: list[pl.Expr] = []
    bm_cols: list[pl.Expr] = []
    rs_terms: list[pl.Expr] = []

    for lb, w in zip(RS_LOOKBACKS, RS_WEIGHTS):
        if RS_SKIP_RECENT > 0:
            ret = (
                (pl.col("close").shift(RS_SKIP_RECENT) / pl.col("close").shift(lb + RS_SKIP_RECENT)) - 1.0
            ).over("symbol").alias(f"ret_{lb}")
        else:
            ret = (
                (pl.col("close") / pl.col("close").shift(lb)) - 1.0
            ).over("symbol").alias(f"ret_{lb}")
        ret_cols.append(ret)
        bm_cols.append(pl.col(f"ret_{lb}").alias(f"bm_ret_{lb}"))
        rs_terms.append(
            pl.lit(w) * ((1.0 + pl.col(f"ret_{lb}")) / (1.0 + pl.col(f"bm_ret_{lb}")) - 1.0)
        )

    with_returns = sorted_df.with_columns(ret_cols)
    benchmark = (
        with_returns.filter(pl.col("symbol") == BENCHMARK_SYMBOL)
        .select("date", *bm_cols)
    )

    scored = (
        with_returns.join(benchmark, on="date", how="left")
        .with_columns(pl.sum_horizontal(rs_terms).alias("rs_score"))
        .filter(pl.col("symbol") != BENCHMARK_SYMBOL)
    )

    return scored.with_columns(
        (
            pl.col("rs_score").rank("average").over("date")
            / pl.col("rs_score").count().over("date")
            * 100.0
        ).alias("rs_pctile")
    ).select("date", "symbol", "rs_pctile")


# ─── Strategy Definitions ────────────────────────────────────────────


@dataclass
class Trade:
    entry_date: date
    entry_price: float
    exit_date: Optional[date] = None
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    exit_reason: str = ""


@dataclass
class StrategyResult:
    name: str
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    total_return: float = 0.0
    avg_return: float = 0.0
    avg_hold_days: float = 0.0
    max_drawdown: float = 0.0
    exposure_pct: float = 0.0
    cagr: float = 0.0
    sharpe: float = 0.0


def _make_trade(
    dates: np.ndarray,
    close: np.ndarray,
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    reason: str,
) -> Trade:
    return Trade(
        entry_date=dates[entry_idx],
        entry_price=entry_price,
        exit_date=dates[exit_idx],
        exit_price=close[exit_idx],
        pnl_pct=(close[exit_idx] - entry_price) / entry_price * 100,
        hold_days=exit_idx - entry_idx,
        exit_reason=reason,
    )


def run_strategy(
    dates: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    strategy: str,
    params: dict,
    *,
    entry_filter: np.ndarray | None = None,
) -> list[Trade]:
    """
    Run a mean reversion strategy and return list of trades.
    Entry on CLOSE of signal day. Exit on CLOSE of exit signal day.

    entry_filter: boolean array — when provided, entries are only taken on
    bars where entry_filter[i] is True (Stage 2 + RS gate).  Falls back
    to close > SMA200 when None.
    """
    n = len(close)
    if n < 250:
        return []

    if entry_filter is None:
        sma200 = compute_sma(close, 200)
        entry_filter = close > sma200

    trades: list[Trade] = []
    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    max_hold = params.get("max_hold", 15)
    warmup = 252

    if strategy == "rsi2":
        rsi = compute_rsi(close, period=2)
        sma3 = compute_sma(close, 3)
        atr = compute_atr(high, low, close, period=14)
        entry_thresh = params.get("entry_threshold", 25)
        exit_thresh = params.get("exit_threshold", 70)
        exit_mode = params.get("exit_mode", "sma3")
        stop_atr_mult = params.get("stop_atr_mult", 3.0)
        stop_price = 0.0

        for i in range(warmup, n):
            if not in_trade:
                if rsi[i] < entry_thresh and entry_filter[i]:
                    in_trade, entry_price, entry_idx = True, close[i], i
                    stop_price = entry_price - stop_atr_mult * atr[i]
            else:
                hold = i - entry_idx
                stopped = close[i] <= stop_price
                if exit_mode == "sma3":
                    signal_exit = close[i] > sma3[i]
                    exit_label = "sma3_exit"
                else:
                    signal_exit = rsi[i] > exit_thresh
                    exit_label = "rsi_exit"
                if stopped or signal_exit or hold >= max_hold:
                    if stopped:
                        reason = "stopped"
                    elif signal_exit:
                        reason = exit_label
                    else:
                        reason = "time_exit"
                    trades.append(_make_trade(dates, close, entry_idx, i, entry_price, reason))
                    in_trade = False

    elif strategy == "williams_r":
        wr = compute_williams_r(high, low, close, period=params.get("wr_period", 5))
        entry_thresh = params.get("entry_threshold", -90)

        for i in range(warmup, n):
            if not in_trade:
                if wr[i] < entry_thresh and entry_filter[i]:
                    in_trade, entry_price, entry_idx = True, close[i], i
            else:
                hold = i - entry_idx
                if close[i] > high[i - 1] or hold >= max_hold:
                    reason = "prev_high_exit" if close[i] > high[i - 1] else "time_exit"
                    trades.append(_make_trade(dates, close, entry_idx, i, entry_price, reason))
                    in_trade = False

    elif strategy == "consecutive_down":
        n_down = params.get("n_down", 3)

        for i in range(warmup, n):
            if not in_trade:
                if i >= n_down and entry_filter[i]:
                    all_down = all(
                        close[i - j] < close[i - j - 1] for j in range(n_down)
                    )
                    if all_down:
                        in_trade, entry_price, entry_idx = True, close[i], i
            else:
                hold = i - entry_idx
                if close[i] > close[i - 1] or hold >= max_hold:
                    reason = "up_close_exit" if close[i] > close[i - 1] else "time_exit"
                    trades.append(_make_trade(dates, close, entry_idx, i, entry_price, reason))
                    in_trade = False

    elif strategy == "double_seven":
        low7 = rolling_min(close, 7)
        high7 = rolling_max(close, 7)

        for i in range(warmup, n):
            if not in_trade:
                if close[i] <= low7[i] and entry_filter[i]:
                    in_trade, entry_price, entry_idx = True, close[i], i
            else:
                hold = i - entry_idx
                if close[i] >= high7[i] or hold >= max_hold:
                    reason = "7d_high_exit" if close[i] >= high7[i] else "time_exit"
                    trades.append(_make_trade(dates, close, entry_idx, i, entry_price, reason))
                    in_trade = False

    elif strategy == "ibs":
        ibs = compute_ibs(high, low, close)
        entry_thresh = params.get("entry_threshold", 0.2)
        exit_thresh = params.get("exit_threshold", 0.8)

        for i in range(warmup, n):
            if not in_trade:
                if ibs[i] < entry_thresh and entry_filter[i]:
                    in_trade, entry_price, entry_idx = True, close[i], i
            else:
                hold = i - entry_idx
                if ibs[i] > exit_thresh or hold >= max_hold:
                    reason = "ibs_exit" if ibs[i] > exit_thresh else "time_exit"
                    trades.append(_make_trade(dates, close, entry_idx, i, entry_price, reason))
                    in_trade = False

    return trades


# ─── Analytics ────────────────────────────────────────────────────────


def compute_metrics(
    trades: list[Trade], name: str, symbol: str, total_bars: int, years: float
) -> StrategyResult:
    r = StrategyResult(name=name, symbol=symbol)

    if not trades:
        return r

    r.trades = trades
    r.total_trades = len(trades)

    returns = [t.pnl_pct for t in trades]
    wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in trades if t.pnl_pct <= 0]
    holds = [t.hold_days for t in trades]

    r.winners = len(wins)
    r.losers = len(losses)
    r.win_rate = r.winners / r.total_trades * 100 if r.total_trades > 0 else 0
    r.avg_win = float(np.mean(wins)) if wins else 0
    r.avg_loss = float(np.mean(losses)) if losses else 0
    r.avg_return = float(np.mean(returns))
    r.avg_hold_days = float(np.mean(holds)) if holds else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001
    r.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    days_in_market = sum(holds)

    for t in trades:
        equity *= 1 + t.pnl_pct / 100
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    r.total_return = (equity - 1) * 100
    r.max_drawdown = max_dd * 100
    r.exposure_pct = days_in_market / total_bars * 100 if total_bars > 0 else 0

    if years > 0 and equity > 0:
        r.cagr = (equity ** (1 / years) - 1) * 100

    if len(returns) > 1:
        avg_trades_per_year = r.total_trades / years if years > 0 else 50
        r.sharpe = float(
            (np.mean(returns) / np.std(returns)) * np.sqrt(avg_trades_per_year)
        )
    else:
        r.sharpe = 0

    return r


# ─── Display ──────────────────────────────────────────────────────────


def print_result(r: StrategyResult) -> None:
    print(f"\n{'=' * 65}")
    print(f"  {r.name}  |  {r.symbol}  |  {r.total_trades} trades")
    print(f"{'=' * 65}")
    if r.total_trades == 0:
        print("  No trades generated.")
        return

    print(f"  Win rate:       {r.win_rate:6.1f}%    ({r.winners}W / {r.losers}L)")
    print(f"  Avg win:        {r.avg_win:+6.2f}%")
    print(f"  Avg loss:       {r.avg_loss:+6.2f}%")
    print(f"  Avg return:     {r.avg_return:+6.2f}%  per trade")
    print(f"  Profit factor:  {r.profit_factor:6.2f}")
    print(f"  Avg hold:       {r.avg_hold_days:6.1f} days")
    print(f"  Exposure:       {r.exposure_pct:6.1f}%  of time in market")
    print(f"  {'─' * 45}")
    print(f"  Total return:   {r.total_return:+8.1f}%  (compounded)")
    print(f"  CAGR:           {r.cagr:+8.2f}%")
    print(f"  Max drawdown:   {r.max_drawdown:8.1f}%")
    print(f"  Sharpe:         {r.sharpe:8.2f}")


def print_comparison(results: list[StrategyResult]) -> None:
    print(f"\n{'=' * 95}")
    print(f"  STRATEGY COMPARISON  |  {results[0].symbol}")
    print(f"{'=' * 95}")

    header = (
        f"  {'Strategy':<22} {'Trades':>6} {'Win%':>6} {'AvgRet':>7} "
        f"{'PF':>6} {'CAGR':>7} {'MaxDD':>7} {'Sharpe':>7} {'Hold':>5}"
    )
    print(header)
    print(f"  {'-' * 88}")

    for r in sorted(
        results, key=lambda x: -x.profit_factor if x.total_trades > 0 else -999
    ):
        if r.total_trades == 0:
            print(f"  {r.name:<22} {'—':>6}")
            continue
        print(
            f"  {r.name:<22} {r.total_trades:>6} {r.win_rate:>5.1f}% {r.avg_return:>+6.2f}% "
            f"{r.profit_factor:>5.2f} {r.cagr:>+6.2f}% {r.max_drawdown:>6.1f}% "
            f"{r.sharpe:>6.2f} {r.avg_hold_days:>4.1f}d"
        )

    print()


def print_recent_trades(trades: list[Trade], n: int = 10) -> None:
    if not trades:
        return
    print(f"\n  Last {min(n, len(trades))} trades:")
    print(
        f"  {'Entry Date':<12} {'Exit Date':<12} {'Entry':>8} {'Exit':>8} "
        f"{'P&L':>7} {'Days':>5} {'Reason':<16}"
    )
    print(f"  {'-' * 72}")
    for t in trades[-n:]:
        print(
            f"  {t.entry_date!s:<12} {t.exit_date!s:<12} "
            f"{t.entry_price:>8.1f} {t.exit_price:>8.1f} "
            f"{t.pnl_pct:>+6.2f}% {t.hold_days:>4}d  {t.exit_reason:<16}"
        )


# ─── Portfolio-Level Backtest ─────────────────────────────────────────


def _build_entry_filter(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    rs_arr: np.ndarray | None,
    use_stage2: bool,
) -> np.ndarray | None:
    """Combine Stage 2 mask and RS gate into a single boolean entry filter."""
    if not use_stage2:
        return None  # fall back to simple SMA200 inside run_strategy

    stage2 = compute_stage2_mask(close, high, low)
    if rs_arr is not None:
        return stage2 & (rs_arr >= RS_MIN_PERCENTILE)
    return stage2


def run_portfolio_backtest(
    df: pl.DataFrame,
    strategy: str,
    params: dict,
    *,
    rs_df: pl.DataFrame | None = None,
    use_stage2: bool = True,
    top_n: int = 0,
    min_bars: int = 500,
) -> list[StrategyResult]:
    symbols = df["symbol"].unique().sort().to_list()

    bar_counts = df.group_by("symbol").agg(pl.len().alias("bars"))
    valid = bar_counts.filter(pl.col("bars") >= min_bars)["symbol"].to_list()
    symbols = [s for s in symbols if s in valid]

    if top_n > 0:
        symbols = symbols[:top_n]

    filter_label = "Stage 2 + RS" if use_stage2 else "SMA200"
    print(f"\n  Running '{strategy}' on {len(symbols)} stocks  [filter: {filter_label}]...")
    results: list[StrategyResult] = []

    for sym in symbols:
        stock = df.filter(pl.col("symbol") == sym).sort("date")

        dates = stock["date"].to_numpy()
        open_ = stock["open"].to_numpy().astype(float)
        high = stock["high"].to_numpy().astype(float)
        low = stock["low"].to_numpy().astype(float)
        close = stock["close"].to_numpy().astype(float)
        volume = stock["volume"].to_numpy().astype(float)

        rs_arr: np.ndarray | None = None
        if rs_df is not None and use_stage2:
            sym_rs = rs_df.filter(pl.col("symbol") == sym).sort("date")
            if not sym_rs.is_empty():
                rs_joined = stock.select("date").join(
                    sym_rs.select("date", "rs_pctile"), on="date", how="left"
                )
                rs_arr = rs_joined["rs_pctile"].fill_null(0.0).to_numpy().astype(float)

        entry_filter = _build_entry_filter(close, high, low, rs_arr, use_stage2)
        trades = run_strategy(
            dates, open_, high, low, close, volume, strategy, params,
            entry_filter=entry_filter,
        )

        warmup = 252
        total_bars = max(len(close) - warmup, 1)
        d0 = dates[warmup] if len(dates) > warmup else dates[0]
        d1 = dates[-1]
        years = max(
            (d1 - d0).astype("timedelta64[D]").astype(int) / 365.25, 0.5
        )

        r = compute_metrics(trades, strategy, sym, total_bars, years)
        results.append(r)

    return results


def print_portfolio_summary(
    results: list[StrategyResult], strategy_name: str
) -> None:
    active = [r for r in results if r.total_trades > 0]
    if not active:
        print("  No trades across any stock.")
        return

    total_trades = sum(r.total_trades for r in active)
    total_wins = sum(r.winners for r in active)
    all_returns: list[float] = []
    for r in active:
        all_returns.extend([t.pnl_pct for t in r.trades])

    win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
    avg_ret = float(np.mean(all_returns)) if all_returns else 0
    median_ret = float(np.median(all_returns)) if all_returns else 0

    wins = [x for x in all_returns if x > 0]
    losses = [x for x in all_returns if x <= 0]
    pf = (
        sum(wins) / abs(sum(losses))
        if losses and sum(losses) != 0
        else float("inf")
    )

    avg_cagr = float(np.mean([r.cagr for r in active]))
    avg_sharpe = float(np.mean([r.sharpe for r in active]))
    avg_dd = float(np.mean([r.max_drawdown for r in active]))

    print(f"\n{'=' * 65}")
    print(f"  PORTFOLIO SUMMARY: {strategy_name}")
    print(f"{'=' * 65}")
    print(f"  Stocks tested:    {len(results)}")
    print(f"  Stocks w/ trades: {len(active)}")
    print(f"  Total trades:     {total_trades}")
    print(f"  Win rate:         {win_rate:.1f}%")
    print(f"  Avg return:       {avg_ret:+.2f}%  per trade")
    print(f"  Median return:    {median_ret:+.2f}%  per trade")
    print(f"  Profit factor:    {pf:.2f}")
    print(f"  Avg CAGR:         {avg_cagr:+.2f}%  (across stocks)")
    print(f"  Avg Sharpe:       {avg_sharpe:.2f}")
    print(f"  Avg max DD:       {avg_dd:.1f}%")

    by_pf = sorted(active, key=lambda r: -r.profit_factor)
    print("\n  Top 5 stocks by profit factor:")
    for r in by_pf[:5]:
        print(
            f"    {r.symbol:<15} PF={r.profit_factor:.2f}  Win={r.win_rate:.0f}%  "
            f"Trades={r.total_trades}  CAGR={r.cagr:+.1f}%"
        )

    print("\n  Bottom 5 stocks by profit factor:")
    for r in by_pf[-5:]:
        print(
            f"    {r.symbol:<15} PF={r.profit_factor:.2f}  Win={r.win_rate:.0f}%  "
            f"Trades={r.total_trades}  CAGR={r.cagr:+.1f}%"
        )

    print()


# ─── Main ─────────────────────────────────────────────────────────────

STRATEGIES = {
    "rsi2": {
        "name": "RSI(2) Pullback",
        "params": {"entry_threshold": 25, "exit_mode": "sma3", "stop_atr_mult": 3.0, "max_hold": 5},
    },
    "williams_r": {
        "name": "Williams %R",
        "params": {"wr_period": 5, "entry_threshold": -90, "max_hold": 15},
    },
    "consecutive_down": {
        "name": "3 Consecutive Down",
        "params": {"n_down": 3, "max_hold": 10},
    },
    "double_seven": {
        "name": "Double Seven",
        "params": {"max_hold": 20},
    },
    "ibs": {
        "name": "IBS (Internal Bar)",
        "params": {"entry_threshold": 0.2, "exit_threshold": 0.8, "max_hold": 10},
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mean Reversion Swing Trading Backtester"
    )
    parser.add_argument(
        "--data",
        default="data/yf_adjusted/ohlcv_all.parquet",
        help="Path to OHLCV parquet",
    )
    parser.add_argument(
        "--symbol", default=None, help="Single stock symbol (e.g., RELIANCE)"
    )
    parser.add_argument(
        "--strategy",
        default="all",
        choices=[*STRATEGIES, "all"],
        help="Strategy to test",
    )
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Test top N stocks by history length (0 = all)",
    )
    parser.add_argument(
        "--trades", action="store_true", help="Print individual trades"
    )
    parser.add_argument(
        "--all-symbols",
        action="store_true",
        help="Test all symbols in parquet (default: Nifty 100 only)",
    )
    parser.add_argument(
        "--no-stage2",
        action="store_true",
        help="Disable Stage 2 + RS filter; use simple SMA200 trend filter instead",
    )
    args = parser.parse_args()

    use_stage2 = not args.no_stage2

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"  Data file not found: {data_path}")
        print("  Run fetch_yf_ohlcv.py first, or provide --data path.")
        sys.exit(1)

    print(f"\n  Loading {data_path}...")
    raw_df = pl.read_parquet(str(data_path))

    if not args.all_symbols and not args.symbol:
        # Keep NIFTYBEES for RS benchmark even if not in Nifty 100
        keep = set(NIFTY_100) | {BENCHMARK_SYMBOL}
        raw_df = raw_df.filter(pl.col("symbol").is_in(keep))
        print(f"  Filtered to Nifty 100 universe + benchmark ({len(keep)} symbols)")

    if args.start:
        raw_df = raw_df.filter(
            pl.col("date") >= pl.lit(datetime.strptime(args.start, "%Y-%m-%d").date())
        )
    if args.end:
        raw_df = raw_df.filter(
            pl.col("date") <= pl.lit(datetime.strptime(args.end, "%Y-%m-%d").date())
        )

    # Precompute cross-sectional RS percentile ranks
    rs_df: pl.DataFrame | None = None
    if use_stage2:
        has_benchmark = raw_df.filter(pl.col("symbol") == BENCHMARK_SYMBOL).height > 0
        if has_benchmark:
            print(f"  Computing RS percentile ranks (benchmark={BENCHMARK_SYMBOL}, min={RS_MIN_PERCENTILE:.0f}th pctile)...")
            rs_df = precompute_rs_percentiles(raw_df)
        else:
            print(f"  WARNING: {BENCHMARK_SYMBOL} not found — RS filter disabled, using Stage 2 template only")

    # Exclude benchmark from trading universe
    df = raw_df.filter(pl.col("symbol") != BENCHMARK_SYMBOL)

    symbols = df["symbol"].unique().sort().to_list()
    date_range = f"{df['date'].min()} → {df['date'].max()}"
    filter_label = "Stage 2 + RS ≥ {:.0f}".format(RS_MIN_PERCENTILE) if use_stage2 else "SMA200 only"
    print(f"  Symbols: {len(symbols)}  |  Range: {date_range}  |  Rows: {len(df):,}")
    print(f"  Entry filter: {filter_label}")

    strategies_to_run = (
        list(STRATEGIES.keys()) if args.strategy == "all" else [args.strategy]
    )

    if args.symbol:
        sym = args.symbol.upper()
        stock = df.filter(pl.col("symbol") == sym).sort("date")
        if stock.is_empty():
            print(f"  Symbol '{sym}' not found in data.")
            sys.exit(1)

        dates = stock["date"].to_numpy()
        open_ = stock["open"].to_numpy().astype(float)
        high = stock["high"].to_numpy().astype(float)
        low = stock["low"].to_numpy().astype(float)
        close = stock["close"].to_numpy().astype(float)
        volume = stock["volume"].to_numpy().astype(float)

        rs_arr: np.ndarray | None = None
        if rs_df is not None and use_stage2:
            sym_rs = rs_df.filter(pl.col("symbol") == sym).sort("date")
            if not sym_rs.is_empty():
                rs_joined = stock.select("date").join(
                    sym_rs.select("date", "rs_pctile"), on="date", how="left"
                )
                rs_arr = rs_joined["rs_pctile"].fill_null(0.0).to_numpy().astype(float)

        entry_filter = _build_entry_filter(close, high, low, rs_arr, use_stage2)

        warmup = 252
        total_bars = max(len(close) - warmup, 1)
        d0 = dates[warmup] if len(dates) > warmup else dates[0]
        d1 = dates[-1]
        years = max(
            (d1 - d0).astype("timedelta64[D]").astype(int) / 365.25, 0.5
        )

        print(f"\n  {sym}: {len(close)} bars  |  {d0} → {d1}  |  {years:.1f} years")

        all_results: list[StrategyResult] = []
        for strat_key in strategies_to_run:
            strat = STRATEGIES[strat_key]
            trades = run_strategy(
                dates, open_, high, low, close, volume, strat_key, strat["params"],
                entry_filter=entry_filter,
            )
            r = compute_metrics(trades, strat["name"], sym, total_bars, years)
            all_results.append(r)

            if len(strategies_to_run) == 1:
                print_result(r)
                if args.trades:
                    print_recent_trades(r.trades, n=20)

        if len(strategies_to_run) > 1:
            print_comparison(all_results)
            if args.trades:
                best = max(
                    all_results,
                    key=lambda x: x.profit_factor if x.total_trades > 0 else 0,
                )
                print(f"  Showing trades for best strategy: {best.name}")
                print_recent_trades(best.trades, n=15)

    else:
        for strat_key in strategies_to_run:
            strat = STRATEGIES[strat_key]
            results = run_portfolio_backtest(
                df, strat_key, strat["params"],
                rs_df=rs_df, use_stage2=use_stage2, top_n=args.top,
            )
            print_portfolio_summary(results, strat["name"])


if __name__ == "__main__":
    main()

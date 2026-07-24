"""Canonical metrics, computed from the daily equity curve.

Equity-curve derived, never serially-compounded R. This is what
``docs/BACKTEST_TRUTH.md`` requires for SEPA, and applying the same rule to
every strategy is what makes a factor portfolio and a breakout system
legitimately comparable on one leaderboard.

The CAGR / max-drawdown / Calmar arithmetic is a deliberate line-for-line port
of ``optimize_joint_wf._metrics`` (the canonical parity harness). Do not
"improve" it — a different-but-defensible definition silently moves the
committed baseline.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import polars as pl

from sepa_scanner.lab.result import EquityPoint, RunMetrics, TradeRecord

MIN_CURVE_POINTS = 20
TRADING_DAYS_PER_YEAR = 252


def _curve_arrays(
    daily_equity: pl.DataFrame, start: date, end: date
) -> tuple[np.ndarray, list[date]] | None:
    eq = daily_equity.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).sort("date")
    if eq.height < MIN_CURVE_POINTS:
        return None
    return eq["equity"].to_numpy().astype(float), eq["date"].to_list()


def compute_core(equity: np.ndarray, dates: list[date]) -> dict[str, float]:
    """CAGR / max drawdown / Calmar exactly as the canonical harness computes them."""
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-6)
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1

    peak = -1e18
    mdd = 0.0
    for value in equity:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1)

    calmar = cagr / abs(mdd) if mdd < 0 else float("nan")
    return {"cagr": float(cagr), "mdd": float(abs(mdd)), "calmar": float(calmar)}


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    running_peak = np.maximum.accumulate(equity)
    return equity / running_peak - 1.0


def _longest_drawdown_days(equity: np.ndarray, dates: list[date]) -> int:
    """Longest stretch between an equity peak and the recovery of that peak."""
    peak_value = equity[0]
    peak_index = 0
    longest = 0
    for i, value in enumerate(equity):
        if value >= peak_value:
            longest = max(longest, (dates[i] - dates[peak_index]).days)
            peak_value = value
            peak_index = i
    # An unrecovered drawdown still counts, measured to the end of the window.
    longest = max(longest, (dates[-1] - dates[peak_index]).days)
    return int(longest)


def _risk_ratios(equity: np.ndarray) -> tuple[float | None, float | None, float]:
    returns = np.diff(equity) / equity[:-1]
    returns = returns[np.isfinite(returns)]
    if returns.size < 2:
        return None, None, 0.0

    volatility = float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
    mean_annual = float(returns.mean() * TRADING_DAYS_PER_YEAR)

    sharpe = mean_annual / volatility if volatility > 0 else None

    downside = returns[returns < 0]
    if downside.size >= 2:
        downside_vol = float(downside.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
        sortino = mean_annual / downside_vol if downside_vol > 0 else None
    else:
        sortino = None

    return sharpe, sortino, volatility * 100.0


def _trade_stats(trades: list[TradeRecord]) -> dict[str, float | int | None]:
    closed = [t for t in trades if t.status == "closed" and t.r_multiple is not None]
    if not closed:
        return {
            "trades": len(trades) or None,
            "win_rate": None,
            "avg_r": None,
            "expectancy_r": None,
            "payoff_ratio": None,
            "avg_hold_days": None,
        }

    r_values = [float(t.r_multiple) for t in closed]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]
    holds = [t.hold_days for t in closed if t.hold_days is not None]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    return {
        "trades": len(closed),
        "win_rate": len(wins) / len(r_values) * 100.0,
        "avg_r": sum(r_values) / len(r_values),
        "expectancy_r": sum(r_values) / len(r_values),
        "payoff_ratio": (avg_win / avg_loss) if avg_loss > 0 else None,
        "avg_hold_days": (sum(holds) / len(holds)) if holds else None,
    }


def build_equity_points(
    daily_equity: pl.DataFrame,
    start: date,
    end: date,
    benchmark: pl.DataFrame | None = None,
) -> list[EquityPoint]:
    eq = daily_equity.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).sort("date")
    if eq.is_empty():
        return []

    equity = eq["equity"].to_numpy().astype(float)
    drawdown = _drawdown_series(equity) * 100.0
    dates = eq["date"].to_list()

    bench_by_date: dict[date, float] = {}
    if benchmark is not None and not benchmark.is_empty():
        col = "tri" if "tri" in benchmark.columns else "close"
        bench = benchmark.sort("date")
        base = float(bench[col][0])
        if base > 0:
            start_equity = float(equity[0])
            bench_by_date = {
                d: start_equity * float(v) / base
                for d, v in zip(bench["date"].to_list(), bench[col].to_list())
            }

    return [
        EquityPoint(
            date=d,
            equity=float(e),
            drawdown_pct=float(dd),
            benchmark_equity=bench_by_date.get(d),
        )
        for d, e, dd in zip(dates, equity, drawdown)
    ]


def summarize(
    daily_equity: pl.DataFrame,
    trades: list[TradeRecord],
    start: date,
    end: date,
    *,
    benchmark: pl.DataFrame | None = None,
    exposure_pct: float | None = None,
    include_trade_stats: bool = True,
) -> RunMetrics:
    arrays = _curve_arrays(daily_equity, start, end)
    if arrays is None:
        raise ValueError(
            f"equity curve has fewer than {MIN_CURVE_POINTS} points in "
            f"{start}..{end}; window too short to summarise"
        )
    equity, dates = arrays

    core = compute_core(equity, dates)
    sharpe, sortino, volatility_pct = _risk_ratios(equity)

    benchmark_cagr = None
    if benchmark is not None and not benchmark.is_empty():
        col = "tri" if "tri" in benchmark.columns else "close"
        bench = benchmark.filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        ).sort("date")
        if bench.height >= MIN_CURVE_POINTS:
            bench_core = compute_core(
                bench[col].to_numpy().astype(float), bench["date"].to_list()
            )
            benchmark_cagr = bench_core["cagr"] * 100.0

    stats = (
        _trade_stats(trades)
        if include_trade_stats
        else {
            "trades": None,
            "win_rate": None,
            "avg_r": None,
            "expectancy_r": None,
            "payoff_ratio": None,
            "avg_hold_days": None,
        }
    )

    cagr_pct = core["cagr"] * 100.0
    calmar = core["calmar"]

    return RunMetrics(
        cagr_pct=cagr_pct,
        total_return_pct=(equity[-1] / equity[0] - 1.0) * 100.0,
        terminal_equity=float(equity[-1]),
        max_drawdown_pct=core["mdd"] * 100.0,
        max_drawdown_days=_longest_drawdown_days(equity, dates),
        calmar=None if math.isnan(calmar) else calmar,
        sharpe=sharpe,
        sortino=sortino,
        volatility_pct=volatility_pct,
        exposure_pct=exposure_pct,
        benchmark_cagr_pct=benchmark_cagr,
        excess_cagr_pct=None if benchmark_cagr is None else cagr_pct - benchmark_cagr,
        **stats,
    )

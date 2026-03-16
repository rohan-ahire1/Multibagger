from __future__ import annotations

from datetime import date
from statistics import median

import polars as pl

from sepa_scanner.backtest.models import BacktestSummary, BacktestTrade, ExitMode

_DEFAULT_CAPITAL = 100_000.0


def _closed_trades(trades: list[BacktestTrade]) -> list[BacktestTrade]:
    return [trade for trade in trades if trade.exit_date is not None and trade.r_multiple is not None]


def build_equity_curve(
    trades: list[BacktestTrade],
) -> pl.DataFrame:
    closed_trades = sorted(
        _closed_trades(trades),
        key=lambda trade: (trade.exit_date, trade.symbol, trade.signal_date),
    )
    if not closed_trades:
        return pl.DataFrame(
            schema={
                "exit_date": pl.Date,
                "symbol": pl.String,
                "r_multiple": pl.Float64,
                "cumulative_r": pl.Float64,
                "running_peak": pl.Float64,
                "drawdown": pl.Float64,
            }
        )

    cumulative = 0.0
    running_peak = 0.0
    rows: list[dict[str, object]] = []
    for trade in closed_trades:
        cumulative += float(trade.r_multiple)
        running_peak = max(running_peak, cumulative)
        rows.append(
            {
                "exit_date": trade.exit_date,
                "symbol": trade.symbol,
                "r_multiple": float(trade.r_multiple),
                "cumulative_r": cumulative,
                "running_peak": running_peak,
                "drawdown": cumulative - running_peak,
            }
        )
    return pl.DataFrame(rows)


def summarize_backtest(
    trades: list[BacktestTrade],
    exit_mode: ExitMode = ExitMode.MINERVINI_PHASED,
) -> BacktestSummary:
    closed_trades = _closed_trades(trades)
    winners = [float(trade.r_multiple) for trade in closed_trades if float(trade.r_multiple) > 0]
    losers = [float(trade.r_multiple) for trade in closed_trades if float(trade.r_multiple) < 0]
    r_values = [float(trade.r_multiple) for trade in closed_trades]
    return_values = [float(trade.return_pct) for trade in closed_trades if trade.return_pct is not None]
    curve = build_equity_curve(trades)

    longest_losing_streak = 0
    current_losing_streak = 0
    sorted_closed = sorted(closed_trades, key=lambda trade: (trade.exit_date, trade.symbol, trade.signal_date))
    for trade in sorted_closed:
        if float(trade.r_multiple) < 0:
            current_losing_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_losing_streak)
        else:
            current_losing_streak = 0

    payoff_ratio = None
    if winners and losers:
        payoff_ratio = (sum(winners) / len(winners)) / abs(sum(losers) / len(losers))

    average_r = (sum(r_values) / len(r_values)) if r_values else None
    average_return_pct = (sum(return_values) / len(return_values)) if return_values else None
    max_drawdown = min(curve["drawdown"].to_list()) if curve.height else 0.0

    mfe_values = [float(t.mfe_r) for t in closed_trades if t.mfe_r is not None]
    mae_values = [float(t.mae_r) for t in closed_trades if t.mae_r is not None]
    giveback_values = [
        float(t.giveback_pct) for t in closed_trades
        if t.giveback_pct is not None and t.r_multiple is not None and float(t.r_multiple) > 0
    ]

    # Phased exit counts and percentages
    breakeven_exits = sum(1 for t in closed_trades if t.exit_reason == "breakeven_stop")
    trail_exits = sum(1 for t in closed_trades if t.exit_reason and t.exit_reason.startswith("trail_"))
    tighten_exits = sum(1 for t in closed_trades if t.exit_reason and t.exit_reason.startswith("tighten_"))
    climax_sells = sum(1 for t in closed_trades if t.exit_reason == "climax_sell")
    extension_sells = sum(1 for t in closed_trades if t.exit_reason and t.exit_reason.startswith("extension_"))
    partial_profit_exits = sum(1 for t in closed_trades if t.partial_exit_pct is not None)

    denom = len(closed_trades)
    _breakeven_phases = {"breakeven", "partial_profit", "trailing", "tighten", "exit"}
    pct_reaching_breakeven = (
        sum(1 for t in closed_trades if (t.exit_phase in _breakeven_phases) or (t.partial_exit_pct is not None)) / denom
        if denom else 0.0
    )
    pct_reaching_partial = sum(1 for t in closed_trades if t.partial_exit_pct is not None) / denom if denom else 0.0
    _trail_phases = {"trailing", "tighten", "exit"}
    pct_reaching_trail = sum(1 for t in closed_trades if t.exit_phase in _trail_phases) / denom if denom else 0.0

    partial_r_values = [float(t.partial_exit_r) for t in closed_trades if t.partial_exit_r is not None]
    avg_partial_r = (sum(partial_r_values) / len(partial_r_values)) if partial_r_values else None

    exit_engine = "minervini_phased" if exit_mode == ExitMode.MINERVINI_PHASED else "legacy"

    return BacktestSummary(
        total_signals=len(trades),
        filled_trades=sum(1 for trade in trades if trade.entry_date is not None),
        not_filled_signals=sum(1 for trade in trades if trade.status == "not_filled"),
        stopped_trades=sum(1 for trade in trades if trade.status == "stopped"),
        target_exits=sum(1 for trade in trades if trade.status == "target"),
        time_stop_exits=sum(1 for trade in trades if trade.status == "time_stop"),
        win_rate=(len(winners) / len(closed_trades)) if closed_trades else 0.0,
        average_return_pct=average_return_pct,
        average_r=average_r,
        median_r=median(r_values) if r_values else None,
        expectancy=average_r,
        payoff_ratio=payoff_ratio,
        cumulative_r=sum(r_values) if r_values else 0.0,
        max_drawdown=max_drawdown,
        longest_losing_streak=longest_losing_streak,
        avg_mfe_r=(sum(mfe_values) / len(mfe_values)) if mfe_values else None,
        avg_mae_r=(sum(mae_values) / len(mae_values)) if mae_values else None,
        avg_giveback_pct=(sum(giveback_values) / len(giveback_values)) if giveback_values else None,
        dead_money_exits=sum(1 for trade in trades if trade.status == "dead_money"),
        exit_mode=exit_mode.value,
        breakeven_exits=breakeven_exits,
        partial_profit_exits=partial_profit_exits,
        trail_exits=trail_exits,
        tighten_exits=tighten_exits,
        climax_sells=climax_sells,
        extension_sells=extension_sells,
        avg_partial_r=avg_partial_r,
        pct_reaching_breakeven=pct_reaching_breakeven,
        pct_reaching_partial=pct_reaching_partial,
        pct_reaching_trail=pct_reaching_trail,
        exit_engine=exit_engine,
    )

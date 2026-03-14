from __future__ import annotations

from datetime import date

from sepa_scanner.backtest.metrics import build_equity_curve, summarize_backtest
from sepa_scanner.backtest.models import BacktestTrade


def _trade(
    *,
    symbol: str,
    signal_date: date,
    status: str,
    entry_date: date | None,
    entry_price: float | None,
    exit_date: date | None,
    exit_price: float | None,
    r_multiple: float | None,
    return_pct: float | None,
) -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol,
        signal_date=signal_date,
        status=status,
        entry_ref=101.0,
        stop=95.0,
        pivot=100.0,
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        holding_days=3 if exit_date else None,
        return_pct=return_pct,
        r_multiple=r_multiple,
    )


def test_backtest_metrics_summarize_closed_and_unfilled_trades() -> None:
    trades = [
        _trade(
            symbol="AAA",
            signal_date=date(2026, 3, 2),
            status="target",
            entry_date=date(2026, 3, 3),
            entry_price=101.0,
            exit_date=date(2026, 3, 5),
            exit_price=113.0,
            r_multiple=2.0,
            return_pct=11.88,
        ),
        _trade(
            symbol="BBB",
            signal_date=date(2026, 3, 3),
            status="stopped",
            entry_date=date(2026, 3, 4),
            entry_price=101.0,
            exit_date=date(2026, 3, 6),
            exit_price=95.0,
            r_multiple=-1.0,
            return_pct=-5.94,
        ),
        _trade(
            symbol="CCC",
            signal_date=date(2026, 3, 4),
            status="time_stop",
            entry_date=date(2026, 3, 5),
            entry_price=101.0,
            exit_date=date(2026, 3, 7),
            exit_price=104.0,
            r_multiple=0.5,
            return_pct=2.97,
        ),
        _trade(
            symbol="DDD",
            signal_date=date(2026, 3, 5),
            status="not_filled",
            entry_date=None,
            entry_price=None,
            exit_date=None,
            exit_price=None,
            r_multiple=None,
            return_pct=None,
        ),
    ]

    summary = summarize_backtest(trades)

    assert summary.total_signals == 4
    assert summary.filled_trades == 3
    assert summary.not_filled_signals == 1
    assert summary.stopped_trades == 1
    assert summary.target_exits == 1
    assert summary.time_stop_exits == 1
    assert summary.win_rate == 2 / 3
    assert summary.average_r == 0.5
    assert summary.median_r == 0.5
    assert summary.expectancy == 0.5
    assert summary.payoff_ratio == 1.25
    assert summary.cumulative_r == 1.5
    assert summary.max_drawdown == -1.0
    assert summary.longest_losing_streak == 1


def test_backtest_metrics_build_equity_curve_in_exit_order() -> None:
    trades = [
        _trade(
            symbol="AAA",
            signal_date=date(2026, 3, 2),
            status="target",
            entry_date=date(2026, 3, 3),
            entry_price=101.0,
            exit_date=date(2026, 3, 5),
            exit_price=113.0,
            r_multiple=2.0,
            return_pct=11.88,
        ),
        _trade(
            symbol="BBB",
            signal_date=date(2026, 3, 3),
            status="stopped",
            entry_date=date(2026, 3, 4),
            entry_price=101.0,
            exit_date=date(2026, 3, 6),
            exit_price=95.0,
            r_multiple=-1.0,
            return_pct=-5.94,
        ),
        _trade(
            symbol="CCC",
            signal_date=date(2026, 3, 4),
            status="time_stop",
            entry_date=date(2026, 3, 5),
            entry_price=101.0,
            exit_date=date(2026, 3, 7),
            exit_price=104.0,
            r_multiple=0.5,
            return_pct=2.97,
        ),
    ]

    curve = build_equity_curve(trades)

    assert curve["exit_date"].to_list() == [date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 7)]
    assert curve["cumulative_r"].to_list() == [2.0, 1.0, 1.5]
    assert curve["drawdown"].to_list() == [0.0, -1.0, -0.5]


def test_backtest_metrics_include_force_closed_incomplete_trade_in_closed_stats() -> None:
    trades = [
        _trade(
            symbol="AAA",
            signal_date=date(2026, 3, 2),
            status="incomplete_data",
            entry_date=date(2026, 3, 3),
            entry_price=101.0,
            exit_date=date(2026, 3, 4),
            exit_price=100.5,
            r_multiple=-0.08333333333333333,
            return_pct=-0.5,
        ),
        _trade(
            symbol="BBB",
            signal_date=date(2026, 3, 3),
            status="not_filled",
            entry_date=None,
            entry_price=None,
            exit_date=None,
            exit_price=None,
            r_multiple=None,
            return_pct=None,
        ),
    ]

    summary = summarize_backtest(trades)

    assert summary.total_signals == 2
    assert summary.filled_trades == 1
    assert summary.not_filled_signals == 1
    assert summary.cumulative_r == -0.08333333333333333
    assert summary.average_r == -0.08333333333333333


def test_skipped_split_excluded_from_metrics() -> None:
    trades = [
        BacktestTrade(
            symbol="ABC", signal_date=date(2026, 3, 2), status="stopped",
            entry_ref=101.0, stop=95.0, entry_date=date(2026, 3, 3),
            entry_price=101.0, exit_date=date(2026, 3, 5), exit_price=95.0,
            holding_days=2, return_pct=-5.94, r_multiple=-1.0,
        ),
        BacktestTrade(
            symbol="XYZ", signal_date=date(2026, 3, 4), status="skipped_split",
            entry_ref=200.0, stop=190.0,
        ),
    ]
    summary = summarize_backtest(trades)
    assert summary.total_signals == 2
    assert summary.filled_trades == 1
    assert summary.skipped_split_count == 1
    assert summary.stopped_trades == 1
    assert summary.win_rate == 0.0

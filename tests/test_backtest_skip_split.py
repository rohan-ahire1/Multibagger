from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams


def _signal(
    *,
    symbol: str = "ABC",
    signal_date: date = date(2026, 3, 2),
    entry_ref: float = 101.0,
    stop: float = 95.0,
    pivot: float = 100.0,
) -> pl.DataFrame:
    return pl.DataFrame({
        "signal_date": [signal_date],
        "symbol": [symbol],
        "signal_type": ["BREAKOUT"],
        "pivot": [pivot],
        "entry_ref": [entry_ref],
        "stop": [stop],
    })


def _ohlcv(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _corp_actions(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "action_type": pl.String,
            "ex_date": pl.Date,
            "adjustment_factor": pl.Float64,
        })
    return pl.DataFrame(rows)


def test_skip_split_during_pending_window() -> None:
    """Signal with a split ex_date in the pending window => skipped_split."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 50.0, "high": 52.0, "low": 49.0, "close": 51.0},
        {"date": date(2026, 3, 5), "symbol": "ABC", "open": 51.0, "high": 52.5, "low": 50.5, "close": 52.0},
    ])
    corp = _corp_actions([{
        "symbol": "ABC", "action_type": "split",
        "ex_date": date(2026, 3, 4), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(pending_days=5), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status == "skipped_split"
    assert trade.entry_date is None


def test_skip_split_during_holding_period() -> None:
    """Trade entered, but split ex_date falls during max hold window => skipped_split."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
        {"date": date(2026, 3, 5), "symbol": "ABC", "open": 51.0, "high": 52.0, "low": 50.0, "close": 51.5},
        {"date": date(2026, 3, 6), "symbol": "ABC", "open": 51.5, "high": 52.5, "low": 51.0, "close": 52.0},
    ])
    corp = _corp_actions([{
        "symbol": "ABC", "action_type": "split",
        "ex_date": date(2026, 3, 5), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=10), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status == "skipped_split"


def test_no_skip_when_no_split() -> None:
    """Normal trade without any corporate action => proceeds normally."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])
    corp = _corp_actions([])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status != "skipped_split"


def test_no_skip_when_corp_actions_none() -> None:
    """Backward compat: corp_actions=None => no skip logic, normal simulation."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2))
    trade = result.trades[0]
    assert trade.status != "skipped_split"


def test_skip_split_different_symbol_not_affected() -> None:
    """Split for different symbol doesn't affect this trade."""
    signals = _signal(symbol="ABC", signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])
    corp = _corp_actions([{
        "symbol": "XYZ", "action_type": "split",
        "ex_date": date(2026, 3, 4), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status != "skipped_split"

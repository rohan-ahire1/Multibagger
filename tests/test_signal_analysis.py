from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.backtest.signal_analysis import analyze_signal_quality


def _signal_df(
    symbol: str = "ABC",
    signal_date: date = date(2026, 3, 2),
    entry_ref: float = 101.0,
    stop: float = 95.0,
    pivot: float = 100.0,
    signal_type: str = "BREAKOUT",
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "signal_date": [signal_date],
            "signal_type": [signal_type],
            "entry_ref": [entry_ref],
            "stop": [stop],
            "pivot": [pivot],
        }
    )


def _ohlcv_df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


def test_analyze_signal_quality_pivot_reached_on_day1() -> None:
    signals = _signal_df(signal_date=date(2026, 3, 2), entry_ref=101.0)
    ohlcv = _ohlcv_df(
        [
            {"date": date(2026, 3, 2), "symbol": "ABC", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1000},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 1000},
            {"date": date(2026, 3, 5), "symbol": "ABC", "open": 102.5, "high": 104.0, "low": 102.0, "close": 103.5, "volume": 1000},
            {"date": date(2026, 3, 6), "symbol": "ABC", "open": 103.5, "high": 105.0, "low": 103.0, "close": 104.5, "volume": 1000},
            {"date": date(2026, 3, 7), "symbol": "ABC", "open": 104.5, "high": 106.0, "low": 104.0, "close": 105.5, "volume": 1000},
        ]
    )
    result = analyze_signal_quality(signals, ohlcv, pending_days=5)
    assert result["total_with_data"] == 1
    assert result["pivot_reached"] == 1
    assert result["pivot_not_reached"] == 0
    assert result["pivot_reached_pct"] == 100.0
    assert result["day_reached_breakdown"][0]["day"] == 1
    assert result["day_reached_breakdown"][0]["count"] == 1
    assert result["day_reached_breakdown"][0]["pct_of_reached"] == 100.0
    assert result["profitable_at_last_day"] == 1
    assert result["profitable_at_last_day_pct"] == 100.0
    assert result["avg_last_day_return_pct"] == pytest.approx((105.5 - 100.0) / 100.0 * 100.0)
    assert result["gap_analysis"]["gap_up_count"] == 1
    assert result["gap_analysis"]["total"] == 1
    assert result["unfilled_analysis"]["count"] == 0


def test_analyze_signal_quality_pivot_not_reached() -> None:
    signals = _signal_df(signal_date=date(2026, 3, 2), entry_ref=105.0)
    ohlcv = _ohlcv_df(
        [
            {"date": date(2026, 3, 2), "symbol": "ABC", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 100.5, "high": 101.0, "low": 100.0, "close": 100.8, "volume": 1000},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 100.8, "high": 101.5, "low": 100.5, "close": 101.2, "volume": 1000},
            {"date": date(2026, 3, 5), "symbol": "ABC", "open": 101.2, "high": 102.0, "low": 101.0, "close": 101.8, "volume": 1000},
            {"date": date(2026, 3, 6), "symbol": "ABC", "open": 101.8, "high": 102.5, "low": 101.5, "close": 102.2, "volume": 1000},
        ]
    )
    result = analyze_signal_quality(signals, ohlcv, pending_days=5)
    assert result["total_with_data"] == 1
    assert result["pivot_reached"] == 0
    assert result["pivot_not_reached"] == 1
    assert result["pivot_reached_pct"] == 0.0
    assert result["unfilled_analysis"]["count"] == 1
    assert result["unfilled_analysis"]["avg_max_gain_pct"] == pytest.approx((102.5 - 100.0) / 100.0 * 100.0)
    assert result["unfilled_analysis"]["avg_last_day_return_pct"] == pytest.approx((102.2 - 100.0) / 100.0 * 100.0)


def test_analyze_signal_quality_filters_breakout_only() -> None:
    signals = pl.concat([
        _signal_df(symbol="A", signal_date=date(2026, 3, 2), signal_type="BREAKOUT"),
        _signal_df(symbol="B", signal_date=date(2026, 3, 2), signal_type="WATCH"),
    ])
    ohlcv = _ohlcv_df(
        [
            {"date": date(2026, 3, 2), "symbol": "A", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"date": date(2026, 3, 3), "symbol": "A", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 1000},
            {"date": date(2026, 3, 2), "symbol": "B", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"date": date(2026, 3, 3), "symbol": "B", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 1000},
        ]
    )
    result = analyze_signal_quality(signals, ohlcv, pending_days=5)
    assert result["total_with_data"] == 1


def test_analyze_signal_quality_empty_breakout() -> None:
    signals = _signal_df(signal_type="WATCH")
    ohlcv = _ohlcv_df([
        {"date": date(2026, 3, 2), "symbol": "ABC", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
    ])
    result = analyze_signal_quality(signals, ohlcv, pending_days=5)
    assert result["total_with_data"] == 0
    assert result["pivot_reached"] == 0
    assert len(result["per_day_returns"]) == 5


def test_analyze_signal_quality_gap_analysis() -> None:
    signals = _signal_df(signal_date=date(2026, 3, 2), entry_ref=101.0)
    ohlcv = _ohlcv_df(
        [
            {"date": date(2026, 3, 2), "symbol": "ABC", "open": 100.0, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1000},
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 1000},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.5, "high": 103.5, "low": 102.0, "close": 103.0, "volume": 1000},
        ]
    )
    result = analyze_signal_quality(signals, ohlcv, pending_days=2)
    gap = (102.0 - 100.0) / 100.0 * 100.0
    assert result["gap_analysis"]["gap_up_count"] == 1
    assert result["gap_analysis"]["avg_gap_pct"] == pytest.approx(gap)
    assert result["gap_analysis"]["big_gap_up_count"] == 1
    assert result["gap_analysis"]["big_gap_down_count"] == 0

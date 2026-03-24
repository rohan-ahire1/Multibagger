from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams
from sepa_scanner.backtest.portfolio import PortfolioManager
from sepa_scanner.backtest.regime_gate import finalize_regime_gate, gate_lookup


def _signal(
    *,
    symbol: str = "ABC",
    signal_date: date = date(2026, 3, 2),
    entry_ref: float = 101.0,
    stop: float = 95.0,
    pivot: float = 100.0,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "signal_date": [signal_date],
            "symbol": [symbol],
            "signal_type": ["BREAKOUT"],
            "pivot": [pivot],
            "entry_ref": [entry_ref],
            "stop": [stop],
        }
    )


def _ohlcv(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


def test_finalize_regime_gate_requires_trend_breadth_and_low_volatility() -> None:
    features = pl.DataFrame(
        {
            "date": [date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6)],
            "trending_market": [True, True, False, True],
            "breadth_pct": [0.55, 0.35, 0.55, 0.55],
            "benchmark_vol_20_ann": [0.20, 0.20, 0.20, 0.30],
        }
    )

    gate = finalize_regime_gate(
        features,
        breadth_weak_pct=0.40,
        high_volatility_threshold=0.25,
    ).sort("date")

    assert gate["entries_allowed"].to_list() == [True, False, False, False]
    lookup = gate_lookup(gate)
    assert lookup[date(2026, 3, 3)] is True
    assert lookup[date(2026, 3, 4)] is False


def test_portfolio_entry_gate_waits_for_next_allowed_fill_day() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 101.5, "high": 102.0, "low": 100.0, "close": 101.2},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 101.1, "high": 102.0, "low": 100.5, "close": 101.4},
            {"date": date(2026, 3, 5), "symbol": "ABC", "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.3},
        ]
    )
    entry_gate = {
        date(2026, 3, 3): False,
        date(2026, 3, 4): True,
        date(2026, 3, 5): True,
    }
    pm = PortfolioManager(max_positions=5, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)

    result = simulate_breakout_backtest(
        signals,
        prices,
        BacktestParams(pending_days=2, max_hold_days=2),
        portfolio_manager=pm,
        use_rust=False,
        entry_gate=entry_gate,
    )

    trade = result.trades[0]
    assert trade.entry_date == date(2026, 3, 4)
    assert trade.status == "time_stop"


def test_portfolio_entry_gate_marks_not_filled_when_all_pending_days_blocked() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 101.5, "high": 102.0, "low": 100.0, "close": 101.2},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 101.1, "high": 102.0, "low": 100.5, "close": 101.4},
        ]
    )
    entry_gate = {
        date(2026, 3, 3): False,
        date(2026, 3, 4): False,
    }
    pm = PortfolioManager(max_positions=5, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)

    result = simulate_breakout_backtest(
        signals,
        prices,
        BacktestParams(pending_days=2, max_hold_days=2),
        portfolio_manager=pm,
        use_rust=False,
        entry_gate=entry_gate,
    )

    trade = result.trades[0]
    assert trade.entry_date is None
    assert trade.status == "not_filled"


def test_rust_portfolio_entry_gate_matches_python_when_extension_available() -> None:
    pytest.importorskip("multibagger_core")

    signals = _signal(signal_date=date(2026, 3, 3))
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC", "open": 100.5, "high": 100.9, "low": 99.8, "close": 100.8},
            {"date": date(2026, 3, 4), "symbol": "ABC", "open": 101.5, "high": 102.0, "low": 100.0, "close": 101.2},
            {"date": date(2026, 3, 5), "symbol": "ABC", "open": 101.1, "high": 102.0, "low": 100.5, "close": 101.4},
            {"date": date(2026, 3, 6), "symbol": "ABC", "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.3},
        ]
    )
    entry_gate = {
        date(2026, 3, 4): False,
        date(2026, 3, 5): True,
        date(2026, 3, 6): True,
    }
    params = BacktestParams(pending_days=2, max_hold_days=2)
    python_pm = PortfolioManager(max_positions=5, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    rust_pm = PortfolioManager(max_positions=5, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)

    python_result = simulate_breakout_backtest(
        signals,
        prices,
        params,
        portfolio_manager=python_pm,
        use_rust=False,
        entry_gate=entry_gate,
    )
    rust_result = simulate_breakout_backtest(
        signals,
        prices,
        params,
        portfolio_manager=rust_pm,
        use_rust=True,
        entry_gate=entry_gate,
    )

    assert python_result.trades[0].entry_date == rust_result.trades[0].entry_date == date(2026, 3, 5)
    assert python_result.trades[0].entry_price == pytest.approx(101.1)
    assert rust_result.trades[0].entry_price == pytest.approx(101.1)

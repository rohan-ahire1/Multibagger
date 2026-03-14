from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams
from sepa_scanner.backtest.portfolio import PortfolioManager


def test_rejects_trade_when_max_positions_reached() -> None:
    pm = PortfolioManager(max_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    assert pm.can_open_trade(sector="IT") is True
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="PHARMA", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="FMCG") is False


def test_rejects_trade_when_max_sector_reached() -> None:
    pm = PortfolioManager(max_positions=10, max_sector_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="IT", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="IT") is False
    assert pm.can_open_trade(sector="PHARMA") is True


def test_rejects_trade_when_max_heat_exceeded() -> None:
    # Capital = 100k, risk_per_trade = 1% = 1000, heat = 1000/100k = 1%
    # max_heat = 2%, so can open 2 trades
    pm = PortfolioManager(
        max_positions=10, max_heat_pct=0.02,
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
    )
    pm.open_trade(symbol="A", sector="IT", risk_per_share=5.0, entry_price=100.0)
    pm.open_trade(symbol="B", sector="PHARMA", risk_per_share=4.0, entry_price=80.0)
    assert pm.can_open_trade(sector="FMCG") is False


def test_drawdown_halves_position_size() -> None:
    pm = PortfolioManager(
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
        drawdown_half_pct=0.10,
    )
    pm.update_equity(90_000.0)  # -10% drawdown
    assert pm.position_size_multiplier == 0.5


def test_drawdown_stops_new_entries() -> None:
    pm = PortfolioManager(
        initial_capital=100_000.0, risk_per_trade_pct=0.01,
        drawdown_stop_pct=0.15,
    )
    pm.update_equity(85_000.0)  # -15% drawdown
    assert pm.can_open_trade(sector="IT") is False


# --- Helpers for backtest integration test ---

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


def test_backtest_skips_signal_when_portfolio_full() -> None:
    signals = pl.concat([
        _signal(symbol="A", signal_date=date(2026, 3, 2)),
        _signal(symbol="B", signal_date=date(2026, 3, 2)),
        _signal(symbol="C", signal_date=date(2026, 3, 2)),
    ], how="vertical")

    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": s, "open": 101.5, "high": 102.0, "low": 100.0, "close": 101.0}
        for s in ["A", "B", "C"]
    ] + [
        {"date": date(2026, 3, 4), "symbol": s, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5}
        for s in ["A", "B", "C"]
    ])

    pm = PortfolioManager(max_positions=2, initial_capital=1_000_000.0, risk_per_trade_pct=0.01)
    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), portfolio_manager=pm)

    statuses = [t.status for t in result.trades]
    assert statuses.count("skipped_portfolio") >= 1

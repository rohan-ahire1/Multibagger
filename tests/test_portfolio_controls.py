from __future__ import annotations

from datetime import date

import polars as pl
import pytest

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

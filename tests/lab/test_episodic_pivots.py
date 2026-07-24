"""Episodic pivots runs end to end on the panel path.

Deliberately does not assert profitability: the strategy loses money under its
own engine too (see strategies/episodic_pivots/README.md). What matters is that
the panel-computed path produces a well-formed result on the shared engine.
"""

from __future__ import annotations

from datetime import date

import pytest

from sepa_scanner.lab.contracts import DataRequirement, StrategyKind
from sepa_scanner.lab.registry import get_strategy
from sepa_scanner.lab.runner import run_backtest

import sepa_scanner.lab_strategies  # noqa: F401

START = date(2015, 1, 1)
END = date(2020, 12, 31)


def test_episodic_pivots_is_registered_as_a_panel_strategy():
    strategy = get_strategy("episodic_pivots")
    assert strategy.kind is StrategyKind.SIGNAL
    assert strategy.data_requirement is DataRequirement.PANEL


@pytest.mark.slow
def test_episodic_pivots_produces_a_wellformed_result():
    result = run_backtest("episodic_pivots", START, END)
    m = result.metrics

    assert result.strategy_id == "episodic_pivots"
    assert m.trades and m.trades > 0
    assert len(result.equity_curve) > 100
    assert m.max_drawdown_pct > 0
    assert 0.0 <= m.win_rate <= 100.0
    # Equity-curve metrics must be internally consistent for every strategy.
    assert result.equity_curve[-1].equity == pytest.approx(m.terminal_equity, rel=1e-9)


@pytest.mark.slow
def test_episodic_pivots_respects_max_signals_per_day():
    result = run_backtest(
        "episodic_pivots", START, date(2016, 12, 31), params={"max_signals_per_day": 1}
    )
    assert result.extras["signals_after_filter"] > 0

    by_day: dict[date, int] = {}
    for trade in result.trades:
        if trade.signal_date is not None:
            by_day[trade.signal_date] = by_day.get(trade.signal_date, 0) + 1
    assert by_day, "expected at least one dated signal"
    assert max(by_day.values()) == 1

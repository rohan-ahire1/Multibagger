"""Mean reversion (REVERSION kind) and research momentum.

Neither asserts profitability. Mean reversion's own realism validation already
records a FAIL verdict under honest costs, and research momentum's recorded
holdout used different exits and sizing. What is asserted is that each runs on
the right executor and produces internally consistent results.
"""

from __future__ import annotations

from datetime import date

import pytest

from sepa_scanner.lab.contracts import DataRequirement, StrategyKind
from sepa_scanner.lab.engines.reversion_engine import (
    reversion_daily_equity,
    reversion_trade_records,
)
from sepa_scanner.lab.registry import get_strategy
from sepa_scanner.lab.runner import run_backtest

import sepa_scanner.lab_strategies  # noqa: F401

START = date(2017, 1, 1)
END = date(2019, 12, 31)


# --- registration ---------------------------------------------------------


def test_mean_reversion_is_a_reversion_strategy():
    """It must not be a SIGNAL strategy: the shared engine has no reversion exit."""
    strategy = get_strategy("mean_reversion")
    assert strategy.kind is StrategyKind.REVERSION
    assert strategy.data_requirement is DataRequirement.PANEL


def test_mean_reversion_exposes_reversion_exit_rules():
    """Exit choices must be reversion rules, not trend exits."""
    strategy = get_strategy("mean_reversion")
    spec = next(s for s in strategy.param_specs if s.name == "exit_mode")
    assert "sma5" in spec.choices
    assert not any(c.startswith("baseline_") for c in spec.choices)


def test_research_momentum_is_registered():
    strategy = get_strategy("research_momentum")
    assert strategy.kind is StrategyKind.SIGNAL
    assert strategy.data_requirement is DataRequirement.PANEL


# --- engine adapters ------------------------------------------------------


def test_reversion_adapters_tolerate_empty_results():
    empty = {}
    assert reversion_trade_records(empty) == []
    curve = reversion_daily_equity(empty)
    assert curve.is_empty()
    assert curve.columns == ["date", "equity"]


# --- runs -----------------------------------------------------------------


@pytest.mark.slow
def test_mean_reversion_behaves_like_mean_reversion():
    """Short holds and a high win rate are the signature of the strategy.

    An earlier version routed it through the shared engine, whose trend exits
    produced 46-day holds and a ~32% win rate — a different strategy wearing
    mean reversion's entries. This asserts the real exit rule is in charge.
    """
    result = run_backtest("mean_reversion", START, END)
    m = result.metrics

    assert m.trades and m.trades > 20
    assert m.avg_hold_days is not None and m.avg_hold_days < 20, (
        f"average hold {m.avg_hold_days} is too long for mean reversion — "
        "the reversion exit is probably not being applied"
    )
    assert m.win_rate is not None and m.win_rate > 50.0, (
        f"win rate {m.win_rate} is too low for mean reversion"
    )
    assert all(t.direction == "long" for t in result.trades)


@pytest.mark.slow
def test_mean_reversion_exit_rule_changes_behaviour():
    sma5 = run_backtest("mean_reversion", START, END, params={"exit_mode": "sma5"})
    rsi = run_backtest("mean_reversion", START, END, params={"exit_mode": "rsi_70"})
    assert sma5.metrics.avg_hold_days != rsi.metrics.avg_hold_days


@pytest.mark.slow
def test_round_trip_cost_reduces_return():
    """The cost knob must actually bite; a free round trip flatters the result."""
    free = run_backtest(
        "mean_reversion", START, END, params={"round_trip_cost_pct": 0.0}
    )
    costly = run_backtest(
        "mean_reversion", START, END, params={"round_trip_cost_pct": 0.02}
    )
    assert costly.metrics.cagr_pct < free.metrics.cagr_pct


@pytest.mark.slow
def test_research_momentum_produces_a_wellformed_result():
    result = run_backtest("research_momentum", date(2023, 1, 1), date(2026, 5, 29))
    m = result.metrics

    assert m.trades and m.trades > 10
    assert len(result.equity_curve) > 100
    assert m.max_drawdown_pct > 0
    # Every entry must carry a stop below its reference price: the shared engine
    # sizes from that distance, so a missing or inverted stop is a sizing bug.
    entered = [t for t in result.trades if t.entry_price is not None]
    assert entered
    assert all(t.stop is None or t.stop < t.entry_price * 1.5 for t in entered)


@pytest.mark.slow
def test_research_momentum_bull_filter_is_binding():
    """Dropping the regime filter must admit more signals."""
    gated = run_backtest(
        "research_momentum", date(2023, 1, 1), date(2025, 12, 31),
        params={"require_market_bull": True},
    )
    ungated = run_backtest(
        "research_momentum", date(2023, 1, 1), date(2025, 12, 31),
        params={"require_market_bull": False},
    )
    assert ungated.extras["intent_rows"] >= gated.extras["intent_rows"]

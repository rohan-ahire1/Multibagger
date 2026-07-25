"""Daily factors: the REBALANCE kind, end to end.

Asserts the shape of a rebalance run and the invariants that keep it comparable
to signal strategies. It deliberately does *not* assert the recorded numbers in
strategies/daily_factors/output/: those were produced on the legacy
survivor-biased yfinance panel and are 3.5-7.5 CAGR points higher as a result.
See that directory's README for the full comparison.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.lab.contracts import StrategyKind
from sepa_scanner.lab.engines.rebalance_engine import (
    turnover_pct,
    weights_to_targets,
)
from sepa_scanner.lab.registry import get_strategy
from sepa_scanner.lab.runner import run_backtest

import sepa_scanner.lab_strategies  # noqa: F401

START = date(2015, 1, 1)
END = date(2019, 12, 31)


def test_registered_as_rebalance_kind():
    strategy = get_strategy("daily_factors")
    assert strategy.kind is StrategyKind.REBALANCE
    assert any(spec.name == "factor" for spec in strategy.param_specs)


def test_weights_to_targets_groups_by_date():
    weights = pl.DataFrame(
        {
            "rebalance_date": [date(2020, 1, 31), date(2020, 1, 31), date(2020, 2, 28)],
            "symbol": ["A", "B", "A"],
            "weight": [0.5, 0.5, 1.0],
        }
    )
    targets = weights_to_targets(weights)
    assert targets == {
        date(2020, 1, 31): {"A": 0.5, "B": 0.5},
        date(2020, 2, 28): {"A": 1.0},
    }


def test_turnover_is_none_without_orders():
    assert turnover_pct(pl.DataFrame(), 1_000_000.0) is None


@pytest.mark.slow
def test_rebalance_run_is_wellformed_and_omits_r_statistics():
    result = run_backtest(
        "daily_factors", START, END, params={"factor": "momentum_12_1_top20"}
    )
    m = result.metrics

    assert result.kind is StrategyKind.REBALANCE
    assert len(result.equity_curve) > 100
    assert m.terminal_equity > 0
    assert m.max_drawdown_pct > 0

    # R-multiples require a stop. A target-weight strategy has none, so these
    # must be absent rather than zero — a zero would read as a real measurement.
    assert m.win_rate is None
    assert m.avg_r is None
    assert m.expectancy_r is None
    assert m.payoff_ratio is None

    # Orders, and a turnover figure, are what this kind reports instead.
    assert m.trades and m.trades > 0
    assert m.turnover_pct and m.turnover_pct > 0
    assert all(t.r_multiple is None for t in result.trades)
    assert {t.meta["side"] for t in result.trades} <= {"buy", "sell"}


@pytest.mark.slow
def test_weights_never_exceed_full_allocation():
    """The runner's validator would reject over-allocation; prove we stay under."""
    result = run_backtest(
        "daily_factors", START, END, params={"factor": "alpha_low_vol_top30"}
    )
    assert result.extras["intent_rows"] > 0


@pytest.mark.slow
def test_weight_cap_is_respected():
    strategy = get_strategy("daily_factors")
    from sepa_scanner.lab.contracts import StrategyContext
    from sepa_scanner.lab.panel import load_panel_bundle
    from sepa_scanner.lab.registry import resolve_params

    bundle = load_panel_bundle(START, END)
    params = resolve_params(
        strategy, bundle.settings, {"factor": "alpha_low_vol_top30", "alpha_weight_cap": 0.05}
    )
    ctx = StrategyContext(
        settings=bundle.settings,
        start_date=START,
        end_date=END,
        data_dir=bundle.settings.runtime.data_dir,
        sector_map=bundle.sector_map,
        panel=bundle.ohlcv,
        benchmark=bundle.benchmark,
        extras={"params": bundle.params, "portfolio": bundle.portfolio},
    )
    weights = strategy.generate(ctx, params)
    assert weights.height > 0
    assert weights["weight"].max() <= 0.05 + 1e-9


@pytest.mark.slow
def test_factor_choice_changes_the_result():
    """The factor parameter must actually select a different portfolio."""
    momentum = run_backtest(
        "daily_factors", START, END, params={"factor": "momentum_12_1_top20"}
    )
    low_vol = run_backtest(
        "daily_factors", START, END, params={"factor": "alpha_low_vol_top30"}
    )
    assert momentum.metrics.cagr_pct != low_vol.metrics.cagr_pct
    assert momentum.params_hash != low_vol.params_hash

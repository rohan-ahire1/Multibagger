from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.daily_factors.engine import (
    ResearchConfig,
    _cap_weights,
    _month_end_signal_dates,
    _percentiles,
    simulate_rebalanced_portfolio,
)


def test_month_end_dates_exclude_truncated_final_month() -> None:
    calendar = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 15)]
    assert _month_end_signal_dates(calendar, date(2024, 1, 1), date(2024, 2, 10)) == [date(2024, 1, 31)]


def test_percentiles_reward_requested_direction() -> None:
    values = np.array([10.0, 30.0, 20.0])
    assert _percentiles(values).tolist() == [0.0, 1.0, 0.5]
    assert _percentiles(values, higher_is_better=False).tolist() == [1.0, 0.0, 0.5]


def test_weight_cap_is_respected_after_redistribution() -> None:
    weights = _cap_weights({f"S{i}": float(10 - i) for i in range(10)}, 0.15)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert max(weights.values()) <= 0.15 + 1e-12


def test_weight_cap_rejects_an_infeasible_portfolio() -> None:
    with pytest.raises(ValueError, match="infeasible"):
        _cap_weights({"A": 1.0, "B": 1.0}, 0.40)


def test_simulator_executes_signal_at_next_open_with_canonical_friction() -> None:
    panel = pl.DataFrame(
        {
            "date": [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)],
            "symbol": ["AAA", "AAA", "AAA"],
            "open": [100.0, 100.0, 110.0],
            "close": [100.0, 110.0, 110.0],
        }
    )
    config = ResearchConfig(start=date(2024, 1, 31), end=date(2024, 2, 2), initial_capital=1_000.0)
    result = simulate_rebalanced_portfolio(
        "test",
        {date(2024, 1, 31): {"AAA": 1.0}},
        panel,
        [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)],
        config,
    )
    assert result.orders.height == 1
    assert result.orders["execution_date"][0] == date(2024, 2, 1)
    assert result.orders["shares"][0] == 9
    assert result.daily_equity.filter(pl.col("date") == date(2024, 2, 1))["positions"][0] == 1

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_signal_quality_research import _future_price_windows, _oracle_exit_bound


def test_future_price_windows_uses_only_current_and_later_bars() -> None:
    frame = pl.DataFrame(
        {
            "date": [date(2020, 1, 1) + timedelta(days=i) for i in range(5)],
            "symbol": ["X"] * 5,
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
            "high": [10.0, 20.0, 12.0, 30.0, 14.0],
        }
    )

    result = _future_price_windows(frame, ["X"], horizon=3).sort("date")

    assert result["future_max_high_120d"].to_list() == [20.0, 30.0, 30.0, None, None]
    assert result["window_end_120d"].to_list()[:3] == [
        date(2020, 1, 3),
        date(2020, 1, 4),
        date(2020, 1, 5),
    ]


def test_oracle_bound_stops_before_using_high_on_stop_collision_bar() -> None:
    trades = pl.DataFrame(
        {
            "symbol": ["X"],
            "signal_date": [date(2020, 1, 1)],
            "status": ["intraday_stop"],
            "entry_date": [date(2020, 1, 2)],
            "exit_date": [date(2020, 1, 4)],
            "entry_price": [100.0],
            "stop": [90.0],
            "initial_risk_price": [10.0],
            "shares": [100.0],
            "pnl": [-1000.0],
            "r_multiple": [-1.0],
        }
    )
    prices = pl.DataFrame(
        {
            "date": [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4)],
            "symbol": ["X"] * 3,
            "open": [100.0, 101.0, 105.0],
            "high": [101.0, 110.0, 150.0],
            "low": [99.0, 95.0, 85.0],
            "close": [100.0, 108.0, 120.0],
            "volume": [1_000] * 3,
        }
    )

    summary, rows = _oracle_exit_bound(
        trades,
        prices,
        date(2020, 1, 1),
        date(2020, 12, 31),
        10_000.0,
    )

    assert rows["feasible_peak"].item() == 110.0
    assert rows["feasible_oracle_r"].item() == 1.0
    assert rows["feasible_oracle_pnl"].item() == 1_000.0
    assert summary["trades"] == 1

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.indicators.highs_lows import add_pivot_column


def test_pivot_excludes_current_day() -> None:
    start = date(2026, 3, 2)
    frame = pl.DataFrame(
        {
            "symbol": ["RELIANCE"] * 5,
            "date": [start + timedelta(days=offset) for offset in range(5)],
            "high": [10.0, 11.0, 12.0, 20.0, 15.0],
        }
    )

    result = add_pivot_column(frame, base_lookback=3)

    assert result["pivot"].to_list() == [None, None, None, 12.0, 20.0]

from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.data_sources.fundamentals import compute_earnings_acceleration


def test_earnings_acceleration_detects_accelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC", "ABC"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 15.0],
        "revenue": [100.0, 120.0, 150.0],
        "yoy_eps_growth": [0.15, 0.20, 0.30],  # accelerating: 15% -> 20% -> 30%
    })

    result = compute_earnings_acceleration(earnings)
    abc = result.filter(pl.col("symbol") == "ABC")
    assert abc["passes_filter"][0] is True
    assert abc["acceleration"][0] > 0


def test_earnings_acceleration_rejects_decelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ", "XYZ"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 13.0],
        "revenue": [100.0, 120.0, 130.0],
        "yoy_eps_growth": [0.30, 0.25, 0.22],  # decelerating: 30% -> 25% -> 22%
    })

    result = compute_earnings_acceleration(earnings)
    xyz = result.filter(pl.col("symbol") == "XYZ")
    assert xyz["passes_filter"][0] is False


def test_empty_earnings_returns_empty_result() -> None:
    result = compute_earnings_acceleration(pl.DataFrame(schema={
        "symbol": pl.String,
        "quarter_end": pl.Date,
        "eps": pl.Float64,
        "revenue": pl.Float64,
        "yoy_eps_growth": pl.Float64,
    }))
    assert result.is_empty()

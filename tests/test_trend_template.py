from __future__ import annotations

import polars as pl
import pytest

from sepa_scanner.settings import TrendTemplateSettings
from sepa_scanner.strategy.trend_template import add_trend_template_columns


def _base_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["RELIANCE"],
            "close": [100.0],
            "sma50": [95.0],
            "sma150": [90.0],
            "sma200": [80.0],
            "sma200_lag20": [70.0],
            "low52": [70.0],
            "high52": [120.0],
        }
    )


def _config() -> TrendTemplateSettings:
    return TrendTemplateSettings(
        sma_fast=50,
        sma_mid=150,
        sma_slow=200,
        lookback_52w=252,
        sma200_slope_lookback=20,
        min_above_52w_low_pct=0.30,
        max_below_52w_high_pct=0.25,
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("close", 89.0),
        ("close", 79.0),
        ("sma150", 79.0),
        ("sma200_lag20", 81.0),
        ("sma50", 89.0),
        ("sma50", 79.0),
        ("close", 94.0),
        ("low52", 80.0),
        ("high52", 140.0),
    ],
)
def test_trend_template_rules_toggle_independently(column: str, value: float) -> None:
    frame = _base_frame().with_columns(pl.lit(value).alias(column))

    result = add_trend_template_columns(frame, _config())

    assert result["trend_template_pass"].to_list() == [False]


def test_trend_template_passes_when_all_conditions_hold() -> None:
    result = add_trend_template_columns(_base_frame(), _config())

    assert result["trend_template_pass"].to_list() == [True]

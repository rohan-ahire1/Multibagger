from __future__ import annotations

import polars as pl
import pytest

from sepa_scanner.settings import SetupVcpSettings
from sepa_scanner.strategy.vcp_setup import add_setup_columns


def _config() -> SetupVcpSettings:
    return SetupVcpSettings(
        base_lookback=40,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
    )


def _base_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["RELIANCE"],
            "range10": [0.08],
            "voldry": [0.50],
            "atr_pct": [0.020],
            "atr_pct_lag20": [0.030],
            "atr_pct_sma10": [0.020],
            "atr_pct_sma20": [0.025],
            "atr_pct_sma30": [0.030],
            "close": [100.0],
            "sma50": [95.0],
            "base_depth": [0.20],
        }
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("range10", 0.11),
        ("voldry", 0.61),
        ("atr_pct_sma10", 0.026),
        ("atr_pct_sma30", 0.020),
        ("close", 106.0),
        ("base_depth", 0.36),
    ],
)
def test_setup_gate_rules_toggle_independently(column: str, value: float) -> None:
    frame = _base_frame().with_columns(pl.lit(value).alias(column))

    result = add_setup_columns(frame, _config())

    assert result["setup_pass"].to_list() == [False]


def test_setup_gate_passes_when_all_conditions_hold() -> None:
    result = add_setup_columns(_base_frame(), _config())

    assert result["setup_pass"].to_list() == [True]


def test_contraction_gate_inactive_when_min_count_zero() -> None:
    """With contraction_min_count=0, contraction_count doesn't affect setup_pass."""
    frame = _base_frame().with_columns(pl.lit(0).cast(pl.Int32).alias("contraction_count"))
    cfg = SetupVcpSettings(
        base_lookback=65,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
        contraction_min_count=0,
    )
    result = add_setup_columns(frame, cfg)
    assert result["setup_pass"].to_list() == [True]


def test_contraction_gate_active_rejects_low_count() -> None:
    """With contraction_min_count=2, a stock with count=1 fails setup_pass."""
    frame = _base_frame().with_columns(pl.lit(1).cast(pl.Int32).alias("contraction_count"))
    cfg = SetupVcpSettings(
        base_lookback=40,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
        contraction_min_count=2,
    )
    result = add_setup_columns(frame, cfg)
    assert result["setup_pass"].to_list() == [False]
    assert result["contraction_ok"].to_list() == [False]


def test_contraction_gate_active_passes_sufficient_count() -> None:
    """With contraction_min_count=2, a stock with count=3 passes."""
    frame = _base_frame().with_columns(pl.lit(3).cast(pl.Int32).alias("contraction_count"))
    cfg = SetupVcpSettings(
        base_lookback=40,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
        contraction_min_count=2,
    )
    result = add_setup_columns(frame, cfg)
    assert result["setup_pass"].to_list() == [True]
    assert result["contraction_ok"].to_list() == [True]


def test_contraction_tier_premium_standard_low() -> None:
    """contraction_tier is advisory: premium >= 3, standard >= 2, low < 2."""
    cfg = SetupVcpSettings(
        base_lookback=40,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
        contraction_min_count=2,
        contraction_standard_count=3,
    )

    base = _base_frame()

    # count=4 -> premium
    frame_4 = base.with_columns(pl.lit(4).cast(pl.Int32).alias("contraction_count"))
    result_4 = add_setup_columns(frame_4, cfg)
    assert result_4["contraction_tier"].to_list() == ["premium"]

    # count=2 -> standard
    frame_2 = base.with_columns(pl.lit(2).cast(pl.Int32).alias("contraction_count"))
    result_2 = add_setup_columns(frame_2, cfg)
    assert result_2["contraction_tier"].to_list() == ["standard"]

    # count=1 -> low
    frame_1 = base.with_columns(pl.lit(1).cast(pl.Int32).alias("contraction_count"))
    result_1 = add_setup_columns(frame_1, cfg)
    assert result_1["contraction_tier"].to_list() == ["low"]


def test_contraction_tier_does_not_affect_setup_pass() -> None:
    """contraction_tier is advisory only -- setup_pass depends only on contraction_min_count."""
    cfg = SetupVcpSettings(
        base_lookback=40,
        max_range10_pct=0.10,
        max_vol_dryup_ratio=0.60,
        atr_period=14,
        atr_contraction_factor=0.75,
        max_extension_from_50sma_pct=0.10,
        base_max_depth_pct=0.35,
        pre_alert_proximity_pct=0.03,
        contraction_min_count=2,
        contraction_standard_count=3,
    )

    base = _base_frame()

    # count=2: standard tier but setup still passes (meets min_count=2)
    frame = base.with_columns(pl.lit(2).cast(pl.Int32).alias("contraction_count"))
    result = add_setup_columns(frame, cfg)
    assert result["setup_pass"].to_list() == [True]
    assert result["contraction_tier"].to_list() == ["standard"]

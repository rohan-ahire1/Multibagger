from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.strategy.market_filter import market_filter_on


def _benchmark_frame(
    close: float = 200.0,
    sma50: float = 180.0,
    sma200: float = 160.0,
    sma200_lag20: float = 155.0,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["NIFTYBEES"],
            "date": [date(2026, 3, 14)],
            "close": [close],
            "sma50": [sma50],
            "sma200": [sma200],
            "sma200_lag20": [sma200_lag20],
        }
    )


SCAN_DATE = date(2026, 3, 14)


def test_secular_conditions_pass_alone() -> None:
    frame = _benchmark_frame()
    assert market_filter_on(frame, "NIFTYBEES", SCAN_DATE) is True


def test_secular_condition_fails_close_below_sma200() -> None:
    frame = _benchmark_frame(close=150.0)
    assert market_filter_on(frame, "NIFTYBEES", SCAN_DATE) is False


def test_layer2_cycle_ema_caps_at_neutral_when_close_below_ema() -> None:
    """EMA fail with no breadth data: graduated filter returns NEUTRAL (not WEAK),
    so market_filter_on is True. This intentionally relaxes old behavior."""
    frame = _benchmark_frame(close=200.0)
    assert market_filter_on(
        frame, "NIFTYBEES", SCAN_DATE, benchmark_ema=210.0
    ) is True


def test_layer2_cycle_ema_passes_when_close_above_ema() -> None:
    frame = _benchmark_frame(close=200.0)
    assert market_filter_on(
        frame, "NIFTYBEES", SCAN_DATE, benchmark_ema=190.0
    ) is True


def test_layer3_breadth_rejects_when_well_below_threshold() -> None:
    """Breadth must be below breadth_weak_pct (require_breadth_pct - 0.10) to reject.
    breadth_pct=0.35 < 0.40 (weak threshold) -> WEAK -> False."""
    frame = _benchmark_frame()
    assert market_filter_on(
        frame, "NIFTYBEES", SCAN_DATE, breadth_pct=0.35, require_breadth_pct=0.50
    ) is False


def test_layer3_breadth_neutral_zone_passes() -> None:
    """Breadth between weak and strong thresholds is NEUTRAL (not WEAK),
    so market_filter_on returns True. This intentionally relaxes old behavior."""
    frame = _benchmark_frame()
    assert market_filter_on(
        frame, "NIFTYBEES", SCAN_DATE, breadth_pct=0.45, require_breadth_pct=0.50
    ) is True


def test_layer3_breadth_passes_when_above_threshold() -> None:
    frame = _benchmark_frame()
    assert market_filter_on(
        frame, "NIFTYBEES", SCAN_DATE, breadth_pct=0.60, require_breadth_pct=0.50
    ) is True


def test_all_layers_pass() -> None:
    frame = _benchmark_frame()
    assert market_filter_on(
        frame,
        "NIFTYBEES",
        SCAN_DATE,
        benchmark_ema=190.0,
        breadth_pct=0.55,
        require_breadth_pct=0.50,
    ) is True


def test_all_layers_secular_passes_but_ema_fails_breadth_neutral() -> None:
    """EMA fails but breadth is in neutral zone: graduated filter returns NEUTRAL,
    so market_filter_on is True. This intentionally relaxes old behavior."""
    frame = _benchmark_frame()
    assert market_filter_on(
        frame,
        "NIFTYBEES",
        SCAN_DATE,
        benchmark_ema=210.0,
        breadth_pct=0.55,
        require_breadth_pct=0.50,
    ) is True

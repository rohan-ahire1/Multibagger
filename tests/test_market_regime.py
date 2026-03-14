from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.strategy.market_filter import classify_market_regime
from sepa_scanner.strategy.market_regime import MarketRegime


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


def test_strong_regime_when_secular_and_breadth_above_60() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.STRONG


def test_neutral_regime_when_breadth_between_thresholds() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.50,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.NEUTRAL


def test_weak_regime_when_breadth_below_40() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.35,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.WEAK


def test_weak_regime_when_secular_fails() -> None:
    frame = _benchmark_frame(close=150.0)  # below sma200
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=190.0, breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.WEAK


def test_neutral_regime_when_ema_fails_but_breadth_strong() -> None:
    frame = _benchmark_frame()
    regime = classify_market_regime(
        frame, "NIFTYBEES", SCAN_DATE,
        benchmark_ema=210.0,  # close (200) below EMA
        breadth_pct=0.65,
        breadth_strong_pct=0.60, breadth_weak_pct=0.40,
    )
    assert regime == MarketRegime.NEUTRAL  # secular passes, EMA fails -> cap at NEUTRAL

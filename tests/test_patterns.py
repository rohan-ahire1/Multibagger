from __future__ import annotations

import numpy as np

from sepa_scanner.strategy.patterns import (
    PatternType,
    detect_flat_base,
    detect_high_tight_flag,
)


def test_htf_detects_doubling_with_tight_correction() -> None:
    n = 65  # 40 gain + 25 correction
    closes = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)

    # Gain phase: 50 -> 110 (120% gain)
    for i in range(40):
        closes[i] = 50.0 + (60.0 / 40) * i
        highs[i] = closes[i] + 1.0
        lows[i] = closes[i] - 1.0

    # Correction phase: 110 -> 95 (13.6% correction) then back up
    peak = 110.0
    for i in range(40, 65):
        offset = i - 40
        closes[i] = peak - 15.0 * np.sin(offset / 25 * np.pi)  # gentle correction
        closes[i] = max(closes[i], peak * 0.85)
        highs[i] = closes[i] + 1.0
        lows[i] = closes[i] - 1.0

    result = detect_high_tight_flag(closes, highs, lows)
    assert result is not None
    assert result.pattern == PatternType.HIGH_TIGHT_FLAG


def test_htf_rejects_insufficient_gain() -> None:
    n = 65
    closes = np.linspace(100, 140, n)  # only 40% gain
    highs = closes + 1.0
    lows = closes - 1.0

    result = detect_high_tight_flag(closes, highs, lows)
    assert result is None


def test_flat_base_detects_tight_consolidation() -> None:
    n = 30
    closes = np.full(n, 100.0) + np.random.default_rng(42).normal(0, 1, n)
    highs = closes + 2.0
    lows = closes - 2.0

    result = detect_flat_base(closes, highs, lows, max_depth_pct=0.15, min_duration=25)
    assert result is not None
    assert result.pattern == PatternType.FLAT_BASE

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl


class PatternType(str, Enum):
    VCP = "vcp"
    HIGH_TIGHT_FLAG = "htf"
    FLAT_BASE = "flat_base"
    # Future: CUP_WITH_HANDLE, DOUBLE_BOTTOM, IPO_BASE (add when implemented)


@dataclass(slots=True)
class PatternMatch:
    pattern: PatternType
    confidence: float        # 0-1
    base_depth_pct: float
    base_duration_days: int
    pivot_price: float


def detect_high_tight_flag(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    min_gain_pct: float = 1.0,
    gain_window: int = 40,
    max_correction_pct: float = 0.25,
    correction_window: int = 25,
) -> PatternMatch | None:
    """Detect High Tight Flag (Power Play) pattern.

    Requirements:
    - Stock doubles (>=100%) in gain_window days
    - Then corrects <=max_correction_pct over correction_window days
    """
    if len(closes) < gain_window + correction_window:
        return None

    # Check for doubling in the gain phase
    gain_start = len(closes) - gain_window - correction_window
    gain_end = len(closes) - correction_window
    gain_start_price = closes[gain_start]
    gain_peak = np.max(highs[gain_start:gain_end])

    if gain_start_price <= 0 or (gain_peak / gain_start_price - 1.0) < min_gain_pct:
        return None

    # Check correction phase
    correction_phase = closes[gain_end:]
    correction_low = np.min(lows[gain_end:])
    correction_depth = (gain_peak - correction_low) / gain_peak

    if correction_depth > max_correction_pct:
        return None

    return PatternMatch(
        pattern=PatternType.HIGH_TIGHT_FLAG,
        confidence=min(1.0, (gain_peak / gain_start_price - 1.0) / 2.0),
        base_depth_pct=correction_depth,
        base_duration_days=correction_window,
        pivot_price=gain_peak,
    )


def detect_flat_base(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    max_depth_pct: float = 0.15,
    min_duration: int = 25,
) -> PatternMatch | None:
    """Detect flat base pattern (<=15% depth, minimum 5 weeks)."""
    if len(closes) < min_duration:
        return None

    window = closes[-min_duration:]
    window_highs = highs[-min_duration:]
    window_lows = lows[-min_duration:]

    peak = np.max(window_highs)
    trough = np.min(window_lows)

    if peak <= 0:
        return None

    depth = (peak - trough) / peak
    if depth > max_depth_pct:
        return None

    return PatternMatch(
        pattern=PatternType.FLAT_BASE,
        confidence=1.0 - (depth / max_depth_pct),
        base_depth_pct=depth,
        base_duration_days=min_duration,
        pivot_price=peak,
    )


def detect_patterns(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> list[PatternMatch]:
    """Run all pattern detectors and return matches."""
    matches: list[PatternMatch] = []

    htf = detect_high_tight_flag(closes, highs, lows)
    if htf:
        matches.append(htf)

    flat = detect_flat_base(closes, highs, lows)
    if flat:
        matches.append(flat)

    return matches

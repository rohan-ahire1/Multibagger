from __future__ import annotations

import polars as pl
import pytest

from sepa_scanner.indicators.contraction import add_contraction_columns, _count_contractions

import numpy as np


def _make_frame(range10_values: list[float], symbol: str = "TEST") -> pl.DataFrame:
    """Build a minimal DataFrame with range10 values for one symbol."""
    n = len(range10_values)
    return pl.DataFrame(
        {
            "symbol": [symbol] * n,
            "date": pl.date_range(
                pl.date(2025, 1, 1),
                pl.date(2025, 1, 1) + pl.duration(days=n - 1),  # type: ignore[operator]
                eager=True,
            ).to_list()[:n],
            "range10": range10_values,
        }
    )


# ---------------------------------------------------------------------------
# Unit tests for _count_contractions (pure numpy function)
# ---------------------------------------------------------------------------


def test_flat_range_no_contractions() -> None:
    """Flat range10 series has no peaks, so contraction_count = 0."""
    arr = np.full(40, 0.05)
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=5, min_peak_pct=0.02)
    assert count == 0
    assert quality is None


def test_single_peak_one_contraction() -> None:
    """One rise-and-fall cycle gives contraction_count = 1."""
    # Build: low → peak → low
    arr = np.concatenate([
        np.linspace(0.03, 0.08, 10),  # rising
        np.linspace(0.08, 0.03, 10),  # falling
        np.full(20, 0.03),            # flat
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 1
    assert quality is None  # need >= 2 for quality ratio


def test_two_successive_contractions() -> None:
    """T1 > T2 pattern gives contraction_count = 2."""
    arr = np.concatenate([
        np.linspace(0.03, 0.10, 8),   # rise to T1
        np.linspace(0.10, 0.04, 8),   # fall from T1
        np.linspace(0.04, 0.07, 8),   # rise to T2 (lower than T1)
        np.linspace(0.07, 0.03, 8),   # fall from T2
        np.full(8, 0.03),             # flat tail
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 2
    assert quality is not None
    assert 0 < quality < 1.0  # T2/T1 should be < 1


def test_three_contractions_classic_vcp() -> None:
    """T1 > T2 > T3 gives contraction_count = 3."""
    arr = np.concatenate([
        np.linspace(0.03, 0.12, 7),   # rise to T1
        np.linspace(0.12, 0.04, 7),   # fall
        np.linspace(0.04, 0.08, 7),   # rise to T2
        np.linspace(0.08, 0.03, 7),   # fall
        np.linspace(0.03, 0.05, 6),   # rise to T3
        np.linspace(0.05, 0.03, 6),   # fall
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 3
    assert quality is not None
    assert 0 < quality < 1.0


def test_broken_chain_stops_counting() -> None:
    """T1 > T2 < T3 (third wider) stops count at 2."""
    arr = np.concatenate([
        np.linspace(0.03, 0.12, 8),   # rise to T1
        np.linspace(0.12, 0.03, 8),   # fall
        np.linspace(0.03, 0.08, 8),   # rise to T2 (lower)
        np.linspace(0.08, 0.03, 8),   # fall
        np.linspace(0.03, 0.10, 8),   # rise to T3 (higher than T2 — breaks chain)
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 2


def test_peaks_below_min_height_ignored() -> None:
    """Peaks below min_peak_pct are not counted."""
    arr = np.concatenate([
        np.linspace(0.005, 0.015, 20),  # tiny rise — below 0.02 threshold
        np.linspace(0.015, 0.005, 20),  # tiny fall
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 0


def test_quality_ratio_computed_correctly() -> None:
    """Verify quality is avg of T(n+1)/T(n)."""
    # Two very clear peaks: T1=0.10, T2=0.06 → ratio = 0.6
    arr = np.concatenate([
        np.linspace(0.02, 0.10, 10),
        np.linspace(0.10, 0.02, 10),
        np.linspace(0.02, 0.06, 10),
        np.linspace(0.06, 0.02, 10),
    ])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 2
    assert quality is not None
    assert 0.4 < quality < 0.8  # roughly 0.6, allow smoothing variance


def test_insufficient_data_returns_zero() -> None:
    """Short series returns count=0."""
    arr = np.array([0.05, 0.06])
    count, quality, _prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 0
    assert quality is None


# ---------------------------------------------------------------------------
# Integration tests for add_contraction_columns (Polars DataFrame)
# ---------------------------------------------------------------------------


def test_add_contraction_columns_basic() -> None:
    """Columns are added with correct types."""
    arr = np.concatenate([
        np.linspace(0.03, 0.10, 20),
        np.linspace(0.10, 0.03, 20),
    ])
    frame = _make_frame(arr.tolist())
    result = add_contraction_columns(frame, base_lookback=40, smooth_window=3, min_peak_pct=0.02)

    assert "contraction_count" in result.columns
    assert "contraction_quality" in result.columns
    assert result["contraction_count"].dtype == pl.Int32
    assert result["contraction_quality"].dtype == pl.Float64


def test_multiple_symbols_isolated() -> None:
    """Contraction counts are computed independently per symbol."""
    # Symbol A: classic VCP with 2 contractions
    arr_a = np.concatenate([
        np.linspace(0.03, 0.10, 10),
        np.linspace(0.10, 0.04, 10),
        np.linspace(0.04, 0.07, 10),
        np.linspace(0.07, 0.03, 10),
    ])
    # Symbol B: flat (no contractions)
    arr_b = np.full(40, 0.05)

    frame_a = _make_frame(arr_a.tolist(), symbol="AAA")
    frame_b = _make_frame(arr_b.tolist(), symbol="BBB")
    frame = pl.concat([frame_a, frame_b])

    result = add_contraction_columns(frame, base_lookback=40, smooth_window=3, min_peak_pct=0.02)

    # Get last row per symbol
    last_rows = result.group_by("symbol", maintain_order=True).tail(1).sort("symbol")
    counts = dict(zip(
        last_rows["symbol"].to_list(),
        last_rows["contraction_count"].to_list(),
    ))
    assert counts["AAA"] >= 2
    assert counts["BBB"] == 0


def test_contraction_rejects_non_progressive_sequence() -> None:
    """A sequence like 12% -> 15% -> 8% should NOT count as 3 contractions."""
    # Create range10 series with non-progressive contractions
    # Peak 1: 0.12, Peak 2: 0.15 (wider!), Peak 3: 0.08
    series = np.array([
        0.05, 0.08, 0.12, 0.10, 0.06,  # contraction 1: peak at 0.12
        0.08, 0.12, 0.15, 0.12, 0.07,  # contraction 2: peak at 0.15 (NOT narrowing)
        0.05, 0.06, 0.08, 0.06, 0.04,  # contraction 3: peak at 0.08
    ])

    count, quality, _prog, _seq = _count_contractions(series, smooth_window=3, min_peak_pct=0.02)
    # Should be 1 (first peak only), since second peak breaks the chain
    assert count <= 2, "Non-progressive peaks should break the contraction chain"


def test_progressive_flag_true_for_classic_vcp() -> None:
    """A true VCP with well-separated contractions should be progressive."""
    arr = np.concatenate([
        np.linspace(0.03, 0.12, 7),   # rise to T1
        np.linspace(0.12, 0.04, 7),   # fall
        np.linspace(0.04, 0.06, 7),   # rise to T2 (50% of T1 - well progressive)
        np.linspace(0.06, 0.03, 7),   # fall
    ])
    count, quality, prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 2
    assert prog is True, "Clear 50% contraction should be progressive"


def test_progressive_flag_false_for_marginal_contraction() -> None:
    """Contractions that barely narrow (< 20%) should not be progressive."""
    arr = np.concatenate([
        np.linspace(0.03, 0.10, 8),   # rise to T1 = 0.10
        np.linspace(0.10, 0.04, 8),   # fall
        np.linspace(0.04, 0.09, 8),   # rise to T2 = 0.09 (only 10% smaller)
        np.linspace(0.09, 0.03, 8),   # fall
    ])
    count, quality, prog, _seq = _count_contractions(arr, smooth_window=3, min_peak_pct=0.02)
    assert count == 2
    assert prog is False, "Only 10% narrowing should not be progressive"


def test_add_contraction_columns_includes_progressive() -> None:
    """contraction_progressive column is added by add_contraction_columns."""
    arr = np.concatenate([
        np.linspace(0.03, 0.10, 20),
        np.linspace(0.10, 0.03, 20),
    ])
    frame = _make_frame(arr.tolist())
    result = add_contraction_columns(frame, base_lookback=40, smooth_window=3, min_peak_pct=0.02)
    assert "contraction_progressive" in result.columns
    assert result["contraction_progressive"].dtype == pl.Boolean

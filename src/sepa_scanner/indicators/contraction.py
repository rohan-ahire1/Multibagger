"""VCP contraction sequence detection.

Detects successive narrowing contractions (T1 > T2 > T3) in the range10
time series — the hallmark of a true Volatility Contraction Pattern.
"""

from __future__ import annotations

import numpy as np
import polars as pl


def _count_contractions(
    range10: np.ndarray,
    smooth_window: int,
    min_peak_pct: float,
) -> tuple[int, float | None, bool, list[float]]:
    """Count successive narrowing peaks in a range10 series.

    Returns (contraction_count, contraction_quality, is_progressive, contraction_sequence).
    contraction_quality is the average T(n+1)/T(n) ratio, or None if count < 2.
    is_progressive is True if every successive peak is at least 20% smaller.
    """
    if len(range10) < smooth_window + 2:
        return 0, None, False, []

    # Step 1: smooth range10 with a simple moving average (valid mode to avoid edge artifacts)
    kernel = np.ones(smooth_window) / smooth_window
    smoothed_valid = np.convolve(range10, kernel, mode="valid")
    # Offset: valid output starts at index (smooth_window - 1) // 2 conceptually,
    # but the valid array has len(range10) - smooth_window + 1 elements.
    # We work on the valid portion only to avoid edge distortion.
    smoothed = smoothed_valid

    # Step 2: find local peaks (derivative goes from positive to negative)
    # Require minimum prominence: peak must rise at least min_peak_pct/2 above
    # the lower of its two neighboring troughs to filter noise.
    diff = np.diff(smoothed)
    peaks: list[tuple[int, float]] = []
    for i in range(1, len(diff)):
        if diff[i - 1] > 0 and diff[i] <= 0:
            peak_val = smoothed[i]
            if peak_val < min_peak_pct:
                continue
            # Check prominence: find nearest troughs on each side
            left_trough = np.min(smoothed[max(0, i - smooth_window) : i])
            right_trough = np.min(smoothed[i : min(len(smoothed), i + smooth_window + 1)])
            prominence = peak_val - max(left_trough, right_trough)
            if prominence > min_peak_pct * 0.25:
                peaks.append((i, peak_val))

    if not peaks:
        return 0, None, False, []

    if len(peaks) == 1:
        return 1, None, True, [float(peaks[0][1] * 100)]

    # Step 3: count successive narrowing peaks working backwards from the most recent peak
    progressive = True
    count = 1
    ratios: list[float] = []
    for j in range(len(peaks) - 2, -1, -1):
        if peaks[j + 1][1] < peaks[j][1]:
            ratio = peaks[j + 1][1] / peaks[j][1]
            count += 1
            ratios.append(ratio)
            if ratio > 0.80:  # Less than 20% smaller -- not truly progressive
                progressive = False
        else:
            break  # chain broken

    # Step 4: compute quality (average ratio)
    quality = sum(ratios) / len(ratios) if ratios else None

    # Step 5: extract the sequence of peaks in the contracting chain (as percentages)
    sequence = [float(p[1] * 100) for p in peaks[len(peaks) - count:]]

    return count, quality, progressive, sequence


def add_contraction_columns(
    frame: pl.DataFrame,
    base_lookback: int,
    smooth_window: int = 5,
    min_peak_pct: float = 0.02,
) -> pl.DataFrame:
    """Add contraction_count and contraction_quality columns to the frame.

    Operates on the ``range10`` column (must already exist) within the last
    ``base_lookback`` bars per symbol.
    """

    def _process_group(group_df: pl.DataFrame) -> pl.DataFrame:
        range10_series = group_df["range10"].to_numpy()
        n = len(range10_series)

        counts = np.zeros(n, dtype=np.int32)
        qualities = np.full(n, np.nan, dtype=np.float64)
        progressives = np.zeros(n, dtype=np.bool_)

        for i in range(base_lookback - 1, n):
            window = range10_series[max(0, i - base_lookback + 1) : i + 1]
            # Skip windows with nulls
            if np.any(np.isnan(window)):
                continue
            c, q, prog, _seq = _count_contractions(window, smooth_window, min_peak_pct)
            counts[i] = c
            if q is not None:
                qualities[i] = q
            progressives[i] = prog

        return group_df.with_columns(
            pl.Series("contraction_count", counts, dtype=pl.Int32),
            pl.Series("contraction_quality", qualities, dtype=pl.Float64),
            pl.Series("contraction_progressive", progressives, dtype=pl.Boolean),
        )

    # Handle null range10 values by filling with NaN for numpy
    frame = frame.with_columns(pl.col("range10").fill_null(float("nan")))

    result = frame.group_by("symbol", maintain_order=True).map_groups(_process_group)

    # Convert NaN back to null for contraction_quality
    result = result.with_columns(
        pl.when(pl.col("contraction_quality").is_nan())
        .then(None)
        .otherwise(pl.col("contraction_quality"))
        .alias("contraction_quality")
    )

    return result

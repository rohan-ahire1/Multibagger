from __future__ import annotations

from collections.abc import Sequence

import polars as pl


def wilder_atr(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    if not (len(high) == len(low) == len(close)):
        raise ValueError("high, low, and close must have identical lengths")
    if period <= 0:
        raise ValueError("period must be positive")
    if len(high) < period:
        return [None] * len(high)

    tr_values: list[float] = []
    for index, (high_value, low_value, close_value) in enumerate(zip(high, low, close, strict=True)):
        if index == 0:
            tr_values.append(high_value - low_value)
            continue
        prev_close = close[index - 1]
        tr_values.append(
            max(
                high_value - low_value,
                abs(high_value - prev_close),
                abs(low_value - prev_close),
            )
        )

    result: list[float | None] = [None] * len(high)
    initial_atr = sum(tr_values[:period]) / period
    result[period - 1] = initial_atr
    previous = initial_atr
    for index in range(period, len(tr_values)):
        previous = ((previous * (period - 1)) + tr_values[index]) / period
        result[index] = previous
    return result


def add_atr_columns(frame: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    alpha = 1.0 / period
    prev_close = pl.col("close").shift(1).over("symbol")
    tr_expr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close).abs(),
        (pl.col("low") - prev_close).abs(),
    )
    return (
        frame.with_columns(tr_expr.alias("_tr"))
        .with_columns(
            pl.col("_tr")
            .ewm_mean(alpha=alpha, adjust=False, min_samples=period)
            .over("symbol")
            .alias("atr"),
        )
        .drop("_tr")
        .with_columns((pl.col("atr") / pl.col("close")).alias("atr_pct"))
    )

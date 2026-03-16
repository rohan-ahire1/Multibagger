from __future__ import annotations

import polars as pl


def add_pivot_column(frame: pl.DataFrame, base_lookback: int) -> pl.DataFrame:
    return frame.sort(["symbol", "date"]).with_columns(
        pl.col("high")
        .rolling_max(window_size=base_lookback, min_samples=base_lookback)
        .shift(1)
        .over("symbol")
        .alias("pivot")
    )


def rolling_high_expr(column: str, window: int) -> pl.Expr:
    return pl.col(column).rolling_max(window_size=window, min_samples=window)


def rolling_low_expr(column: str, window: int) -> pl.Expr:
    return pl.col(column).rolling_min(window_size=window, min_samples=window)

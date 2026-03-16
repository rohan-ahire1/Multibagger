from __future__ import annotations

import polars as pl


def add_tightness_columns(frame: pl.DataFrame, base_lookback: int) -> pl.DataFrame:
    return frame.with_columns(
        (
            (
                pl.col("high").rolling_max(window_size=10, min_samples=10).over("symbol")
                - pl.col("low").rolling_min(window_size=10, min_samples=10).over("symbol")
            )
            / pl.col("close")
        ).alias("range10"),
        pl.col("high")
        .rolling_max(window_size=base_lookback, min_samples=base_lookback)
        .over("symbol")
        .alias("base_high"),
        pl.col("low")
        .rolling_min(window_size=base_lookback, min_samples=base_lookback)
        .over("symbol")
        .alias("base_low"),
    ).with_columns(
        ((pl.col("base_high") - pl.col("base_low")) / pl.col("base_high")).alias("base_depth")
    )

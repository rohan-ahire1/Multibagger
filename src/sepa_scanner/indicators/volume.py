from __future__ import annotations

import polars as pl

from sepa_scanner.indicators.sma import sma_expr


def add_volume_columns(frame: pl.DataFrame) -> pl.DataFrame:
    traded_value = pl.coalesce([pl.col("turnover"), pl.col("close") * pl.col("volume")])
    return (
        frame.with_columns(traded_value.alias("traded_value"))
        .with_columns(
            sma_expr("volume", 10).over("symbol").alias("vol10"),
            sma_expr("volume", 50).over("symbol").alias("vol50"),
            sma_expr("traded_value", 50).over("symbol").alias("adv50"),
        )
        .with_columns(
        (pl.col("vol10") / pl.col("vol50")).alias("voldry"),
    )
    )

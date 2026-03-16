from __future__ import annotations

import polars as pl


def valid_ohlcv_rows() -> pl.Expr:
    return (
        pl.col("date").is_not_null()
        & pl.col("symbol").is_not_null()
        & (pl.col("symbol").str.len_chars() > 0)
        & (pl.col("close") > 0)
        & (pl.col("high") >= pl.col("low"))
        & (pl.col("volume") >= 0)
    )

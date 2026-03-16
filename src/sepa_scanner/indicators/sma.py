from __future__ import annotations

import polars as pl


def sma_expr(column: str, window: int, min_periods: int | None = None) -> pl.Expr:
    return pl.col(column).rolling_mean(window_size=window, min_samples=min_periods or window)

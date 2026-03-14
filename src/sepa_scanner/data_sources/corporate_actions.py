from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_corporate_actions_csv(path: Path) -> pl.DataFrame:
    """Load corporate actions from a CSV file."""
    return pl.read_csv(
        path,
        schema_overrides={
            "ex_date": pl.Date,
            "ratio_num": pl.Int32,
            "ratio_den": pl.Int32,
            "old_face_value": pl.Float64,
            "new_face_value": pl.Float64,
            "adjustment_factor": pl.Float64,
        },
        null_values=[""],
    ).sort(["symbol", "ex_date"])


def has_split_in_window(
    symbol: str,
    start_date: date,
    end_date: date,
    corp_actions: pl.DataFrame,
) -> bool:
    """Check if any split/bonus ex_date falls strictly between start_date (exclusive)
    and end_date (inclusive).

    Boundary choice: start_date is exclusive because if the signal fires on the same
    day as the split, prices are already post-split. end_date is inclusive to catch
    splits that happen on the last day of the window.
    """
    if corp_actions.is_empty():
        return False
    return (
        corp_actions.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("action_type").is_in(["split", "bonus"]))
            & (pl.col("ex_date") > pl.lit(start_date))
            & (pl.col("ex_date") <= pl.lit(end_date))
        ).height
        > 0
    )


def compute_adjustment_factors(
    ohlcv: pl.LazyFrame,
    corp_actions: pl.DataFrame,
) -> pl.LazyFrame:
    """Compute per-(symbol, date) cumulative adjustment factor.

    For each date, adj_factor = product of all split/bonus factors
    with ex_date AFTER that date.
    On and after the most recent ex_date, adj_factor = 1.0.

    Note: collects the lazy frame eagerly because the corp_actions table is small
    (~10-100 rows). The join is O(dates x actions_per_symbol), which is negligible.
    """
    if corp_actions.is_empty():
        return ohlcv.select("symbol", "date").with_columns(
            pl.lit(1.0).alias("adj_factor")
        )

    dates_df = ohlcv.select("symbol", "date").collect()
    actions = corp_actions.select("symbol", "ex_date", "adjustment_factor")

    joined = dates_df.join(actions, on="symbol", how="left", suffix="_ca")

    result = (
        joined.with_columns(
            pl.when(pl.col("ex_date").is_not_null() & (pl.col("ex_date") > pl.col("date")))
            .then(pl.col("adjustment_factor"))
            .otherwise(1.0)
            .alias("factor_contrib")
        )
        .group_by(["symbol", "date"])
        .agg(pl.col("factor_contrib").product().alias("adj_factor"))
        .sort(["symbol", "date"])
    )

    return result.lazy()


def build_adjusted_ohlcv(
    raw_ohlcv: pl.LazyFrame,
    adj_factors: pl.LazyFrame,
) -> pl.LazyFrame:
    """Apply adjustment factors to raw OHLCV.
    Preserves raw_close and raw_volume for display.

    In Polars with_columns, all expressions read from pre-mutation column values,
    so raw_close correctly captures the original close before adjustment.
    """
    return (
        raw_ohlcv.join(adj_factors, on=["symbol", "date"], how="left")
        .with_columns(pl.col("adj_factor").fill_null(1.0))
        .with_columns(
            pl.col("close").alias("raw_close"),
            pl.col("volume").alias("raw_volume"),
            (pl.col("open") * pl.col("adj_factor")).alias("open"),
            (pl.col("high") * pl.col("adj_factor")).alias("high"),
            (pl.col("low") * pl.col("adj_factor")).alias("low"),
            (pl.col("close") * pl.col("adj_factor")).alias("close"),
            (pl.col("volume") / pl.col("adj_factor")).cast(pl.Int64).alias("volume"),
        )
        .drop("adj_factor")
    )

from __future__ import annotations

import polars as pl

from sepa_scanner.settings import TrendTemplateSettings


def add_trend_template_columns(
    frame: pl.DataFrame, cfg: TrendTemplateSettings
) -> pl.DataFrame:
    return frame.with_columns(
        (pl.col("close") > pl.col("sma150")).alias("tt_close_above_sma150"),
        (pl.col("close") > pl.col("sma200")).alias("tt_close_above_sma200"),
        (pl.col("sma150") > pl.col("sma200")).alias("tt_sma150_above_sma200"),
        (pl.col("sma200") > pl.col("sma200_lag20")).alias("tt_sma200_rising"),
        (pl.col("sma50") > pl.col("sma150")).alias("tt_sma50_above_sma150"),
        (pl.col("sma50") > pl.col("sma200")).alias("tt_sma50_above_sma200"),
        (pl.col("close") > pl.col("sma50")).alias("tt_close_above_sma50"),
        (
            pl.col("close")
            >= pl.col("low52") * (1.0 + cfg.min_above_52w_low_pct)
        ).alias("tt_above_52w_low"),
        (
            pl.col("close")
            >= pl.col("high52") * (1.0 - cfg.max_below_52w_high_pct)
        ).alias("tt_near_52w_high"),
    ).with_columns(
        pl.all_horizontal(
            "tt_close_above_sma150",
            "tt_close_above_sma200",
            "tt_sma150_above_sma200",
            "tt_sma200_rising",
            "tt_sma50_above_sma150",
            "tt_sma50_above_sma200",
            "tt_close_above_sma50",
            "tt_above_52w_low",
            "tt_near_52w_high",
        ).alias("trend_template_pass")
    )

from __future__ import annotations

import polars as pl

from sepa_scanner.settings import PullbackSettings


def add_pullback_setup_columns(
    frame: pl.DataFrame,
    settings: PullbackSettings,
) -> pl.DataFrame:
    """Add causal pullback-continuation setup columns to an indicator frame.

    The input must already be sorted by ``symbol, date`` and contain the trend
    template columns plus ``sma50``, ``ema21``, ``voldry``, and OHLCV fields.
    This helper intentionally does not inspect future bars.  Cross-sectional RS
    and same/prior BREAKOUT de-duplication are applied by the signal engine after
    the per-date ranks are attached.
    """
    anchor_column = settings.anchor_ma
    if anchor_column not in {"ema21", "sma50"}:
        raise ValueError("pullback.anchor_ma must be 'ema21' or 'sma50'")

    above_sma50 = (pl.col("close") > pl.col("sma50")).fill_null(False)
    established_leader = (
        above_sma50.cast(pl.Int32)
        .rolling_sum(
            window_size=settings.leader_min_days,
            min_samples=settings.leader_min_days,
        )
        .over("symbol")
        == settings.leader_min_days
    )
    anchor = pl.col(anchor_column)
    anchor_rising = anchor > anchor.shift(5).over("symbol")
    sma50_rising = pl.col("sma50") > pl.col("sma50").shift(5).over("symbol")
    prior_swing_low = (
        pl.col("low")
        .rolling_min(window_size=10, min_samples=10)
        .shift(1)
        .over("symbol")
    )
    pullback_swing_low = (
        pl.col("low").rolling_min(window_size=3, min_samples=3).over("symbol")
    )
    touched_support = pl.col("low") <= anchor * (1.0 + settings.ma_touch_pct)
    held_structure = pl.col("low") >= prior_swing_low
    close_location = (
        pl.when(pl.col("high") > pl.col("low"))
        .then((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low")))
        .otherwise(0.0)
    )
    up_close_reversal = (
        (pl.col("close") > pl.col("close").shift(1).over("symbol"))
        & (close_location >= settings.reversal_upper_range_frac)
        & (pl.col("close") >= anchor)
    )
    higher_low_reversal = (
        (pl.col("low") > pl.col("low").shift(1).over("symbol"))
        & (
            pl.col("low").shift(1).over("symbol")
            > pl.col("low").shift(2).over("symbol")
        )
        & (pl.col("close") >= anchor)
    )

    return frame.with_columns(
        anchor.alias("pullback_anchor"),
        prior_swing_low.alias("pullback_prior_swing_low"),
        pullback_swing_low.alias("pullback_stop"),
        pl.col("high").alias("pullback_pivot"),
        established_leader.fill_null(False).alias("pullback_established_leader"),
        anchor_rising.fill_null(False).alias("pullback_anchor_rising"),
        sma50_rising.fill_null(False).alias("pullback_sma50_rising"),
        touched_support.fill_null(False).alias("pullback_touched_support"),
        held_structure.fill_null(False).alias("pullback_held_structure"),
        (up_close_reversal | higher_low_reversal)
        .fill_null(False)
        .alias("pullback_reversal"),
        (
            established_leader
            & sma50_rising
            & anchor_rising
            & touched_support
            & held_structure
            & (pl.col("voldry") <= settings.pullback_voldry_max)
            & (up_close_reversal | higher_low_reversal)
        )
        .fill_null(False)
        .alias("pullback_setup_pass"),
    )

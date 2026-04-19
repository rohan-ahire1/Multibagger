from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

_TREND_LOOKBACK_DAYS = 500
_VOL_LOOKBACK_DAYS = 20
_ANNUALIZATION_FACTOR = math.sqrt(252.0)


def finalize_regime_gate(
    features: pl.DataFrame,
    *,
    breadth_weak_pct: float,
    high_volatility_threshold: float,
) -> pl.DataFrame:
    """Convert raw regime features into a daily entry gate."""
    if "is_macro_bull" not in features.columns:
        features = features.with_columns(pl.lit(True).alias("is_macro_bull"))
    return (
        features.sort("date")
        .with_columns(
            pl.col("trending_market").fill_null(False).cast(pl.Boolean).alias("trending_market"),
            pl.col("is_macro_bull").fill_null(False).cast(pl.Boolean).alias("is_macro_bull"),
            (pl.col("breadth_pct") < breadth_weak_pct).fill_null(True).alias("weak_breadth"),
            (pl.col("benchmark_vol_20_ann") > high_volatility_threshold)
            .fill_null(True)
            .alias("high_volatility"),
        )
        .with_columns(
            (
                pl.col("trending_market")
                & pl.col("is_macro_bull")
                & ~pl.col("weak_breadth")
                & ~pl.col("high_volatility")
            ).alias("entries_allowed")
        )
    )


def compute_regime_gate(
    parquet_store: ParquetOhlcvStore,
    *,
    benchmark_symbol: str,
    start_date: date,
    end_date: date,
    breadth_weak_pct: float,
    high_volatility_threshold: float,
) -> pl.DataFrame:
    """Compute daily entry gating from benchmark trend, market breadth, and realized volatility."""
    lookback_start = start_date - timedelta(days=_TREND_LOOKBACK_DAYS)

    base_scan = (
        parquet_store.scan_lazy(
            start_date=lookback_start,
            end_date=end_date,
            columns=["date", "symbol", "close"],
        )
        .sort(["symbol", "date"])
    )

    breadth = (
        base_scan
        .with_columns(pl.col("close").rolling_mean(200).over("symbol").alias("sma200"))
        .with_columns((pl.col("close") > pl.col("sma200")).cast(pl.Float64).alias("above_200"))
        .filter(pl.col("date") >= pl.lit(start_date))
        .group_by("date")
        .agg(pl.col("above_200").mean().alias("breadth_pct"))
    )

    benchmark = (
        base_scan
        .filter(pl.col("symbol") == pl.lit(benchmark_symbol))
        .sort("date")
        .with_columns(
            pl.col("close").rolling_mean(50).alias("sma50"),
            pl.col("close").rolling_mean(200).alias("sma200"),
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("benchmark_return"),
        )
        .with_columns(pl.col("sma200").shift(20).alias("sma200_lag20"))
        .with_columns(
            pl.col("benchmark_return")
            .rolling_std(_VOL_LOOKBACK_DAYS)
            .mul(_ANNUALIZATION_FACTOR)
            .alias("benchmark_vol_20_ann"),
            (pl.col("close") > pl.col("sma200")).alias("is_macro_bull"),
            (
                (pl.col("close") > pl.col("sma200"))
                & (pl.col("sma50") > pl.col("sma200"))
                & (pl.col("sma200") > pl.col("sma200_lag20"))
            ).alias("trending_market"),
        )
        .filter(pl.col("date") >= pl.lit(start_date))
        .select("date", "trending_market", "is_macro_bull", "benchmark_vol_20_ann")
    )

    features = benchmark.join(breadth, on="date", how="left").collect()
    return finalize_regime_gate(
        features,
        breadth_weak_pct=breadth_weak_pct,
        high_volatility_threshold=high_volatility_threshold,
    )


def gate_lookup(regime_gate: pl.DataFrame) -> dict[date, bool]:
    """Return {date: entries_allowed} for backtest entry checks."""
    if regime_gate.is_empty():
        return {}
    return {
        row["date"]: bool(row["entries_allowed"])
        for row in regime_gate.select("date", "entries_allowed").to_dicts()
    }

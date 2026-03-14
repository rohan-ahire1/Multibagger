from __future__ import annotations

from datetime import date

import polars as pl


def compute_rs_ranks(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    lookbacks: list[int],
    weights: list[float],
    scan_date: date,
    symbols: list[str] | None = None,
    skip_recent_days: int = 0,
) -> pl.DataFrame:
    if len(lookbacks) != len(weights):
        raise ValueError("lookbacks and weights must be the same length")

    sorted_frame = frame.sort(["symbol", "date"])
    return_columns: list[pl.Expr] = []
    benchmark_columns: list[pl.Expr] = []
    rs_terms: list[pl.Expr] = []

    for lookback, weight in zip(lookbacks, weights, strict=True):
        # Shift by skip_recent_days to use close[t-skip] as "current" price
        if skip_recent_days > 0:
            symbol_ret = (
                (pl.col("close").shift(skip_recent_days) / pl.col("close").shift(lookback + skip_recent_days)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        else:
            symbol_ret = (
                (pl.col("close") / pl.col("close").shift(lookback)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        return_columns.append(symbol_ret)
        benchmark_columns.append(pl.col(f"ret_{lookback}").alias(f"bm_ret_{lookback}"))
        rs_terms.append(
            pl.lit(weight)
            * (
                (1.0 + pl.col(f"ret_{lookback}")) / (1.0 + pl.col(f"bm_ret_{lookback}")) - 1.0
            )
        )

    with_returns = sorted_frame.with_columns(return_columns)
    benchmark = with_returns.filter(pl.col("symbol") == benchmark_symbol).select(
        "date", *benchmark_columns
    )

    ranked = with_returns.join(benchmark, on="date", how="left").with_columns(
        pl.sum_horizontal(rs_terms).alias("rs_score")
    )

    filtered = ranked.filter(pl.col("date") == pl.lit(scan_date)).filter(
        pl.col("symbol") != benchmark_symbol
    )
    if symbols is not None:
        filtered = filtered.filter(pl.col("symbol").is_in(symbols))

    result = filtered.select("symbol", "rs_score").sort("symbol")
    count = result.height
    if count == 0:
        return result.with_columns(pl.lit(None).cast(pl.Float64).alias("rs_rank"))
    if count == 1:
        return result.with_columns(pl.lit(100.0).alias("rs_rank"))

    return result.with_columns(
        (
            ((pl.col("rs_score").rank(method="average") - 1.0) / float(count - 1)) * 100.0
        ).alias("rs_rank")
    )


def precompute_rs_scores(
    frame: pl.DataFrame,
    benchmark_symbol: str,
    lookbacks: list[int],
    weights: list[float],
    skip_recent_days: int = 0,
) -> pl.DataFrame:
    """Compute RS scores for all (symbol, date) pairs in a single pass.

    Returns a DataFrame with columns [date, symbol, rs_score].
    Percentile ranking is deferred to ``rank_rs_for_date``.
    """
    if len(lookbacks) != len(weights):
        raise ValueError("lookbacks and weights must be the same length")

    sorted_frame = frame.sort(["symbol", "date"])
    return_columns: list[pl.Expr] = []
    benchmark_columns: list[pl.Expr] = []
    rs_terms: list[pl.Expr] = []

    for lookback, weight in zip(lookbacks, weights, strict=True):
        if skip_recent_days > 0:
            symbol_ret = (
                (pl.col("close").shift(skip_recent_days) / pl.col("close").shift(lookback + skip_recent_days)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        else:
            symbol_ret = (
                (pl.col("close") / pl.col("close").shift(lookback)) - 1.0
            ).over("symbol").alias(f"ret_{lookback}")
        return_columns.append(symbol_ret)
        benchmark_columns.append(pl.col(f"ret_{lookback}").alias(f"bm_ret_{lookback}"))
        rs_terms.append(
            pl.lit(weight)
            * (
                (1.0 + pl.col(f"ret_{lookback}"))
                / (1.0 + pl.col(f"bm_ret_{lookback}"))
                - 1.0
            )
        )

    with_returns = sorted_frame.with_columns(return_columns)
    benchmark = with_returns.filter(pl.col("symbol") == benchmark_symbol).select(
        "date", *benchmark_columns
    )

    return (
        with_returns.join(benchmark, on="date", how="left")
        .with_columns(pl.sum_horizontal(rs_terms).alias("rs_score"))
        .filter(pl.col("symbol") != benchmark_symbol)
        .select("date", "symbol", "rs_score")
    )


def rank_rs_for_date(
    rs_scores: pl.DataFrame,
    scan_date: date,
    symbols: list[str],
) -> pl.DataFrame:
    """Rank pre-computed RS scores for a specific date within an eligible subset."""
    filtered = (
        rs_scores.filter(
            (pl.col("date") == pl.lit(scan_date))
            & pl.col("symbol").is_in(symbols)
        )
        .select("symbol", "rs_score")
        .sort("symbol")
    )

    count = filtered.height
    if count == 0:
        return filtered.with_columns(pl.lit(None).cast(pl.Float64).alias("rs_rank"))
    if count == 1:
        return filtered.with_columns(pl.lit(100.0).alias("rs_rank"))

    return filtered.with_columns(
        (
            ((pl.col("rs_score").rank(method="average") - 1.0) / float(count - 1)) * 100.0
        ).alias("rs_rank")
    )

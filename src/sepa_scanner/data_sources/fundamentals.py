from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_quarterly_results(path: Path | None = None) -> pl.DataFrame:
    """Load quarterly earnings data.

    Expected columns: symbol, quarter_end, eps, revenue, yoy_eps_growth.
    Returns empty DataFrame if path is None or doesn't exist.
    """
    if path is None or not Path(path).exists():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "quarter_end": pl.Date,
            "eps": pl.Float64,
            "revenue": pl.Float64,
            "yoy_eps_growth": pl.Float64,
        })
    return pl.read_csv(path, try_parse_dates=True)


def compute_eps_growth_gate(
    earnings: pl.DataFrame, min_yoy_growth: float = 0.25
) -> pl.DataFrame:
    """Minervini-style EPS-growth-LEVEL gate (the formulation that validated).

    Uses the latest point-in-time YoY EPS growth *level* — not acceleration.
    Prototype testing (2026-06, screener.in data through the portfolio engine)
    showed the *level* of EPS growth separates winners (top tercile +5.5% vs
    bottom two negative on 126d fwd) and lifts realized CAGR/Calmar through the
    engine, whereas acceleration (compute_earnings_acceleration) carried no
    signal. Default threshold 0.25 = Minervini's classic +25% YoY ("code 33"),
    which also sits near the prototype cohort median.

    Returns: symbol, latest_quarter, yoy_growth, passes_filter.
    """
    if earnings.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "latest_quarter": pl.Date,
            "yoy_growth": pl.Float64,
            "passes_filter": pl.Boolean,
        })
    return (
        earnings.sort(["symbol", "quarter_end"])
        .group_by("symbol")
        .agg(pl.all().sort_by("quarter_end").last())
        .select(
            "symbol",
            pl.col("quarter_end").alias("latest_quarter"),
            pl.col("yoy_eps_growth").alias("yoy_growth"),
        )
        .with_columns(
            (pl.col("yoy_growth") >= min_yoy_growth).fill_null(False).alias("passes_filter")
        )
    )


def compute_earnings_acceleration(earnings: pl.DataFrame) -> pl.DataFrame:
    """Compute earnings acceleration: QoQ change in YoY EPS growth rate.

    NOTE: prototype testing found acceleration carries NO forward signal on
    Indian data; prefer compute_eps_growth_gate (EPS-growth level). Kept for
    reference / comparison.

    Returns DataFrame with columns: symbol, latest_quarter, yoy_growth, acceleration, passes_filter.
    A stock passes if: yoy_growth >= 20% AND acceleration > 0 (growth is accelerating).
    """
    if earnings.is_empty():
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "latest_quarter": pl.Date,
            "yoy_growth": pl.Float64,
            "acceleration": pl.Float64,
            "passes_filter": pl.Boolean,
        })

    # Sort by symbol and quarter, compute acceleration
    sorted_df = earnings.sort(["symbol", "quarter_end"])

    result = sorted_df.with_columns(
        pl.col("yoy_eps_growth").shift(1).over("symbol").alias("prev_yoy_growth"),
    ).with_columns(
        (pl.col("yoy_eps_growth") - pl.col("prev_yoy_growth")).alias("acceleration"),
    )

    # Get latest quarter per symbol
    latest = (
        result.group_by("symbol")
        .agg(pl.all().sort_by("quarter_end").last())
        .select(
            "symbol",
            pl.col("quarter_end").alias("latest_quarter"),
            pl.col("yoy_eps_growth").alias("yoy_growth"),
            "acceleration",
        )
    ).with_columns(
        (
            (pl.col("yoy_growth") >= 0.20)
            & (pl.col("acceleration") > 0)
        ).alias("passes_filter"),
    )

    return latest

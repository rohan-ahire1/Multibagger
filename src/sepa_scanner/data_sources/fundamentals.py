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


def apply_fundamental_gate(
    signals: pl.DataFrame,
    *,
    data_path: str | Path,
    min_yoy_growth: float = 0.25,
    reporting_lag_days: int = 75,
    freq: str | None = None,
    pass_if_missing: bool = True,
) -> pl.DataFrame:
    """Filter breakout signals by point-in-time earnings growth (Minervini gate).

    Point-in-time: a quarter/year only counts once its results would have been
    announced (period_end + reporting_lag_days). For each signal it as-of joins
    the latest known growth for that symbol and drops signals whose growth is
    below ``min_yoy_growth``. Signals with no available data are kept iff
    ``pass_if_missing`` (so the strategy degrades gracefully on partial data).

    The data file may use either schema (annual: period_end/yoy_growth;
    quarterly: quarter_end/yoy_eps_growth). Returns ``signals`` unchanged with a
    no-op (and warning) if the file is missing, so enabling the gate without
    data never silently empties the book.
    """
    path = Path(data_path)
    if not path.exists():
        print(f"⚠️  fundamental_gate enabled but {path} not found — gate skipped (no-op).")
        return signals
    if "signal_date" not in signals.columns or "symbol" not in signals.columns:
        return signals

    e = pl.read_csv(path, try_parse_dates=True)
    date_col = "period_end" if "period_end" in e.columns else "quarter_end"
    growth_col = "yoy_growth" if "yoy_growth" in e.columns else "yoy_eps_growth"
    if freq and "freq" in e.columns:
        e = e.filter(pl.col("freq") == freq)
    e = (
        e.select(["symbol", pl.col(date_col).alias("_pe"), pl.col(growth_col).alias("_g")])
        .drop_nulls("_g")
        .with_columns((pl.col("_pe") + pl.duration(days=reporting_lag_days)).alias("_known"))
        .sort(["symbol", "_known"])
    )

    cols = signals.columns
    joined = signals.sort("signal_date").join_asof(
        e.select(["symbol", "_known", "_g"]),
        left_on="signal_date",
        right_on="_known",
        by="symbol",
        strategy="backward",
    )
    cond = pl.col("_g") >= min_yoy_growth
    if pass_if_missing:
        cond = pl.col("_g").is_null() | cond
    return joined.filter(cond).select(cols)


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

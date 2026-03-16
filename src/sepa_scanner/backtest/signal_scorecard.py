"""Signal scorecard — measures how entry signals actually perform.

For each BREAKOUT signal, tracks forward price action over multiple horizons
and slices results by signal attributes to reveal which conditions predict success.
"""

from __future__ import annotations

from datetime import date

import polars as pl


FORWARD_DAYS = [1, 3, 5, 10, 20]


def _compute_forward_metrics(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    forward_days: list[int],
) -> pl.DataFrame:
    """For each signal, compute forward returns, MFE, and MAE."""
    max_fwd = max(forward_days)
    rows: list[dict] = []

    prices_by_sym: dict[str, pl.DataFrame] = {}
    for sym in signals["symbol"].unique().to_list():
        prices_by_sym[sym] = ohlcv.filter(pl.col("symbol") == sym).sort("date")

    for sig in signals.iter_rows(named=True):
        sym = str(sig["symbol"])
        sig_date = sig["signal_date"]
        entry_ref = float(sig["entry_ref"])
        stop = float(sig["stop"])
        r_per_share = float(sig["r_per_share"])

        # Use OHLCV close on signal date (not stored signal close which may
        # come from a different data source / adjustment basis)
        sym_prices = prices_by_sym.get(sym)
        if sym_prices is None or sym_prices.is_empty():
            continue
        sig_day_row = sym_prices.filter(pl.col("date") == pl.lit(sig_date))
        if sig_day_row.is_empty():
            continue
        sig_close = float(sig_day_row["close"][0])
        # Recompute entry_ref and stop relative to adjusted close
        entry_ref = sig_close
        stop = sig_close * (1.0 - r_per_share / float(sig["entry_ref"]))  # preserve R ratio
        r_per_share = entry_ref - stop

        future = sym_prices.filter(pl.col("date") > pl.lit(sig_date)).head(max_fwd)
        if future.is_empty():
            continue

        highs = future["high"].to_list()
        lows = future["low"].to_list()
        closes = future["close"].to_list()
        n = len(closes)

        if n < forward_days[0]:
            continue

        # MFE/MAE over available days
        max_high = max(float(h) for h in highs)
        min_low = min(float(lo) for lo in lows)
        mfe_pct = (max_high - sig_close) / sig_close * 100.0
        mae_pct = (min_low - sig_close) / sig_close * 100.0

        # MFE/MAE in R-multiples
        mfe_r = (max_high - entry_ref) / r_per_share if r_per_share > 0 else 0.0
        mae_r = (min_low - entry_ref) / r_per_share if r_per_share > 0 else 0.0

        # Did price hit stop before +1R?
        hit_stop_first = False
        hit_1r_first = False
        target_1r = entry_ref + r_per_share
        for i in range(n):
            low_i = float(lows[i])
            high_i = float(highs[i])
            if low_i <= stop and not hit_1r_first:
                hit_stop_first = True
                break
            if high_i >= target_1r and not hit_stop_first:
                hit_1r_first = True
                break

        row = {
            "symbol": sym,
            "signal_date": sig_date,
            "sig_close": sig_close,
            "entry_ref": entry_ref,
            "stop": stop,
            "r_per_share": r_per_share,
            "rs_rank": float(sig["rs_rank"]),
            "vol_mult": float(sig["vol_mult"]),
            "range10": float(sig["range10"]),
            "voldry": float(sig["voldry"]),
            "atr_pct": float(sig["atr_pct"]),
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "hit_stop_first": hit_stop_first,
            "hit_1r_first": hit_1r_first,
            "days_available": n,
        }

        for d in forward_days:
            if n >= d:
                fwd_close = float(closes[d - 1])
                row[f"fwd_{d}d_pct"] = (fwd_close - sig_close) / sig_close * 100.0
                row[f"fwd_{d}d_win"] = fwd_close > sig_close
            else:
                row[f"fwd_{d}d_pct"] = None
                row[f"fwd_{d}d_win"] = None

        rows.append(row)

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows)


def _bucket(col: str, edges: list[float], labels: list[str]) -> pl.Expr:
    """Create a bucket column from edges and labels."""
    expr = pl.when(pl.col(col) < edges[0]).then(pl.lit(labels[0]))
    for i in range(len(edges) - 1):
        expr = expr.when(
            (pl.col(col) >= edges[i]) & (pl.col(col) < edges[i + 1])
        ).then(pl.lit(labels[i + 1]))
    expr = expr.otherwise(pl.lit(labels[-1]))
    return expr.alias(f"{col}_bucket")


def _slice_summary(df: pl.DataFrame, group_col: str, forward_days: list[int]) -> list[dict]:
    """Aggregate forward metrics by a grouping column."""
    if df.is_empty():
        return []

    agg_exprs: list[pl.Expr] = [pl.len().alias("count")]

    for d in forward_days:
        col = f"fwd_{d}d_pct"
        win_col = f"fwd_{d}d_win"
        agg_exprs.extend([
            pl.col(col).filter(pl.col(col).is_not_null()).mean().alias(f"avg_{d}d_return"),
            pl.col(col).filter(pl.col(col).is_not_null()).median().alias(f"med_{d}d_return"),
            (
                pl.col(win_col).filter(pl.col(win_col).is_not_null()).mean() * 100.0
            ).alias(f"win_rate_{d}d"),
        ])

    agg_exprs.extend([
        pl.col("mfe_pct").mean().alias("avg_mfe_pct"),
        pl.col("mae_pct").mean().alias("avg_mae_pct"),
        pl.col("mfe_r").mean().alias("avg_mfe_r"),
        pl.col("mae_r").mean().alias("avg_mae_r"),
        (pl.col("hit_stop_first").mean() * 100.0).alias("stop_hit_first_pct"),
        (pl.col("hit_1r_first").mean() * 100.0).alias("reached_1r_first_pct"),
    ])

    result = df.group_by(group_col).agg(agg_exprs).sort(group_col)
    return result.to_dicts()


def compute_scorecard(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    diagnostics: pl.DataFrame | None = None,
    forward_days: list[int] | None = None,
) -> dict:
    """Compute full signal scorecard with sliced breakdowns.

    Returns dict with overall summary + breakdowns by RS rank, volume multiple,
    tightness, volume dryup, and contraction count.
    """
    if forward_days is None:
        forward_days = FORWARD_DAYS

    breakouts = signals.filter(pl.col("signal_type") == "BREAKOUT")
    if breakouts.is_empty():
        return {"error": "No BREAKOUT signals found", "total_signals": 0}

    metrics = _compute_forward_metrics(breakouts, ohlcv, forward_days)
    if metrics.is_empty():
        return {"error": "No forward data available for signals", "total_signals": breakouts.height}

    # Join diagnostics if available (for contraction data)
    if diagnostics is not None and not diagnostics.is_empty():
        diag_cols = diagnostics.select(
            pl.col("diag_date").alias("signal_date"),
            "symbol",
            "contraction_count",
            "contraction_quality",
        )
        metrics = metrics.join(diag_cols, on=["signal_date", "symbol"], how="left")

    # Overall summary
    overall = _slice_summary(
        metrics.with_columns(pl.lit("all").alias("_all")), "_all", forward_days
    )

    # Bucket by RS rank
    metrics = metrics.with_columns(
        _bucket("rs_rank", [85, 90, 95], ["80-85", "85-90", "90-95", "95-100"])
    )

    # Bucket by volume multiple
    metrics = metrics.with_columns(
        _bucket("vol_mult", [2.0, 3.0, 5.0], ["1.5-2x", "2-3x", "3-5x", "5x+"])
    )

    # Bucket by range10 (tightness)
    metrics = metrics.with_columns(
        _bucket("range10", [0.04, 0.06, 0.08], ["<4%", "4-6%", "6-8%", "8-10%"])
    )

    # Bucket by voldry
    metrics = metrics.with_columns(
        _bucket("voldry", [0.3, 0.45, 0.55], ["<0.30", "0.30-0.45", "0.45-0.55", "0.55-0.60"])
    )

    result = {
        "total_signals": breakouts.height,
        "signals_with_fwd_data": metrics.height,
        "date_range": {
            "earliest": str(breakouts["signal_date"].min()),
            "latest": str(breakouts["signal_date"].max()),
        },
        "forward_days": forward_days,
        "overall": overall[0] if overall else {},
        "by_rs_rank": _slice_summary(metrics, "rs_rank_bucket", forward_days),
        "by_vol_mult": _slice_summary(metrics, "vol_mult_bucket", forward_days),
        "by_range10": _slice_summary(metrics, "range10_bucket", forward_days),
        "by_voldry": _slice_summary(metrics, "voldry_bucket", forward_days),
    }

    # Contraction slicing if available
    if "contraction_count" in metrics.columns:
        metrics = metrics.with_columns(
            pl.when(pl.col("contraction_count").is_null())
            .then(pl.lit("unknown"))
            .when(pl.col("contraction_count") >= 3)
            .then(pl.lit("3+"))
            .when(pl.col("contraction_count") >= 2)
            .then(pl.lit("2"))
            .when(pl.col("contraction_count") >= 1)
            .then(pl.lit("1"))
            .otherwise(pl.lit("0"))
            .alias("contraction_bucket")
        )
        result["by_contraction"] = _slice_summary(metrics, "contraction_bucket", forward_days)

    return result

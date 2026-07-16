"""Signal scorecard — measures how entry signals actually perform.

For each BREAKOUT signal, tracks forward price action over multiple horizons
and slices results by signal attributes to reveal which conditions predict success.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.strategy.ranking import add_signal_score


FORWARD_DAYS = [1, 3, 5, 10, 20]


def _compute_forward_metrics(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    forward_days: list[int],
    pending_days: int,
) -> pl.DataFrame:
    """Measure actual D+1 entries and their post-fill price path.

    A BREAKOUT is an end-of-day observation.  The scorecard therefore uses the
    same executable contract as the simulator: inspect the next ``pending_days``
    *symbol trading bars*, fill at the first bar that reaches ``entry_ref`` and
    then assess only bars after the fill day.  Excluding the fill bar avoids
    inventing an intraday high/low sequence that daily OHLCV cannot establish.
    """
    max_fwd = max(forward_days)
    rows: list[dict] = []

    prices_by_sym: dict[str, pl.DataFrame] = {}
    for sym in signals["symbol"].unique().to_list():
        prices_by_sym[sym] = ohlcv.filter(pl.col("symbol") == sym).sort("date")

    for sig in signals.iter_rows(named=True):
        sym = str(sig["symbol"])
        sig_date = sig["signal_date"]
        stored_entry_ref = float(sig["entry_ref"])
        stored_stop = float(sig["stop"])

        # Stored signals can predate a yfinance corporate-action revision.
        # Rebase their levels to the current adjusted OHLCV basis while
        # preserving the original entry/stop relationship.
        sym_prices = prices_by_sym.get(sym)
        if sym_prices is None or sym_prices.is_empty():
            continue
        sig_day_row = sym_prices.filter(pl.col("date") == pl.lit(sig_date))
        if sig_day_row.is_empty():
            continue
        adjusted_signal_close = float(sig_day_row["close"][0])
        stored_signal_close = sig.get("close")
        if stored_signal_close is not None and float(stored_signal_close) > 0.0:
            adjustment_factor = adjusted_signal_close / float(stored_signal_close)
        else:
            adjustment_factor = 1.0
        entry_ref = stored_entry_ref * adjustment_factor
        stop = stored_stop * adjustment_factor

        pending = sym_prices.filter(pl.col("date") > pl.lit(sig_date)).head(pending_days)
        # Do not score a partly observed pending window.  Otherwise the most
        # recent signals get a favourable chance to be counted as "not filled".
        if pending.height < pending_days:
            continue

        fill_date = None
        fill_price = None
        for bar in pending.iter_rows(named=True):
            open_price = float(bar["open"])
            high_price = float(bar["high"])
            if open_price >= entry_ref:
                fill_date = bar["date"]
                fill_price = open_price
                break
            if high_price >= entry_ref:
                fill_date = bar["date"]
                fill_price = entry_ref
                break

        row = {
            "symbol": sym,
            "signal_date": sig_date,
            "sig_close": adjusted_signal_close,
            "entry_ref": entry_ref,
            "stop": stop,
            "rs_rank": float(sig["rs_rank"]),
            "vol_mult": float(sig["vol_mult"]),
            "range10": float(sig["range10"]),
            "voldry": float(sig["voldry"]),
            "atr_pct": float(sig["atr_pct"]),
            "filled": fill_price is not None,
            "fill_date": fill_date,
            "fill_price": fill_price,
            "r_per_share": None,
            "days_available": None,
            "mfe_pct": None,
            "mae_pct": None,
            "mfe_r": None,
            "mae_r": None,
            "hit_stop_first": None,
            "hit_1r_first": None,
            "reached_2r": None,
            "reached_3r": None,
            "reached_5r": None,
        }
        for d in forward_days:
            row[f"fwd_{d}d_pct"] = None
            row[f"fwd_{d}d_win"] = None
            row[f"fwd_{d}d_r"] = None

        if fill_price is None:
            rows.append(row)
            continue

        r_per_share = fill_price - stop
        # A gap through the physical stop is not an executable long entry for
        # this scorecard; leave it visible as filled but without R outcomes.
        if r_per_share <= 0.0:
            rows.append(row)
            continue

        future = sym_prices.filter(pl.col("date") > pl.lit(fill_date)).head(max_fwd)
        highs = future["high"].to_list()
        lows = future["low"].to_list()
        closes = future["close"].to_list()
        n = len(closes)

        row["r_per_share"] = r_per_share
        row["days_available"] = n
        if n == 0:
            rows.append(row)
            continue

        # MFE/MAE over the configured maximum forward window, calculated from
        # the actual fill rather than the EOD signal close.
        max_high = max(float(h) for h in highs)
        min_low = min(float(lo) for lo in lows)
        mfe_pct = (max_high - fill_price) / fill_price * 100.0
        mae_pct = (min_low - fill_price) / fill_price * 100.0
        mfe_r = (max_high - fill_price) / r_per_share
        mae_r = (min_low - fill_price) / r_per_share

        # Did price hit stop before +1R?
        hit_stop_first = False
        hit_1r_first = False
        target_1r = fill_price + r_per_share
        for i in range(n):
            low_i = float(lows[i])
            high_i = float(highs[i])
            if low_i <= stop and not hit_1r_first:
                hit_stop_first = True
                break
            if high_i >= target_1r and not hit_stop_first:
                hit_1r_first = True
                break

        row.update({
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "hit_stop_first": hit_stop_first,
            "hit_1r_first": hit_1r_first,
            "reached_2r": mfe_r >= 2.0,
            "reached_3r": mfe_r >= 3.0,
            "reached_5r": mfe_r >= 5.0,
        })

        for d in forward_days:
            if n >= d:
                fwd_close = float(closes[d - 1])
                row[f"fwd_{d}d_pct"] = (fwd_close - fill_price) / fill_price * 100.0
                row[f"fwd_{d}d_win"] = fwd_close > fill_price
                row[f"fwd_{d}d_r"] = (fwd_close - fill_price) / r_per_share
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

    agg_exprs: list[pl.Expr] = [
        pl.len().alias("count"),
        pl.col("filled").sum().alias("filled_count"),
        (pl.col("filled").mean() * 100.0).alias("fill_rate_pct"),
    ]

    for d in forward_days:
        col = f"fwd_{d}d_pct"
        win_col = f"fwd_{d}d_win"
        r_col = f"fwd_{d}d_r"
        agg_exprs.extend([
            pl.col(col).filter(pl.col(col).is_not_null()).mean().alias(f"avg_{d}d_return"),
            pl.col(col).filter(pl.col(col).is_not_null()).median().alias(f"med_{d}d_return"),
            pl.col(r_col).filter(pl.col(r_col).is_not_null()).mean().alias(f"avg_{d}d_r"),
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
        (pl.col("reached_2r").mean() * 100.0).alias("reached_2r_pct"),
        (pl.col("reached_3r").mean() * 100.0).alias("reached_3r_pct"),
        (pl.col("reached_5r").mean() * 100.0).alias("reached_5r_pct"),
    ])

    result = df.group_by(group_col).agg(agg_exprs).sort(group_col)
    return result.to_dicts()


def compute_scorecard(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    diagnostics: pl.DataFrame | None = None,
    forward_days: list[int] | None = None,
    pending_days: int = 8,
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

    if pending_days < 1:
        raise ValueError("pending_days must be at least one trading bar")

    metrics = _compute_forward_metrics(breakouts, ohlcv, forward_days, pending_days)
    if metrics.is_empty():
        return {"error": "No forward data available for signals", "total_signals": breakouts.height}

    # Join diagnostics if available (for contraction data)
    if diagnostics is not None and not diagnostics.is_empty():
        diag_cols = diagnostics.select(
            pl.col("diag_date").alias("signal_date"),
            "symbol",
            "tt_pass",
            "rs_pass",
            "setup_pass",
            "vcp1_range10_ok",
            "vcp2_voldry_ok",
            "vcp3_atr_trend_ok",
            "vcp4_atr_contract_ok",
            "vcp5_extension_ok",
            "vcp6_base_depth_ok",
            "contraction_count",
            "contraction_quality",
            "near_pivot",
            "setup_active_streak",
            "pivot_age_days",
            "watch_fail_mask",
            "breakout_fail_mask",
        )
        metrics = metrics.join(diag_cols, on=["signal_date", "symbol"], how="left")
    metrics = add_signal_score(metrics, ranking_mode="offense_v2", score_column="offense_v2_score")

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

    if "contraction_quality" in metrics.columns:
        metrics = metrics.with_columns(
            _bucket(
                "contraction_quality",
                [0.35, 0.55, 0.75],
                ["<0.35", "0.35-0.55", "0.55-0.75", "0.75+"],
            )
        )
    if "setup_active_streak" in metrics.columns:
        metrics = metrics.with_columns(
            pl.when(pl.col("setup_active_streak").is_null())
            .then(pl.lit("unknown"))
            .when(pl.col("setup_active_streak") <= 1)
            .then(pl.lit("1"))
            .when(pl.col("setup_active_streak") <= 3)
            .then(pl.lit("2-3"))
            .when(pl.col("setup_active_streak") <= 6)
            .then(pl.lit("4-6"))
            .otherwise(pl.lit("7+"))
            .alias("setup_active_streak_bucket")
        )
    if "pivot_age_days" in metrics.columns:
        metrics = metrics.with_columns(
            pl.when(pl.col("pivot_age_days").is_null())
            .then(pl.lit("unknown"))
            .when(pl.col("pivot_age_days") <= 1)
            .then(pl.lit("0-1"))
            .when(pl.col("pivot_age_days") <= 3)
            .then(pl.lit("2-3"))
            .when(pl.col("pivot_age_days") <= 7)
            .then(pl.lit("4-7"))
            .otherwise(pl.lit("8+"))
            .alias("pivot_age_days_bucket")
        )
    if "near_pivot" in metrics.columns:
        metrics = metrics.with_columns(
            _bucket("near_pivot", [0.01, 0.02, 0.03], ["<1%", "1-2%", "2-3%", "3%+"])
        )
    if "atr_pct" in metrics.columns:
        metrics = metrics.with_columns(
            _bucket("atr_pct", [0.02, 0.03, 0.04], ["<2%", "2-3%", "3-4%", "4%+"])
        )
    if "offense_v2_score" in metrics.columns:
        metrics = metrics.with_columns(
            pl.when(pl.col("offense_v2_score") >= 9.0)
            .then(pl.lit("9.0+"))
            .when(pl.col("offense_v2_score") >= 8.0)
            .then(pl.lit("8.0-9.0"))
            .when(pl.col("offense_v2_score") >= 7.0)
            .then(pl.lit("7.0-8.0"))
            .otherwise(pl.lit("<7.0"))
            .alias("offense_v2_score_bucket")
        )

    result = {
        "total_signals": breakouts.height,
        "signals_entry_evaluable": metrics.height,
        "signals_filled": int(metrics["filled"].sum()),
        "signals_with_fwd_data": int(
            metrics.filter(pl.col("mfe_r").is_not_null()).height
            if "mfe_r" in metrics.columns
            else 0
        ),
        "date_range": {
            "earliest": str(breakouts["signal_date"].min()),
            "latest": str(breakouts["signal_date"].max()),
        },
        "forward_days": forward_days,
        "pending_days": pending_days,
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
    if "contraction_quality_bucket" in metrics.columns:
        result["by_contraction_quality"] = _slice_summary(metrics, "contraction_quality_bucket", forward_days)
    if "setup_active_streak_bucket" in metrics.columns:
        result["by_setup_streak"] = _slice_summary(metrics, "setup_active_streak_bucket", forward_days)
    if "pivot_age_days_bucket" in metrics.columns:
        result["by_pivot_age"] = _slice_summary(metrics, "pivot_age_days_bucket", forward_days)
    if "near_pivot_bucket" in metrics.columns:
        result["by_near_pivot"] = _slice_summary(metrics, "near_pivot_bucket", forward_days)
    if "atr_pct_bucket" in metrics.columns:
        result["by_atr_pct"] = _slice_summary(metrics, "atr_pct_bucket", forward_days)
    if "offense_v2_score_bucket" in metrics.columns:
        result["by_offense_v2_score"] = _slice_summary(metrics, "offense_v2_score_bucket", forward_days)

    return result

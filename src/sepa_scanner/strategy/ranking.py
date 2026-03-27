from __future__ import annotations

import polars as pl


def _clamp01(expr: pl.Expr) -> pl.Expr:
    return (
        pl.when(expr < 0.0)
        .then(pl.lit(0.0))
        .when(expr > 1.0)
        .then(pl.lit(1.0))
        .otherwise(expr)
    )


def _championship_score_expr() -> pl.Expr:
    rs_rank = pl.col("rs_rank").fill_null(0.0)
    vol_mult = pl.col("vol_mult").fill_null(0.0)
    range10 = pl.col("range10").fill_null(0.10)
    voldry = pl.col("voldry").fill_null(0.60)
    near_pivot = pl.col("near_pivot").fill_null(0.05).abs()
    adv50 = pl.col("adv50").fill_null(0.0)
    risk_pct = (pl.col("r_per_share").fill_null(0.0) / pl.col("entry_ref").fill_null(1.0)).fill_nan(0.0)

    rs_score = _clamp01(1.0 - ((rs_rank - 90.0).abs() / 10.0))
    volume_score = _clamp01((vol_mult - 1.5) / 3.5)
    tightness_score = _clamp01(1.0 - ((range10 - 0.07).abs() / 0.03))
    dryup_score = _clamp01(1.0 - ((voldry - 0.45).abs() / 0.15))
    proximity_score = _clamp01(1.0 - (near_pivot / 0.05))
    risk_score = _clamp01(1.0 - ((risk_pct - 0.06).abs() / 0.06))
    liquidity_score = _clamp01((adv50.log10() - 7.7) / 1.5)

    return (
        (rs_score * 3.0)
        + (tightness_score * 2.0)
        + (dryup_score * 2.0)
        + (volume_score * 1.5)
        + (proximity_score * 1.0)
        + (risk_score * 1.0)
        + (liquidity_score * 0.5)
    ).alias("_signal_score")


def _sort_for_mode(frame: pl.DataFrame, ranking_mode: str, primary_group_col: str) -> pl.DataFrame:
    if ranking_mode == "championship":
        return frame.with_columns(_championship_score_expr()).sort(
            by=[primary_group_col, "_signal_score", "rs_rank", "vol_mult", "range10", "symbol"],
            descending=[False, True, True, True, False, False],
        )
    return frame.sort(
        by=[primary_group_col, "rs_rank", "vol_mult", "range10", "symbol"],
        descending=[False, True, True, False, False],
    )


def rank_signals(frame: pl.DataFrame, max_alerts_per_day: int, ranking_mode: str = "default") -> pl.DataFrame:
    if frame.is_empty():
        return frame
    ranked = (
        _sort_for_mode(frame, ranking_mode, "signal_type")
        .group_by("signal_type", maintain_order=True)
        .head(max_alerts_per_day)
    )
    return ranked.drop("_signal_score", strict=False)


def select_top_signals_by_day(
    frame: pl.DataFrame,
    max_signals_per_day: int | None,
    ranking_mode: str = "default",
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    ranked = _sort_for_mode(frame, ranking_mode, "signal_date")
    if max_signals_per_day is None or max_signals_per_day <= 0:
        return ranked.drop("_signal_score", strict=False)
    return (
        ranked.group_by("signal_date", maintain_order=True)
        .head(max_signals_per_day)
        .drop("_signal_score", strict=False)
    )

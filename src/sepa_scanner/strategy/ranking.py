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


def _value_expr(columns: set[str], name: str, default: float) -> pl.Expr:
    if name not in columns:
        return pl.lit(default)
    return pl.col(name).cast(pl.Float64).fill_null(default).fill_nan(default)


def _bool_expr(columns: set[str], name: str, default: bool = False) -> pl.Expr:
    if name not in columns:
        return pl.lit(1.0 if default else 0.0)
    return pl.col(name).cast(pl.Float64).fill_null(1.0 if default else 0.0)


def _championship_score_expr(columns: set[str]) -> pl.Expr:
    rs_rank = _value_expr(columns, "rs_rank", 0.0)
    vol_mult = _value_expr(columns, "vol_mult", 0.0)
    range10 = _value_expr(columns, "range10", 0.10)
    voldry = _value_expr(columns, "voldry", 0.60)
    near_pivot = _value_expr(columns, "near_pivot", 0.05).abs()
    adv50 = _value_expr(columns, "adv50", 0.0)
    risk_pct = (
        _value_expr(columns, "r_per_share", 0.0) / _value_expr(columns, "entry_ref", 1.0)
    ).fill_nan(0.0)

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


def _offense_v2_score_expr(columns: set[str]) -> pl.Expr:
    rs_rank = _value_expr(columns, "rs_rank", 0.0)
    vol_mult = _value_expr(columns, "vol_mult", 0.0)
    range10 = _value_expr(columns, "range10", 0.10)
    voldry = _value_expr(columns, "voldry", 0.60)
    near_pivot = _value_expr(columns, "near_pivot", 0.05).abs()
    adv50 = _value_expr(columns, "adv50", 0.0)
    atr_pct = _value_expr(columns, "atr_pct", 0.04)
    risk_pct = (
        _value_expr(columns, "r_per_share", 0.0) / _value_expr(columns, "entry_ref", 1.0)
    ).fill_nan(0.0)
    contraction_quality = _value_expr(columns, "contraction_quality", 0.45)
    contraction_count = _value_expr(columns, "contraction_count", 1.0)
    setup_active_streak = _value_expr(columns, "setup_active_streak", 1.0)
    pivot_age_days = _value_expr(columns, "pivot_age_days", 4.0)
    tt_pass = _bool_expr(columns, "tt_pass", True)
    setup_pass = _bool_expr(columns, "setup_pass", True)
    rs_pass = _bool_expr(columns, "rs_pass", True)
    range10_ok = _bool_expr(columns, "vcp1_range10_ok", True)
    voldry_ok = _bool_expr(columns, "vcp2_voldry_ok", True)
    atr_contract_ok = _bool_expr(columns, "vcp4_atr_contract_ok", True)
    extension_ok = _bool_expr(columns, "vcp5_extension_ok", True)
    base_depth_ok = _bool_expr(columns, "vcp6_base_depth_ok", True)
    watch_fail_mask = _value_expr(columns, "watch_fail_mask", 0.0)
    breakout_fail_mask = _value_expr(columns, "breakout_fail_mask", 0.0)

    rs_score = _clamp01(1.0 - ((rs_rank - 90.0).abs() / 8.0))
    volume_score = _clamp01((vol_mult - 1.8) / 2.5)
    tightness_score = _clamp01(1.0 - ((range10 - 0.065).abs() / 0.03))
    dryup_score = _clamp01(1.0 - ((voldry - 0.40).abs() / 0.20))
    proximity_score = _clamp01(1.0 - (near_pivot / 0.04))
    atr_score = _clamp01(1.0 - ((atr_pct - 0.03).abs() / 0.03))
    risk_score = _clamp01(1.0 - ((risk_pct - 0.055).abs() / 0.04))
    liquidity_score = _clamp01((adv50.log10() - 7.7) / 1.5)
    contraction_quality_score = _clamp01(contraction_quality / 0.80)
    contraction_count_score = _clamp01((contraction_count - 1.0) / 2.0)
    streak_score = _clamp01(setup_active_streak / 6.0)
    pivot_freshness_score = _clamp01(1.0 - (pivot_age_days / 12.0))
    structure_score = (
        range10_ok + voldry_ok + atr_contract_ok + extension_ok + base_depth_ok
    ) / 5.0
    failure_penalty = _clamp01((watch_fail_mask + breakout_fail_mask) / 16.0)

    return (
        (rs_score * 2.5)
        + (tightness_score * 2.0)
        + (dryup_score * 1.5)
        + (contraction_quality_score * 1.5)
        + (streak_score * 1.25)
        + (volume_score * 1.0)
        + (contraction_count_score * 0.75)
        + (pivot_freshness_score * 0.75)
        + (proximity_score * 0.75)
        + (risk_score * 0.5)
        + (atr_score * 0.5)
        + (liquidity_score * 0.5)
        + (structure_score * 1.0)
        + (tt_pass * 0.5)
        + (setup_pass * 0.5)
        + (rs_pass * 0.5)
        - (failure_penalty * 1.5)
    ).alias("_signal_score")


def add_signal_score(
    frame: pl.DataFrame,
    *,
    ranking_mode: str,
    score_column: str = "_signal_score",
) -> pl.DataFrame:
    if frame.is_empty():
        return frame

    columns = set(frame.columns)
    if ranking_mode == "championship":
        return frame.with_columns(_championship_score_expr(columns).alias(score_column))
    if ranking_mode == "offense_v2":
        return frame.with_columns(_offense_v2_score_expr(columns).alias(score_column))
    return frame


def add_signal_size_multiplier(
    frame: pl.DataFrame,
    *,
    tilt_mode: str,
    score_min: float,
    score_max: float,
    min_mult: float,
    max_mult: float,
    mult_column: str = "signal_size_mult",
) -> pl.DataFrame:
    if frame.is_empty() or tilt_mode in {"off", "none", ""}:
        return frame
    if tilt_mode not in {"offense_v2", "offense_v2_band"}:
        raise ValueError(f"Unsupported signal size tilt mode: {tilt_mode}")
    if score_max <= score_min:
        raise ValueError("signal size tilt score_max must be greater than score_min")
    if min_mult < 0.0 or max_mult < 0.0:
        raise ValueError("signal size tilt multipliers must be non-negative")

    ranked = add_signal_score(frame, ranking_mode="offense_v2", score_column="_offense_v2_score")
    if tilt_mode == "offense_v2":
        scaled = _clamp01((pl.col("_offense_v2_score") - float(score_min)) / float(score_max - score_min))
    else:
        midpoint = float(score_min + score_max) / 2.0
        radius = float(score_max - score_min) / 2.0
        scaled = _clamp01(1.0 - ((pl.col("_offense_v2_score") - midpoint).abs() / radius))
    return ranked.with_columns(
        (pl.lit(float(min_mult)) + (scaled * float(max_mult - min_mult))).alias(mult_column)
    ).drop("_offense_v2_score", strict=False)


def _sort_for_mode(frame: pl.DataFrame, ranking_mode: str, primary_group_col: str) -> pl.DataFrame:
    if ranking_mode == "championship":
        return add_signal_score(frame, ranking_mode=ranking_mode).sort(
            by=[primary_group_col, "_signal_score", "rs_rank", "vol_mult", "range10", "symbol"],
            descending=[False, True, True, True, False, False],
        )
    if ranking_mode == "offense_v2":
        ranked = add_signal_score(frame, ranking_mode=ranking_mode)
        sort_cols = [primary_group_col, "_signal_score", "rs_rank", "contraction_quality", "range10", "symbol"]
        descending = [False, True, True, True, False, False]
        available_cols = [col for col in sort_cols if col in ranked.columns]
        available_desc = [desc for col, desc in zip(sort_cols, descending, strict=True) if col in ranked.columns]
        return ranked.sort(
            by=available_cols,
            descending=available_desc,
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

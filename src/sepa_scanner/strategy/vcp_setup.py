from __future__ import annotations

import polars as pl

from sepa_scanner.settings import SetupVcpSettings


def add_setup_columns(frame: pl.DataFrame, cfg: SetupVcpSettings) -> pl.DataFrame:
    result = frame.with_columns(
        (pl.col("range10") <= cfg.max_range10_pct).alias("range10_ok"),
        (pl.col("voldry") <= cfg.max_vol_dryup_ratio).alias("voldry_ok"),
        (pl.col("atr_pct_sma10") < pl.col("atr_pct_sma20")).alias("atr_trend_ok"),
        (
            pl.col("atr_pct_sma10") / pl.col("atr_pct_sma30")
            <= cfg.atr_contraction_factor
        ).alias("atr_contract_ok"),
        (
            pl.col("close")
            <= pl.col("sma50") * (1.0 + cfg.max_extension_from_50sma_pct)
        ).alias("extension_ok"),
        (pl.col("base_depth") <= cfg.base_max_depth_pct).alias("base_depth_ok"),
    )

    gate_cols = [
        "range10_ok",
        "voldry_ok",
        "atr_trend_ok",
        "atr_contract_ok",
        "extension_ok",
        "base_depth_ok",
    ]

    if "contraction_count" in result.columns:
        conds = []
        if cfg.contraction_min_count > 0:
            conds.append(pl.col("contraction_count") >= cfg.contraction_min_count)
        if cfg.contraction_max_count < 99:
            conds.append(pl.col("contraction_count") <= cfg.contraction_max_count)
            
        if conds:
            result = result.with_columns(
                pl.all_horizontal(*conds).alias("contraction_ok")
            )
            gate_cols.append("contraction_ok")

    # True-VCP gate: require successive contractions each >=20% tighter (Minervini).
    # Computed in add_contraction_columns but only enforced when configured.
    if cfg.require_progressive and "contraction_progressive" in result.columns:
        result = result.with_columns(
            pl.col("contraction_progressive").fill_null(False).alias("progressive_ok")
        )
        gate_cols.append("progressive_ok")

    result = result.with_columns(
        pl.all_horizontal(*gate_cols).alias("setup_pass")
    )

    # Advisory contraction tier (does NOT affect setup_pass)
    if "contraction_count" in result.columns:
        result = result.with_columns(
            pl.when(pl.col("contraction_count") >= cfg.contraction_standard_count)
            .then(pl.lit("premium"))
            .when(pl.col("contraction_count") >= cfg.contraction_min_count)
            .then(pl.lit("standard"))
            .otherwise(pl.lit("low"))
            .alias("contraction_tier"),
        )

    return result

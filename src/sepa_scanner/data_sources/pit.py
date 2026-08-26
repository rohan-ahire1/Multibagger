"""Point-in-time-safe panel construction and feature-input guards.

``processed`` preserves economically raw NSE observations with stable identity
where the source supplied an ISIN.  ``pit`` is the only panel allowed to feed
historical signal generation.  Retrospectively adjusted and research-label
columns are intentionally rejected at that boundary.
"""
from __future__ import annotations

import polars as pl


_REQUIRED_OHLCV = {"date", "symbol", "open", "high", "low", "close", "volume"}
_EX_POST_PREFIXES = ("fwd_", "forward_", "future_", "target_", "label_", "mfe", "mae")
_EX_POST_COLUMNS = {
    "split_adjusted_price",
    "split_adjusted_close",
    "total_return_price",
    "total_return_close",
    "canonical_exit_price",
    "canonical_return",
    "delisted_status",
    "future_index_membership",
}


def _require_ohlcv(frame: pl.DataFrame) -> None:
    missing = _REQUIRED_OHLCV - set(frame.columns)
    if missing:
        raise ValueError(f"OHLCV frame missing required columns: {sorted(missing)}")


def _with_identity(frame: pl.DataFrame) -> pl.DataFrame:
    """Add stable source identity without claiming one where no ISIN exists."""
    if "security_id" in frame.columns and "identity_quality" in frame.columns:
        return frame

    isin = pl.col("isin").cast(pl.Utf8).str.strip_chars() if "isin" in frame.columns else pl.lit(None, dtype=pl.Utf8)
    has_isin = isin.is_not_null() & (isin != "")
    series = pl.col("series").cast(pl.Utf8).fill_null("unknown") if "series" in frame.columns else pl.lit("unknown")
    fallback = pl.concat_str([pl.lit("fallback:"), pl.col("symbol").cast(pl.Utf8), pl.lit(":"), series])
    additions: list[pl.Expr] = []
    if "isin" not in frame.columns:
        additions.append(pl.lit(None, dtype=pl.Utf8).alias("isin"))
    additions.extend(
        [
            pl.when(has_isin)
            .then(pl.concat_str([pl.lit("isin:"), isin]))
            .otherwise(fallback)
            .alias("security_id"),
            pl.when(has_isin)
            .then(pl.lit("verified"))
            .otherwise(pl.lit("unverified"))
            .alias("identity_quality"),
        ]
    )
    return frame.with_columns(additions)


def build_processed_panel(raw_panel: pl.DataFrame) -> pl.DataFrame:
    """Return normalized, economically raw observations.

    ``raw_price`` is a convenience alias for raw close.  The original OHLCV
    columns remain untouched, and a missing historical ISIN gets a scoped
    fallback identity instead of an invented match to a later security.
    """
    _require_ohlcv(raw_panel)
    processed = _with_identity(raw_panel).with_columns(
        pl.col("close").cast(pl.Float64).alias("raw_price"),
        pl.lit("processed_raw").alias("data_layer"),
    )
    return processed.sort(["security_id", "date", "symbol"])


def build_pit_features(
    raw_panel: pl.DataFrame,
    *,
    corporate_actions: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Build the historical signal-input panel without retrospective actions.

    Corporate-action data is accepted only to make the as-of boundary explicit:
    this raw-price PIT layer never incorporates it.  Split-adjusted and
    total-return series belong to the ex-post ``adjusted`` layer and are for
    return measurement/research only.
    """
    del corporate_actions  # Never a signal input; retaining it would be leakage-prone.
    pit = build_processed_panel(raw_panel).with_columns(
        pl.col("raw_price").alias("pit_price"),
        pl.lit("pit_raw_price").alias("data_layer"),
    )
    return pit.sort(["security_id", "date", "symbol"])


def assert_pit_signal_inputs(frame: pl.DataFrame) -> None:
    """Reject columns whose value is explicitly ex-post by its contract/name."""
    bad = sorted(
        column
        for column in frame.columns
        if column.lower() in _EX_POST_COLUMNS
        or column.lower().startswith(_EX_POST_PREFIXES)
    )
    if bad:
        raise ValueError(
            "signal input contains explicitly ex-post column(s): " + ", ".join(bad)
        )

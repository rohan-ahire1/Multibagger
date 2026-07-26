"""Collapse ticker renames onto one identity using the ISIN.

The panel is keyed on ticker. When a company renames, its history splits into
two unrelated series, each truncated -- and any indicator needing a long lookback
silently sees a short one. 432 ISINs in this dataset trade under more than one
ticker, all of them cleanly sequential.

This deliberately does **not** touch the inverse problem: 537 tickers map to more
than one ISIN. Most of those are the same company after a face-value change,
where NSE reissues the ISIN, so splitting on ISIN would fragment a continuous
company. Distinguishing that from genuine ticker reuse needs evidence this
module does not have, so those are reported, never rewritten.
"""

from __future__ import annotations

import polars as pl


def build_rename_map(master: pl.DataFrame) -> dict[str, str]:
    """Map every superseded ticker to the security's most recent ticker.

    Chains are resolved transitively: if ``A -> B`` and ``B -> C`` then ``A``
    maps to ``C``, so no row is left pointing at an intermediate name.
    """
    eras = master.group_by(["isin", "symbol"]).agg(
        pl.col("first_seen").min(), pl.col("last_seen").max()
    )
    multi = eras.group_by("isin").agg(pl.len().alias("n")).filter(pl.col("n") > 1).select("isin")
    renamed = eras.join(multi, on="isin", how="inner")
    if renamed.is_empty():
        return {}

    canonical = (
        renamed.sort(["isin", "last_seen"])
        .group_by("isin", maintain_order=True)
        .last()
        .select("isin", pl.col("symbol").alias("canonical"))
    )
    pairs = (
        renamed.join(canonical, on="isin", how="inner")
        .filter(pl.col("symbol") != pl.col("canonical"))
        .select("symbol", "canonical")
    )

    direct = dict(zip(pairs["symbol"].to_list(), pairs["canonical"].to_list()))

    resolved: dict[str, str] = {}
    for source in direct:
        target = direct[source]
        seen = {source}
        while target in direct and target not in seen:
            seen.add(target)
            target = direct[target]
        resolved[source] = target
    return resolved


def overlapping_renames(master: pl.DataFrame) -> pl.DataFrame:
    """ISINs whose ticker eras overlap -- unsafe to merge, reported instead."""
    eras = (
        master.group_by(["isin", "symbol"])
        .agg(pl.col("first_seen").min(), pl.col("last_seen").max())
        .sort(["isin", "first_seen"])
    )
    return (
        eras.with_columns(
            pl.col("last_seen").shift(1).over("isin").alias("prev_last"),
            pl.col("symbol").shift(1).over("isin").alias("prev_symbol"),
        )
        .filter(pl.col("prev_last").is_not_null() & (pl.col("first_seen") <= pl.col("prev_last")))
        .select("isin", "prev_symbol", "symbol", "first_seen", "prev_last")
    )


def reused_tickers(master: pl.DataFrame) -> pl.DataFrame:
    """Tickers covering more than one ISIN, with the gap between the eras.

    A long gap suggests genuine reuse by an unrelated company -- a spliced
    series. A zero gap is almost always a face-value change on the same
    company. Reported for review; nothing is rewritten on this basis.
    """
    eras = (
        master.group_by(["symbol", "isin"])
        .agg(pl.col("first_seen").min(), pl.col("last_seen").max())
        .sort(["symbol", "first_seen"])
    )
    multi = eras.group_by("symbol").agg(pl.len().alias("n")).filter(pl.col("n") > 1).select("symbol")
    return (
        eras.join(multi, on="symbol", how="inner")
        .with_columns(
            pl.col("last_seen").shift(1).over("symbol").alias("prev_last"),
            pl.col("isin").shift(1).over("symbol").alias("prev_isin"),
        )
        .filter(pl.col("prev_last").is_not_null())
        .with_columns((pl.col("first_seen") - pl.col("prev_last")).dt.total_days().alias("gap_days"))
        .select("symbol", "prev_isin", "isin", "prev_last", "first_seen", "gap_days")
        .sort("gap_days", descending=True)
    )


def apply_canonical_symbols(frame: pl.DataFrame, mapping: dict[str, str]) -> pl.DataFrame:
    """Rewrite the ``symbol`` column through ``mapping``, leaving others alone."""
    if not mapping or "symbol" not in frame.columns:
        return frame
    return frame.with_columns(
        pl.col("symbol").replace(mapping).alias("symbol")
    )


def remap_corporate_actions(
    frame: pl.DataFrame,
    mapping: dict[str, str],
    *,
    override_sources: tuple[str, ...],
) -> pl.DataFrame:
    """Remap action tickers and re-apply override precedence after the merge.

    The NSE feed can record an action under the company's new ticker while an
    inferred override is keyed by the old ticker that was present in the raw
    panel. Those keys are distinct before canonicalisation, so the normal
    ``(symbol, ex_date)`` anti-join cannot see that they describe one event.
    Once both tickers collapse onto the same symbol, applying both factors
    double-adjusts the entire earlier history.

    An override states the total observed multiplier for its symbol-date, so it
    replaces every feed event on that canonical key. Multiple overrides for one
    canonical key are ambiguous and fail loudly instead of compounding.
    """
    mapped = apply_canonical_symbols(frame, mapping)
    if not mapping or "source" not in mapped.columns:
        return mapped

    overrides = mapped.filter(pl.col("source").is_in(override_sources))
    if overrides.is_empty():
        return mapped

    duplicate_overrides = (
        overrides.group_by(["symbol", "ex_date"])
        .len()
        .filter(pl.col("len") > 1)
    )
    if not duplicate_overrides.is_empty():
        keys = duplicate_overrides.select(
            pl.format("{}@{}", pl.col("symbol"), pl.col("ex_date")).alias("key")
        )["key"].to_list()
        raise ValueError(
            "multiple corporate-action overrides collapse onto canonical key(s) "
            f"{keys}; each override must state the total multiplier"
        )

    keys = overrides.select("symbol", "ex_date")
    feed = (
        mapped.filter(~pl.col("source").is_in(override_sources))
        .join(keys, on=["symbol", "ex_date"], how="anti")
    )
    return pl.concat([feed, overrides], how="diagonal")

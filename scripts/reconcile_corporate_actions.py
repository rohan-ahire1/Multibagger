#!/usr/bin/env python
"""Classify unexplained price discontinuities and emit the corporate actions.

Every large single-day price ratio in the adjusted panel is one of three
things: a corporate action the pipeline knows about but failed to apply, a
corporate action nobody recorded, or a genuine collapse that must be preserved
exactly as it is. Confusing the third for the second erases a real loss and
invents a recovery, so nothing is inferred without corroboration.

Buckets, in priority order:

``ca_known``
    A corporate action exists within ``--window`` days. The factor is known;
    failing to apply it is a pipeline bug, not a data gap.

``isin_corroborated``
    The security's ISIN changes within ``--window`` days of the jump. NSE
    reissues an ISIN when face value changes, so this is direct evidence of a
    capital action -- and a price crash never reissues an ISIN. Applies to
    equities and funds alike.

``etf_inferred``
    No ISIN change, but the instrument is a fund (ISIN prefix ``INF``) and the
    ratio sits within tolerance of a round split. NSE's equity corporate-action
    feed does not carry ETF splits, and a fund tracking gold or an index cannot
    genuinely fall 99% in a session.

``long_hiatus``
    The two adjacent observations are separated by more than
    ``--max-gap-days`` calendar days. This is a listing/resumption boundary,
    not evidence of a one-session corporate action, so it is recorded but
    never inferred.

``complex_action``
    NSE identifies a demerger, rights issue, or scheme of arrangement. A
    single-symbol price multiplier cannot reproduce the value and subsequent
    returns of the distributed security, so the event is recorded as reviewed
    and deliberately left unadjusted.

``manual``
    Everything else. Written to a review queue and left untouched.

A ratio matching more than one round candidate inside tolerance is never
guessed; it is sent to ``manual``.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import polars as pl

from sepa_scanner.data_sources.adjustment import event_price_multiplier
from sepa_scanner.data_sources.ca_purpose import parse_purpose

LOGGER = logging.getLogger("reconcile_ca")

ADJUSTED_PANEL = Path("data/nse_adjusted/ohlcv_all.parquet")
RAW_PANEL = Path("data/nse_raw/ohlcv_raw.parquet")
CA_FEED = Path("data/reference/nse_corporate_actions.parquet")
SECURITY_MASTER = Path("data/reference/security_master.parquet")
CA_OVERRIDES = Path("config/corporate_actions.csv")
MANUAL_QUEUE = Path("reports/data_audit/ca_manual_queue.csv")
CLASSIFIED = Path("reports/data_audit/ca_reconciliation.csv")

# Splits shrink the price; consolidations raise it. Both are round by nature.
SPLIT_DENOMINATORS = (2, 3, 4, 5, 8, 10, 20, 25, 50, 100, 1000)
CONSOLIDATION_MULTIPLES = (2, 3, 4, 5, 10)


def round_ratio_match(ratio: float, tolerance: float) -> tuple[str | None, float | None]:
    """Nearest round split ratio, or ``(None, None)`` if none or ambiguous."""
    hits: list[tuple[str, float]] = []
    for d in SPLIT_DENOMINATORS:
        target = 1.0 / d
        if abs(ratio - target) <= tolerance * target:
            hits.append((f"1:{d}", target))
    for m in CONSOLIDATION_MULTIPLES:
        target = float(m)
        if abs(ratio - target) <= tolerance * target:
            hits.append((f"{m}:1", target))
    if len(hits) != 1:
        return None, None
    return hits[0]


def all_ratios(panel: pl.DataFrame) -> pl.DataFrame:
    """Every close-to-close ratio, unfiltered. Needed to check small factors."""
    return (
        panel.sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol")).alias("ratio")
        )
        .select("symbol", "date", "close", "ratio")
        .filter(pl.col("ratio").is_not_null())
    )


def price_discontinuities(panel: pl.DataFrame, max_jump: float) -> pl.DataFrame:
    """Single-day ratios outside ``[1/max_jump, max_jump]``, plus bad prices."""
    d = panel.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol")).alias("ratio"),
        pl.col("date").shift(1).over("symbol").alias("prev_date"),
    ).with_columns(
        (pl.col("date") - pl.col("prev_date")).dt.total_days().alias("gap_days")
    )
    return d.filter(
        (
            pl.col("ratio").is_not_null()
            & ((pl.col("ratio") > max_jump) | (pl.col("ratio") < 1.0 / max_jump))
        )
        | (pl.col("close") <= 0)
    ).select("symbol", "prev_date", "date", "gap_days", "close", "ratio")


def isin_change_events(raw: pl.DataFrame) -> pl.DataFrame:
    """Dates on which a symbol's ISIN changed -- evidence of a capital action."""
    ordered = raw.select("symbol", "date", "isin").sort(["symbol", "date"])
    return (
        ordered.with_columns(pl.col("isin").shift(1).over("symbol").alias("prev_isin"))
        .filter(pl.col("prev_isin").is_not_null() & (pl.col("isin") != pl.col("prev_isin")))
        .select("symbol", pl.col("date").alias("isin_change_date"), "prev_isin", "isin")
    )


def _nearest_within(
    jumps: pl.DataFrame, events: pl.DataFrame, on: str, window: int, out: str
) -> pl.DataFrame:
    """Attach the nearest event date within ``window`` days, if any."""
    joined = (
        jumps.join(events, on="symbol", how="left")
        .with_columns((pl.col("date") - pl.col(on)).dt.total_days().abs().alias("_gap"))
        .filter(pl.col("_gap") <= window)
        .sort(["symbol", "date", "_gap"])
        .group_by(["symbol", "date"], maintain_order=True)
        .first()
        .select("symbol", "date", pl.col(on).alias(out))
    )
    return jumps.join(joined, on=["symbol", "date"], how="left")


def purpose_events(ca: pl.DataFrame) -> pl.DataFrame:
    """One parsed, full-event factor per symbol/date.

    NSE can publish separate bonus and face-value rows on the same ex-date.
    The adjusted panel may already contain the recognised leg, so
    ``parsed_residual`` is the factor still expected in that panel while
    ``parsed_mult`` is the full replacement factor written to the override.
    """
    rows: list[dict] = []
    for (symbol, ex_date), group in ca.group_by(["symbol", "ex_date"], maintain_order=True):
        parsed_actions = []
        purposes_in_order: list[str] = []
        seen_purposes: set[str] = set()
        for purpose in group["raw_purpose"].to_list():
            if not purpose or purpose in seen_purposes:
                continue
            seen_purposes.add(purpose)
            purposes_in_order.append(purpose)
            parsed = parse_purpose(purpose)
            if parsed is not None:
                parsed_actions.append(parsed)

        parsed_mult = None
        parsed_residual = None
        parsed_detail = None
        parsed_kind = None
        if parsed_actions:
            parsed_mult = 1.0
            for parsed in parsed_actions:
                parsed_mult *= parsed.multiplier

            applied_mult = 1.0
            for event in group.iter_rows(named=True):
                multiplier = event_price_multiplier(event)
                if multiplier is not None:
                    applied_mult *= multiplier
            parsed_residual = parsed_mult / applied_mult

            details = list(dict.fromkeys(parsed.detail for parsed in parsed_actions))
            kinds = {parsed.kind for parsed in parsed_actions}
            parsed_detail = " + ".join(details)
            parsed_kind = (
                "bonus_split"
                if "bonus_split" in kinds or (("bonus" in kinds) and ("split" in kinds))
                else next(iter(kinds))
            )

        rows.append(
            {
                "symbol": symbol,
                "ca_date": ex_date,
                "ca_action_type": " + ".join(
                    dict.fromkeys(group["action_type"].drop_nulls().to_list())
                ),
                "raw_purpose": " | ".join(purposes_in_order),
                "parsed_mult": parsed_mult,
                "parsed_residual": parsed_residual,
                "parsed_detail": parsed_detail,
                "parsed_kind": parsed_kind,
            }
        )

    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.String,
            "ca_date": pl.Date,
            "ca_action_type": pl.String,
            "raw_purpose": pl.String,
            "parsed_mult": pl.Float64,
            "parsed_residual": pl.Float64,
            "parsed_detail": pl.String,
            "parsed_kind": pl.String,
        },
    )


def classify(
    panel: pl.DataFrame,
    raw: pl.DataFrame,
    ca: pl.DataFrame,
    master: pl.DataFrame,
    *,
    window: int,
    tolerance: float,
    purpose_tolerance: float,
    max_gap_days: int,
    max_jump: float,
) -> pl.DataFrame:
    jumps = price_discontinuities(panel, max_jump)

    jumps = _nearest_within(
        jumps, ca.select("symbol", "ex_date").unique(), "ex_date", window, "ca_date"
    )
    jumps = _nearest_within(
        jumps,
        isin_change_events(raw).select("symbol", "isin_change_date").unique(),
        "isin_change_date",
        window,
        "isin_change_date",
    )

    classes = master.select("symbol", "instrument_class").unique(subset=["symbol"])
    jumps = jumps.join(classes, on="symbol", how="left")

    # Attach the nearby CA's free text. A large share of the feed is stored as
    # action_type="unparsed" with only a purpose string, so the event is known
    # but carries no usable factor -- the adjustment silently does nothing.
    purposes = purpose_events(ca)
    jumps = jumps.join(purposes, on=["symbol", "ca_date"], how="left")

    matched = [round_ratio_match(r, tolerance) for r in jumps["ratio"].to_list()]
    jumps = jumps.with_columns(
        pl.Series("round_label", [m[0] for m in matched], dtype=pl.String),
        pl.Series("round_ratio", [m[1] for m in matched], dtype=pl.Float64),
    )

    # The parsed multiplier is only trusted when the observed price move agrees
    # with it. Text alone could be a stale or misdated filing; price alone
    # could be a real collapse. Together they corroborate each other.
    agrees = pl.col("parsed_residual").is_not_null() & (
        (pl.col("parsed_residual") - pl.col("ratio")).abs()
        <= purpose_tolerance * pl.col("parsed_residual").abs()
    )
    complex_action = pl.col("raw_purpose").str.to_lowercase().str.contains(
        r"demerger|scheme\s+of\s+arrangement|rights"
    )

    return jumps.with_columns(
        pl.when(pl.col("gap_days") > max_gap_days)
        .then(pl.lit("long_hiatus"))
        .when(complex_action)
        .then(pl.lit("complex_action"))
        .when(agrees)
        .then(pl.lit("purpose_parsed"))
        .when(pl.col("isin_change_date").is_not_null())
        .then(pl.lit("isin_corroborated"))
        .when((pl.col("instrument_class") == "fund") & pl.col("round_label").is_not_null())
        .then(pl.lit("etf_inferred"))
        .otherwise(pl.lit("manual"))
        .alias("bucket")
    ).sort("ratio")


def inferred_ca_rows(classified: pl.DataFrame) -> pl.DataFrame:
    """Corporate-action override rows for the buckets we are willing to infer.

    ``isin_corroborated`` jumps without a clean round ratio still get the
    observed ratio: the ISIN change proves an action occurred, and the observed
    price ratio is the best available estimate of its size.
    """
    inferable = classified.filter(
        pl.col("bucket").is_in(["purpose_parsed", "isin_corroborated", "etf_inferred"])
    )
    return inferable.select(
        pl.col("symbol"),
        pl.lit("").alias("isin"),
        pl.coalesce([pl.col("parsed_kind"), pl.lit("split")]).alias("action_type"),
        # Use the feed's ex-date when there is one. The override must land on
        # the same date as the event it replaces, or the de-dup anti-join
        # leaves the feed row in place and the symbol is adjusted twice.
        pl.coalesce([pl.col("ca_date"), pl.col("date")]).alias("ex_date"),
        pl.lit(None, dtype=pl.Int64).alias("ratio_num"),
        pl.lit(None, dtype=pl.Int64).alias("ratio_den"),
        pl.lit(None, dtype=pl.Float64).alias("old_face_value"),
        pl.lit(None, dtype=pl.Float64).alias("new_face_value"),
        pl.coalesce([pl.col("parsed_mult"), pl.col("round_ratio"), pl.col("ratio")]).alias("adjustment_factor"),
        pl.format(
            "{} inferred from {} (observed ratio {})",
            pl.coalesce([pl.col("parsed_detail"), pl.col("round_label"), pl.lit("non-round action")]),
            pl.col("bucket"),
            pl.col("ratio").round(6).cast(pl.String),
        ).alias("description"),
        pl.col("bucket").alias("source"),
    )


def merge_inferred_overrides(
    overrides: pl.DataFrame, inferred: pl.DataFrame
) -> pl.DataFrame:
    """Upsert newly inferred events without deleting unrelated old evidence."""
    replace_keys = inferred.select("symbol", "ex_date").unique()
    keep = overrides.join(replace_keys, on=["symbol", "ex_date"], how="anti")

    # The CSV infers Int64 for all-null numeric columns; align both sides so
    # concat cannot fail on a column that is empty in one of them.
    for column, dtype in (
        ("ratio_num", pl.Float64),
        ("ratio_den", pl.Float64),
        ("old_face_value", pl.Float64),
        ("new_face_value", pl.Float64),
        ("adjustment_factor", pl.Float64),
        ("isin", pl.String),
        ("description", pl.String),
        ("source", pl.String),
    ):
        if column in keep.columns:
            keep = keep.with_columns(pl.col(column).cast(dtype))
        if column in inferred.columns:
            inferred = inferred.with_columns(pl.col(column).cast(dtype))

    return (
        pl.concat([keep, inferred], how="diagonal")
        .unique(subset=["symbol", "ex_date"], keep="last")
        .sort(["symbol", "ex_date"])
    )


def validate_seed_rows(overrides: pl.DataFrame, raw_jumps: pl.DataFrame, window: int) -> pl.DataFrame:
    """Seed rows must sit near a discontinuity in the RAW prices, or they are fiction.

    This has to be checked against raw prices, not adjusted ones: a correctly
    applied corporate action leaves no jump in the adjusted panel, so testing
    there marks every working row as unsupported.
    """
    seeds = overrides.filter(pl.col("source") == "seed")
    if seeds.is_empty():
        return seeds.with_columns(pl.lit(True).alias("corroborated"))

    # Test each row against its OWN stated factor, not a generic large-jump
    # list. A 1:2 split or 1:1 bonus moves price by 0.5, which sits inside any
    # sane large-jump threshold and would otherwise look like no evidence at
    # all -- marking correct rows as fiction.
    ratios = raw_jumps.select("symbol", pl.col("date").alias("jump_date"), "ratio")
    joined = (
        seeds.join(ratios, on="symbol", how="left")
        .with_columns(
            (pl.col("ex_date") - pl.col("jump_date")).dt.total_days().abs().alias("gap"),
            (pl.col("ratio") - pl.col("adjustment_factor")).abs().alias("factor_err"),
        )
        .filter(pl.col("gap") <= window)
        .sort("factor_err")
        .group_by("symbol", maintain_order=True)
        .first()
        .select("symbol", pl.col("ratio").alias("observed_ratio"), "gap", "factor_err")
    )
    return seeds.join(joined, on="symbol", how="left").with_columns(
        (
            pl.col("observed_ratio").is_not_null()
            & (pl.col("factor_err") <= 0.05 * pl.col("adjustment_factor"))
        ).alias("corroborated")
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", type=int, default=3, help="days to associate an event")
    ap.add_argument("--tolerance", type=float, default=0.03, help="round-ratio tolerance")
    ap.add_argument(
        "--purpose-tolerance",
        type=float,
        default=0.25,
        help="relative price agreement required for a factor parsed from NSE purpose text",
    )
    ap.add_argument(
        "--max-gap-days",
        type=int,
        default=10,
        help="adjacent observations farther apart are listing/resumption boundaries",
    )
    ap.add_argument("--max-jump", type=float, default=4.0)
    ap.add_argument("--write", action="store_true", help="append inferred rows to the overrides CSV")
    args = ap.parse_args()

    panel = pl.read_parquet(ADJUSTED_PANEL)
    raw = pl.read_parquet(RAW_PANEL)
    ca = pl.read_parquet(CA_FEED)
    master = pl.read_parquet(SECURITY_MASTER)

    classified = classify(
        panel, raw, ca, master,
        window=args.window,
        tolerance=args.tolerance,
        purpose_tolerance=args.purpose_tolerance,
        max_gap_days=args.max_gap_days,
        max_jump=args.max_jump,
    )

    CLASSIFIED.parent.mkdir(parents=True, exist_ok=True)
    classified.write_csv(CLASSIFIED)

    counts = classified.group_by("bucket").len().sort("len", descending=True)
    LOGGER.info("classified %d discontinuities -> %s", classified.height, CLASSIFIED)
    for row in counts.iter_rows(named=True):
        LOGGER.info("  %-18s %d", row["bucket"], row["len"])

    manual = classified.filter(pl.col("bucket") == "manual").sort("ratio")
    manual.write_csv(MANUAL_QUEUE)
    LOGGER.info("manual review queue: %d rows -> %s", manual.height, MANUAL_QUEUE)

    overrides = pl.read_csv(CA_OVERRIDES, try_parse_dates=True)
    seeds = validate_seed_rows(overrides, all_ratios(raw), args.window)
    if not seeds.is_empty():
        bad = seeds.filter(~pl.col("corroborated"))
        LOGGER.info(
            "seed rows: %d total, %d uncorroborated", seeds.height, bad.height
        )
        for row in bad.iter_rows(named=True):
            LOGGER.warning(
                "  UNCORROBORATED seed: %s %s %s stated_factor=%s observed_ratio=%s",
                row["symbol"], row["action_type"], row["ex_date"], row["adjustment_factor"], row["observed_ratio"],
            )

    inferred = inferred_ca_rows(classified)
    LOGGER.info("inferable rows: %d", inferred.height)

    if args.write:
        merged = merge_inferred_overrides(overrides, inferred)
        merged.write_csv(CA_OVERRIDES)
        LOGGER.info("wrote %d override rows -> %s", merged.height, CA_OVERRIDES)
    else:
        LOGGER.info("dry run; pass --write to update %s", CA_OVERRIDES)


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Build an ISIN-keyed security master from the raw bhavcopy panel.

The panel is keyed on ticker, so a renamed symbol silently becomes two
companies and a reused ticker silently merges two. The ISIN is carried in every
bhavcopy row and is stable across renames, so it is the identity the rest of
the pipeline should key on.

It also gives a deterministic instrument classification. NSE ISINs prefix fund
units with ``INF`` and equities with ``INE``; government securities use ``IN0``
and ``IN9``. That distinction matters because NSE's equity corporate-action
feed does not carry ETF splits -- the reason NIFTYBEES's 2019-12-19 1:10 split
was invisible to the adjustment pipeline.

Writes ``data/reference/security_master.parquet``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl

LOGGER = logging.getLogger("security_master")

RAW_PANEL = Path("data/nse_raw/ohlcv_raw.parquet")
OUT_PATH = Path("data/reference/security_master.parquet")


def classify_isin(column: str = "isin") -> pl.Expr:
    """Instrument class from the ISIN prefix, per NSE's allocation scheme."""
    prefix = pl.col(column).str.slice(0, 3)
    return (
        pl.when(prefix == "INE")
        .then(pl.lit("equity"))
        .when(prefix == "INF")
        .then(pl.lit("fund"))
        .when(prefix.is_in(["IN0", "IN9"]))
        .then(pl.lit("govt"))
        .when(pl.col(column).is_null() | (pl.col(column).str.len_chars() == 0))
        .then(pl.lit("unknown"))
        .otherwise(pl.lit("other"))
        .alias("instrument_class")
    )


def build_security_master(raw: pl.DataFrame) -> pl.DataFrame:
    """One row per (isin, symbol, series) with the window it was observed in."""
    usable = raw.filter(
        pl.col("isin").is_not_null() & (pl.col("isin").str.len_chars() > 0)
    )
    return (
        usable.group_by(["isin", "symbol", "series"])
        .agg(
            pl.col("date").min().alias("first_seen"),
            pl.col("date").max().alias("last_seen"),
            pl.len().alias("sessions"),
        )
        .with_columns(classify_isin())
        .sort(["isin", "first_seen", "symbol"])
    )


def rename_map(master: pl.DataFrame) -> pl.DataFrame:
    """ISINs seen under more than one ticker -- the renames the panel misses."""
    return (
        master.group_by("isin")
        .agg(
            pl.col("symbol").unique().sort().alias("symbols"),
            pl.col("symbol").n_unique().alias("symbol_count"),
            pl.col("first_seen").min().alias("first_seen"),
            pl.col("last_seen").max().alias("last_seen"),
        )
        .filter(pl.col("symbol_count") > 1)
        .sort("symbol_count", descending=True)
    )


def reused_tickers(master: pl.DataFrame) -> pl.DataFrame:
    """Tickers mapping to more than one ISIN -- distinct companies, one symbol."""
    return (
        master.group_by("symbol")
        .agg(
            pl.col("isin").unique().sort().alias("isins"),
            pl.col("isin").n_unique().alias("isin_count"),
        )
        .filter(pl.col("isin_count") > 1)
        .sort("isin_count", descending=True)
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=RAW_PANEL)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    raw = pl.read_parquet(args.raw)
    master = build_security_master(raw)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    master.write_parquet(args.out, compression="zstd")

    classes = master.group_by("instrument_class").agg(
        pl.col("isin").n_unique().alias("isins")
    ).sort("isins", descending=True)
    renames = rename_map(master)
    reused = reused_tickers(master)

    LOGGER.info("security master: %d rows -> %s", master.height, args.out)
    LOGGER.info("distinct ISINs: %d", master["isin"].n_unique())
    LOGGER.info("distinct symbols: %d", master["symbol"].n_unique())
    for row in classes.iter_rows(named=True):
        LOGGER.info("  %-8s %d", row["instrument_class"], row["isins"])
    LOGGER.info("ISINs traded under >1 ticker (renames): %d", renames.height)
    LOGGER.info("tickers mapping to >1 ISIN (reuse): %d", reused.height)


if __name__ == "__main__":
    main()

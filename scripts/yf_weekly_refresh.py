"""Weekly full refresh: re-download all history to pick up retroactive adjustments.

Run on weekends when the market is closed.

Usage:
    uv run python scripts/yf_weekly_refresh.py
    uv run python scripts/yf_weekly_refresh.py --start 2010-01-01
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import polars as pl

from yf_bulk_download import DEFAULT_OUTPUT, download_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def weekly_refresh(store_path: Path = DEFAULT_OUTPUT, start: str = "2010-01-01") -> None:
    if store_path.exists():
        before = pl.read_parquet(store_path)
        before_rows = before.height
        before_symbols = before["symbol"].n_unique()
        symbols = sorted(before["symbol"].unique().to_list())
        log.info("Existing store: %d rows, %d symbols", before_rows, before_symbols)
    else:
        log.error("Store not found at %s — run yf_bulk_download.py first", store_path)
        sys.exit(1)

    download_universe(symbols, start=start, output=store_path)

    after = pl.read_parquet(store_path)
    log.info(
        "Refresh complete: %d -> %d rows (%+d), %d symbols",
        before_rows,
        after.height,
        after.height - before_rows,
        after["symbol"].n_unique(),
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Weekly yfinance full refresh")
    parser.add_argument("--start", default="2010-01-01", help="Start date YYYY-MM-DD (default: 2010-01-01)")
    parser.add_argument("--store", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    weekly_refresh(store_path=args.store, start=args.start)


if __name__ == "__main__":
    main()

"""Append yesterday's OHLCV bar from yfinance to the adjusted parquet store.

Run after market close (e.g. 16:30 IST).

Usage:
    uv run python scripts/yf_daily_update.py
    uv run python scripts/yf_daily_update.py --date 2026-03-14
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from sepa_scanner.data_sources.yfinance_adjusted import download_adjusted_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = PROJECT_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"


def daily_update(store_path: Path = DEFAULT_STORE, target_date: str | None = None) -> None:
    if not store_path.exists():
        log.error("Store not found at %s — run yf_bulk_download.py first", store_path)
        sys.exit(1)

    existing = pl.read_parquet(store_path)
    symbols = sorted(existing["symbol"].unique().to_list())
    log.info("Updating %d symbols", len(symbols))

    if target_date:
        end_dt = datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)
        start_dt = datetime.strptime(target_date, "%Y-%m-%d")
    else:
        end_dt = datetime.now() + timedelta(days=1)
        start_dt = end_dt - timedelta(days=4)

    new_data, failed = download_adjusted_history(
        symbols,
        start=start_dt.date(),
        end=end_dt.date(),
    )

    if new_data.is_empty():
        log.info("No new bars extracted")
        return

    new_dates = new_data["date"].unique().sort().to_list()
    combined = pl.concat([
        existing.filter(~pl.col("date").is_in(new_dates)),
        new_data,
    ]).unique(subset=["date", "symbol"], keep="last").sort(["symbol", "date"])

    combined.write_parquet(store_path, compression="zstd")
    added = combined.height - existing.height
    log.info("Updated store: %+d rows (total %d)", added, combined.height)
    if failed:
        log.warning("Unavailable symbols (%d): %s", len(failed), ", ".join(sorted(failed)))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Daily yfinance OHLCV update")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args()
    daily_update(store_path=args.store, target_date=args.date)


if __name__ == "__main__":
    main()

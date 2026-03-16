"""Compare yfinance close prices against Bhavcopy for the latest trading day.

Current-day prices should match exactly (no historical adjustment applies).
Run weekly as a data quality check.

Usage:
    uv run python scripts/validate_bhavcopy_vs_yf.py
    uv run python scripts/validate_bhavcopy_vs_yf.py --date 2026-03-14 --tolerance 0.02
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import polars as pl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
YF_STORE = PROJECT_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"
BHAVCOPY_STORE = PROJECT_ROOT / "data" / "ohlcv_parquet"


def validate(target_date: date | None = None, tolerance: float = 0.01) -> None:
    if not YF_STORE.exists():
        log.error("yfinance store not found at %s", YF_STORE)
        sys.exit(1)
    if not any(BHAVCOPY_STORE.rglob("*.parquet")):
        log.error("Bhavcopy store is empty at %s", BHAVCOPY_STORE)
        sys.exit(1)

    yf_df = pl.read_parquet(YF_STORE)
    bhav_df = pl.scan_parquet(
        str(BHAVCOPY_STORE / "**/*.parquet"), hive_partitioning=True
    ).collect()

    if target_date is None:
        common_dates = set(yf_df["date"].unique().to_list()) & set(bhav_df["date"].unique().to_list())
        if not common_dates:
            log.error("No overlapping dates between yfinance and Bhavcopy")
            sys.exit(1)
        target_date = max(common_dates)

    log.info("Validating date: %s", target_date)

    yf_day = yf_df.filter(pl.col("date") == target_date).select("symbol", "close").rename({"close": "yf_close"})
    bhav_day = bhav_df.filter(pl.col("date") == target_date).select("symbol", "close").rename({"close": "bhav_close"})

    merged = yf_day.join(bhav_day, on="symbol", how="inner")
    if merged.is_empty():
        log.warning("No common symbols on %s", target_date)
        return

    merged = merged.with_columns(
        ((pl.col("yf_close") - pl.col("bhav_close")).abs() / pl.col("bhav_close")).alias("pct_diff")
    )

    divergent = merged.filter(pl.col("pct_diff") > tolerance).sort("pct_diff", descending=True)

    log.info(
        "Compared %d symbols. Divergent (>%.1f%%): %d",
        merged.height,
        tolerance * 100,
        divergent.height,
    )

    if divergent.height > 0:
        log.warning("Divergent symbols:")
        for row in divergent.head(20).to_dicts():
            log.warning(
                "  %s: yf=%.2f bhav=%.2f diff=%.2f%%",
                row["symbol"],
                row["yf_close"],
                row["bhav_close"],
                row["pct_diff"] * 100,
            )
    else:
        log.info("All prices match within tolerance")


def main() -> None:
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description="Validate yfinance vs Bhavcopy prices")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: latest common)")
    parser.add_argument("--tolerance", type=float, default=0.01, help="Max acceptable % diff (default: 1%%)")
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    validate(target_date=target, tolerance=args.tolerance)


if __name__ == "__main__":
    main()

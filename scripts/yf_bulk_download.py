"""Bulk-download adjusted OHLCV from yfinance for the full universe.

Usage:
    uv run python scripts/yf_bulk_download.py --symbols-from auto
    uv run python scripts/yf_bulk_download.py --symbols-from config
    uv run python scripts/yf_bulk_download.py --symbols-from store
    uv run python scripts/yf_bulk_download.py --symbols-from file --symbols-file symbols.txt
    uv run python scripts/yf_bulk_download.py --symbols RELIANCE INFY TCS
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import polars as pl

from sepa_scanner.data_sources.yfinance_adjusted import download_adjusted_history
from sepa_scanner.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"


def _symbols_from_file(path: Path) -> list[str]:
    return sorted({line.strip() for line in path.read_text().splitlines() if line.strip()})


def _symbols_from_store(path: Path) -> list[str]:
    if not path.exists():
        log.error("yfinance store not found at %s", path)
        sys.exit(1)
    df = pl.read_parquet(path)
    return sorted(df["symbol"].unique().to_list())


def _symbols_from_config() -> list[str]:
    settings = load_settings()
    if not settings.manual_universe:
        log.error("config/universe.yaml is empty; add the full symbol universe first")
        sys.exit(1)
    return sorted(set(settings.manual_universe))


def _symbols_from_auto(output: Path) -> list[str]:
    settings = load_settings()
    if settings.manual_universe:
        return sorted(set(settings.manual_universe))
    if output.exists():
        return _symbols_from_store(output)
    log.error(
        "No symbol universe found. Populate config/universe.yaml or provide --symbols-from file/args."
    )
    sys.exit(1)


def download_universe(
    symbols: list[str],
    start: str = "2010-01-01",
    output: Path = DEFAULT_OUTPUT,
) -> None:
    log.info("Downloading %d symbols from %s", len(symbols), start)
    frame, failed = download_adjusted_history(
        symbols,
        start=date.fromisoformat(start),
    )
    if frame.is_empty():
        log.error("No data extracted for any symbol")
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(output, compression="zstd")

    log.info(
        "Wrote %d rows (%d symbols) to %s",
        frame.height,
        frame["symbol"].n_unique(),
        output,
    )
    if failed:
        log.warning(
            "Unavailable symbols (%d): %s", len(failed), ", ".join(sorted(failed)),
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Bulk download yfinance adjusted OHLCV")
    parser.add_argument(
        "--symbols-from",
        choices=["auto", "config", "store", "file", "args"],
        default="auto",
        help="Source of symbol list (default: auto)",
    )
    parser.add_argument("--symbols-file", type=Path, help="Text file with one symbol per line")
    parser.add_argument("--symbols", nargs="*", help="Explicit symbol list")
    parser.add_argument("--start", default="2010-01-01", help="Start date YYYY-MM-DD (default: 2010-01-01)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.symbols_from == "auto":
        symbols = _symbols_from_auto(args.output)
    elif args.symbols_from == "config":
        symbols = _symbols_from_config()
    elif args.symbols_from == "store":
        symbols = _symbols_from_store(args.output)
    elif args.symbols_from == "file":
        if not args.symbols_file:
            parser.error("--symbols-file required when --symbols-from=file")
        symbols = _symbols_from_file(args.symbols_file)
    else:
        if not args.symbols:
            parser.error("--symbols required when --symbols-from=args")
        symbols = sorted(args.symbols)

    download_universe(symbols, start=args.start, output=args.output)


if __name__ == "__main__":
    main()

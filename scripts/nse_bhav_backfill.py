"""Backfill the full daily NSE bhavcopy archive (survivorship-free raw OHLCV).

Downloads every trading session's bhavcopy zip into an immutable raw store, then
(optionally) builds the raw parquet panel. Resumable: existing zips are skipped unless
``--force``. Politeness throttled; missing/failed sessions are logged, never fabricated.

Usage:
    uv run python scripts/nse_bhav_backfill.py --start 2005-01-01
    uv run python scripts/nse_bhav_backfill.py --start 2024-01-01 --end 2024-01-31 --build
"""
from __future__ import annotations

import argparse
import csv
import logging
from datetime import date, datetime
from pathlib import Path

from tqdm import tqdm

from sepa_scanner.calendar import trading_days_between
from sepa_scanner.data_sources.nse_bhavcopy import NseBhavcopyClient
from sepa_scanner.settings import AppSettings, load_settings
from sepa_scanner.storage.bhav_raw_store import BhavRawStore
from sepa_scanner.utils.io import ensure_directory

LOGGER = logging.getLogger("nse_bhav_backfill")

RAW_ZIP_ROOT = Path("data/nse_bhavcopy_raw")
RAW_PANEL_ROOT = Path("data/nse_raw")
LOG_PATH = Path("reports/data_audit/bhav_backfill_log.csv")


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def backfill(
    settings: AppSettings,
    start: date,
    end: date,
    *,
    force: bool,
    sleep_seconds: float,
) -> tuple[int, int, list[date]]:
    client = NseBhavcopyClient(settings)
    client.bootstrap()
    sessions = trading_days_between(start, end)
    ensure_directory(LOG_PATH.parent)

    downloaded = 0
    cached = 0
    missing: list[date] = []
    with LOG_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["trade_date", "status", "path"])
        progress = tqdm(
            sessions,
            desc="bhavcopy backfill",
            unit="session",
            dynamic_ncols=True,
            smoothing=0.05,  # steadier ETA over a long, network-bound run
        )
        for trade_date in progress:
            progress.set_postfix_str(
                f"{trade_date} | dl={downloaded} cache={cached} miss={len(missing)}"
            )
            year_dir = RAW_ZIP_ROOT / f"{trade_date.year}"
            ensure_directory(year_dir)
            target = year_dir / f"{trade_date.isoformat()}.zip"
            if target.exists() and not force:
                cached += 1
                writer.writerow([trade_date.isoformat(), "cached", str(target)])
                continue
            try:
                result = client.download_for_date(
                    trade_date, year_dir, force=force, sleep_seconds=sleep_seconds
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("%s download error: %s", trade_date, exc)
                missing.append(trade_date)
                writer.writerow([trade_date.isoformat(), "error", ""])
                continue
            if result is None:
                # 404 = holiday or not-yet-published; expected, not fatal.
                missing.append(trade_date)
                writer.writerow([trade_date.isoformat(), "missing", ""])
                continue
            path, _meta = result
            downloaded += 1
            writer.writerow([trade_date.isoformat(), "downloaded", str(path)])

    LOGGER.info(
        "Backfill done: %d downloaded, %d cached, %d missing (of %d sessions)",
        downloaded,
        cached,
        len(missing),
        len(sessions),
    )
    return downloaded, cached, missing


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_date, default=date(2005, 1, 1))
    parser.add_argument("--end", type=_parse_date, default=date.today())
    parser.add_argument("--force", action="store_true", help="re-download existing zips")
    parser.add_argument("--sleep", type=float, default=0.4, help="throttle seconds/request")
    parser.add_argument("--build", action="store_true", help="build raw panel after download")
    parser.add_argument(
        "--series", default="EQ,BE", help="comma series to keep when building panel"
    )
    args = parser.parse_args()

    settings = load_settings()
    backfill(settings, args.start, args.end, force=args.force, sleep_seconds=args.sleep)

    if args.build:
        store = BhavRawStore(RAW_PANEL_ROOT)
        panel = store.build_raw_panel(
            RAW_ZIP_ROOT, allowed_series=[s.strip() for s in args.series.split(",")]
        )
        LOGGER.info(
            "Raw panel written to %s: %d rows, %d symbols",
            store.flat_path,
            panel.height,
            panel["symbol"].n_unique(),
        )


if __name__ == "__main__":
    main()

"""Raw (unadjusted) daily OHLCV panel built from NSE bhavcopy zips.

This is the survivorship-free source: NSE bhavcopy is the as-of-date record of every
symbol trading each session, including names later delisted/merged. Prices here are RAW
(unadjusted) -- corporate-action adjustment is applied downstream in
``sepa_scanner.data_sources.adjustment``.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl
from tqdm import tqdm

from sepa_scanner.data_sources.nse_bhavcopy import (
    LEGACY_CUTOFF,
    parse_bhavcopy_zip,
    parse_legacy_bhavcopy_zip,
)
from sepa_scanner.utils.io import ensure_directory

LOGGER = logging.getLogger(__name__)

RAW_COLUMNS = [
    "date",
    "symbol",
    "series",
    "isin",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


def _zip_trade_date(path: Path) -> date | None:
    """Trade date is the zip stem (``YYYY-MM-DD.zip``), written by the backfill."""
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def parse_zip(path: Path, allowed_series: list[str]) -> pl.DataFrame | None:
    """Parse one bhavcopy zip, dispatching on the legacy/UDiFF format cutoff."""
    trade_date = _zip_trade_date(path)
    try:
        if trade_date is not None and trade_date < LEGACY_CUTOFF:
            frame = parse_legacy_bhavcopy_zip(path, allowed_series)
        else:
            # Try the modern parser first; fall back to legacy on schema mismatch.
            try:
                frame = parse_bhavcopy_zip(path, allowed_series)
            except Exception:
                frame = parse_legacy_bhavcopy_zip(path, allowed_series)
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill the build
        LOGGER.warning("Failed to parse %s: %s", path, exc)
        return None
    if frame.is_empty():
        return None
    return frame.select([c for c in RAW_COLUMNS if c in frame.columns])


class BhavRawStore:
    """Builds/reads ``ohlcv_raw.parquet`` from a directory tree of bhavcopy zips."""

    FLAT_FILE = "ohlcv_raw.parquet"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        ensure_directory(self.root)

    @property
    def flat_path(self) -> Path:
        return self.root / self.FLAT_FILE

    def build_raw_panel(
        self,
        zip_dir: Path,
        *,
        allowed_series: list[str] | None = None,
    ) -> pl.DataFrame:
        """Parse every zip under ``zip_dir`` into one raw panel and persist it."""
        allowed_series = allowed_series or ["EQ", "BE"]
        zips = sorted(Path(zip_dir).rglob("*.zip"))
        if not zips:
            raise FileNotFoundError(f"no bhavcopy zips under {zip_dir}")

        frames: list[pl.DataFrame] = []
        parsed = 0
        for path in tqdm(zips, desc="parse bhavcopy", unit="zip", dynamic_ncols=True):
            frame = parse_zip(path, allowed_series)
            if frame is not None:
                frames.append(frame)
                parsed += 1
        if not frames:
            raise RuntimeError("no zips parsed successfully")

        panel = (
            pl.concat(frames, how="diagonal")
            .unique(subset=["date", "symbol"], keep="last")
            .sort(["symbol", "date"])
        )
        panel.write_parquet(self.flat_path, compression="zstd")
        LOGGER.info(
            "Built raw panel: %d rows, %d symbols, %d/%d zips parsed",
            panel.height,
            panel["symbol"].n_unique(),
            parsed,
            len(zips),
        )
        return panel

    def read_frame(self) -> pl.DataFrame:
        if not self.flat_path.exists():
            raise FileNotFoundError(self.flat_path)
        return pl.read_parquet(self.flat_path)

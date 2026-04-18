from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from sepa_scanner.data_sources.nse_bhavcopy import (
    LEGACY_CUTOFF,
    NseBhavcopyClient,
    parse_bhavcopy_zip,
    parse_legacy_bhavcopy_zip,
)
from sepa_scanner.settings import AppSettings
from sepa_scanner.utils.io import ensure_directory


@dataclass(frozen=True, slots=True)
class UniverseBuildResult:
    output_path: Path
    symbol_count: int
    snapshots_requested: int
    snapshots_loaded: int


def build_universe_from_bhavcopy_snapshots(
    *,
    settings: AppSettings,
    raw_dir: Path,
    start: date,
    end: date,
    cadence: str,
    output_path: Path,
    final_only: bool = True,
    force: bool = False,
    sleep_seconds: float = 0.0,
) -> UniverseBuildResult:
    """Build a broad NSE symbol list from historical EQ bhavcopy snapshots.

    This discovers symbols only. yfinance remains the canonical OHLCV source for
    scanner and backtest data.
    """
    if start > end:
        raise ValueError("start must be on or before end")

    snapshot_dates = _snapshot_dates(start, end, cadence)
    client = NseBhavcopyClient(settings)
    ensure_directory(raw_dir)

    symbols: set[str] = {settings.market.benchmark_symbol}
    loaded_trade_dates: set[date] = set()

    for snapshot_date in snapshot_dates:
        lookback_days = 0 if cadence == "daily" else 7
        downloaded = _download_snapshot(
            client,
            snapshot_date,
            raw_dir,
            final_only=final_only,
            force=force,
            sleep_seconds=sleep_seconds,
            lookback_days=lookback_days,
            lower_bound=start,
        )
        if downloaded is None:
            continue

        trade_date, zip_path = downloaded
        if trade_date in loaded_trade_dates:
            continue

        frame = _parse_snapshot(
            zip_path,
            trade_date=trade_date,
            allowed_series=settings.universe.allowed_series,
        )
        if not frame.is_empty():
            symbols.update(frame["symbol"].unique().to_list())
            loaded_trade_dates.add(trade_date)

    _write_universe_yaml(
        output_path,
        symbols=sorted(symbols),
        start=start,
        end=end,
        cadence=cadence,
        snapshots_requested=len(snapshot_dates),
        snapshots_loaded=len(loaded_trade_dates),
    )

    return UniverseBuildResult(
        output_path=output_path,
        symbol_count=len(symbols),
        snapshots_requested=len(snapshot_dates),
        snapshots_loaded=len(loaded_trade_dates),
    )


def _snapshot_dates(start: date, end: date, cadence: str) -> list[date]:
    cadence = cadence.lower()
    if cadence not in {"daily", "weekly", "monthly", "quarterly"}:
        raise ValueError("cadence must be one of: daily, weekly, monthly, quarterly")

    if cadence == "daily":
        days: list[date] = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
        return days

    if cadence == "weekly":
        days = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=7)
        return days

    months_step = 3 if cadence == "quarterly" else 1
    days = []
    year = start.year
    month = start.month
    while date(year, month, 1) <= end:
        last_day = date(year, month, monthrange(year, month)[1])
        if last_day >= start:
            days.append(min(last_day, end))
        month += months_step
        while month > 12:
            year += 1
            month -= 12
    return days


def _download_snapshot(
    client: NseBhavcopyClient,
    snapshot_date: date,
    raw_dir: Path,
    *,
    final_only: bool,
    force: bool,
    sleep_seconds: float,
    lookback_days: int,
    lower_bound: date,
) -> tuple[date, Path] | None:
    for offset in range(lookback_days + 1):
        trade_date = snapshot_date - timedelta(days=offset)
        if trade_date < lower_bound:
            return None
        result = client.download_for_date(
            trade_date,
            raw_dir,
            final_only=final_only,
            force=force,
            sleep_seconds=sleep_seconds,
        )
        if result is not None:
            zip_path, _meta = result
            return trade_date, zip_path
    return None


def _parse_snapshot(zip_path: Path, *, trade_date: date, allowed_series: list[str]):
    if trade_date < LEGACY_CUTOFF:
        return parse_legacy_bhavcopy_zip(zip_path, allowed_series)
    return parse_bhavcopy_zip(zip_path, allowed_series)


def _write_universe_yaml(
    output_path: Path,
    *,
    symbols: list[str],
    start: date,
    end: date,
    cadence: str,
    snapshots_requested: int,
    snapshots_loaded: int,
) -> None:
    ensure_directory(output_path.parent)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("# Generated by `sepa build-universe`.\n")
        handle.write("# Source: NSE bhavcopy symbol snapshots; OHLCV still comes from yfinance.\n")
        handle.write(f"# Range: {start.isoformat()} to {end.isoformat()}; cadence: {cadence}.\n")
        handle.write(
            f"# Snapshots loaded: {snapshots_loaded}/{snapshots_requested}.\n"
        )
        yaml.safe_dump({"symbols": symbols}, handle, sort_keys=False)

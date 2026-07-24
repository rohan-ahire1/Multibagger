"""Shared data loading for lab runs.

Mirrors how ``optimize_joint_wf.py`` assembles its inputs, so a lab run and the
canonical parity run see identical signals, prices, sector map, and benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.lab.engines.portfolio_sim import load_inputs
from sepa_scanner.strategy.ranking import load_sector_map


@dataclass(slots=True)
class DataBundle:
    settings: Any
    signals: pl.DataFrame
    ohlcv: pl.DataFrame
    diagnostics: pl.DataFrame
    params: Any
    portfolio: dict[str, Any]
    sector_map: dict[str, str]
    benchmark: pl.DataFrame
    start_date: date
    end_date: date
    data_source: str


def resolve_sector_map(data_dir: Path) -> dict[str, str]:
    """Prefer the full sector map, falling back to Nifty 500 constituents.

    Same precedence the canonical harness uses; picking the other file changes
    sector caps and therefore the result.
    """
    reference = data_dir / "reference"
    full = reference / "full_sector_map.csv"
    fallback = reference / "nifty500_constituents.csv"
    return load_sector_map(full if full.exists() else fallback)


def detect_price_discontinuities(
    frame: pl.DataFrame,
    column: str,
    *,
    low: float = 0.7,
    high: float = 1.4,
) -> list[date]:
    """Dates where the series jumps more than a real session plausibly could.

    A 10x overnight move is a corporate action that was never adjusted for, not
    a price. Used to refuse to publish metrics derived from a broken series:
    NIFTYBEES split 1:10 on 2019-12-19 and is absent from
    ``config/corporate_actions.csv``, so its raw curve shows a ~90% "loss" that
    would otherwise be reported as strategy alpha.
    """
    if frame.is_empty() or column not in frame.columns or frame.height < 2:
        return []
    ratios = frame.sort("date").with_columns(
        (pl.col(column) / pl.col(column).shift(1)).alias("_ratio")
    )
    broken = ratios.filter(
        pl.col("_ratio").is_not_null() & ((pl.col("_ratio") < low) | (pl.col("_ratio") > high))
    )
    return broken["date"].to_list()


def benchmark_curve(
    ohlcv: pl.DataFrame, symbol: str, start: date, end: date
) -> pl.DataFrame:
    return (
        ohlcv.filter(pl.col("symbol") == symbol)
        .select(["date", "close"])
        .rename({"close": "tri"})
        .filter((pl.col("date") >= start) & (pl.col("date") <= end))
        .sort("date")
    )


def load_bundle(start_date: date, end_date: date) -> DataBundle:
    """Load signals, prices, diagnostics, sector map, and benchmark for a window."""
    bundle, signals, ohlcv = load_inputs(start_date, end_date)
    settings = bundle["settings"]
    data_dir = settings.runtime.data_dir

    return DataBundle(
        settings=settings,
        signals=signals,
        ohlcv=ohlcv,
        diagnostics=bundle["diagnostics"],
        params=bundle["params"],
        portfolio=bundle["portfolio"],
        sector_map=resolve_sector_map(data_dir),
        benchmark=benchmark_curve(
            ohlcv, settings.market.benchmark_symbol, start_date, end_date
        ),
        start_date=start_date,
        end_date=end_date,
        data_source=getattr(settings.data, "source", "unknown"),
    )

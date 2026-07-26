"""Shared data loading for lab runs.

Mirrors how ``optimize_joint_wf.py`` assembles its inputs, so a lab run and the
canonical parity run see identical signals, prices, sector map, and benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.lab.engines.portfolio_sim import load_inputs
from sepa_scanner.settings import load_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
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


def _reference_index(data_dir: Path, prefix: str, value_column: str) -> pl.DataFrame:
    """Load a versioned Nifty reference file without manufacturing missing data."""
    files = sorted((data_dir / "reference").glob(f"{prefix}_*.csv"))
    if not files:
        return pl.DataFrame(schema={"date": pl.Date, value_column: pl.Float64})
    frames: list[pl.DataFrame] = []
    for path in files:
        frame = pl.read_csv(path, try_parse_dates=True)
        source_value = value_column if value_column in frame.columns else "close" if "close" in frame.columns else "tri" if "tri" in frame.columns else "price"
        if "date" not in frame.columns or source_value not in frame.columns:
            continue
        frames.append(frame.select(pl.col("date").cast(pl.Date), pl.col(source_value).cast(pl.Float64).alias(value_column)))
    if not frames:
        return pl.DataFrame(schema={"date": pl.Date, value_column: pl.Float64})
    return pl.concat(frames).unique(subset=["date"], keep="last").sort("date")


def nifty500_price_index(data_dir: Path, start: date, end: date) -> pl.DataFrame:
    """Nifty 500 price series, if a separately fetched file is locally available."""
    return _reference_index(data_dir, "nifty500_price", "price").filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    )


def nifty500_tri(data_dir: Path, start: date, end: date) -> pl.DataFrame:
    """Nifty 500 TRI, deliberately empty when its local history does not cover a run."""
    return _reference_index(data_dir, "nifty500_tri", "tri").filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
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


def load_panel_bundle(
    start_date: date,
    end_date: date,
    *,
    warmup_days: int = 400,
    tail_days: int = 400,
) -> DataBundle:
    """Load the raw price panel without touching the DuckDB signals table.

    For strategies that compute their own signals from prices. ``warmup_days``
    of history before ``start_date`` is included so indicators are warm on the
    first scored bar rather than ramping up inside the measured window;
    ``tail_days`` after ``end_date`` lets the simulator manage positions that
    are still open when the window closes.
    """
    settings = load_settings()
    data_dir = settings.runtime.data_dir
    store = ParquetOhlcvStore(data_dir / settings.data.panel_subdir)

    ohlcv = store.read_frame(
        start_date=start_date - timedelta(days=warmup_days),
        end_date=end_date + timedelta(days=tail_days),
        columns=["date", "symbol", "open", "high", "low", "close", "volume", "turnover"],
    ).sort(["symbol", "date"])

    portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        "max_positions": settings.portfolio.max_positions,
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_name_pct": settings.portfolio.max_name_pct,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": settings.portfolio.max_heat_pct,
        "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
        "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
        "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": settings.portfolio.regime_high_volatility_threshold,
        "regime_cooldown_days": settings.portfolio.regime_cooldown_days,
        "breadth_soft_floor_pct": settings.portfolio.breadth_soft_floor_pct,
        "breadth_soft_size_mult": settings.portfolio.breadth_soft_size_mult,
        "pyramid_max_adds": settings.portfolio.pyramid_max_adds,
        "pyramid_trigger_r": settings.portfolio.pyramid_trigger_r,
        "pyramid_add_interval_r": settings.portfolio.pyramid_add_interval_r,
        "pyramid_risk_frac": settings.portfolio.pyramid_risk_frac,
        "cash_yield_apy": settings.portfolio.cash_yield_apy,
    }

    return DataBundle(
        settings=settings,
        signals=pl.DataFrame(),
        ohlcv=ohlcv,
        diagnostics=pl.DataFrame(),
        params=BacktestParams(
            pending_days=settings.backtest.pending_days,
            max_hold_days=max(settings.backtest.max_hold_days, 90),
            exit_mode=ExitMode(settings.backtest.exit_mode),
            baseline_tighten_r=settings.backtest.baseline_tighten_r,
            stop_trigger_mode=settings.backtest.stop_trigger_mode,
            signal_exit_execution=settings.backtest.signal_exit_execution,
            slippage_bps=settings.backtest.slippage_bps,
            friction_enabled=True,
        ),
        portfolio=portfolio,
        sector_map=resolve_sector_map(data_dir),
        benchmark=benchmark_curve(
            ohlcv, settings.market.benchmark_symbol, start_date, end_date
        ),
        start_date=start_date,
        end_date=end_date,
        data_source=getattr(settings.data, "source", "unknown"),
    )

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import typer

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.metrics import build_equity_curve, summarize_backtest
from sepa_scanner.backtest.models import BacktestParams, BacktestReport, ExitMode
from sepa_scanner.alerts.console import ConsoleAlertChannel
from sepa_scanner.alerts.telegram import TelegramAlertChannel
from sepa_scanner.calendar import (
    load_cm_holidays,
    is_cm_trading_day,
    previous_trading_days,
    trading_days_between,
)
from sepa_scanner.data_sources.yfinance_adjusted import download_adjusted_history
from sepa_scanner.logging_setup import setup_logging
from sepa_scanner.settings import AppSettings, load_settings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.signal_engine import ScanResult, scan_for_date
from sepa_scanner.types import DownloadManifest, RunLog, Signal, WatchlistCandidate
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals
from sepa_scanner.utils.http import bootstrap_nse_session, build_nse_session
from sepa_scanner.utils.io import ensure_directory, export_dataframe


app = typer.Typer(help="Local, ultrafast NSE multibagger scanner.")
LOGGER = logging.getLogger(__name__)


def resolve_settings(config_dir: Path | None = None) -> AppSettings:
    settings = load_settings(config_dir=config_dir)
    os.environ.setdefault("POLARS_MAX_THREADS", str(settings.runtime.polars_max_threads))
    return settings


def _stores(settings: AppSettings) -> tuple[ParquetOhlcvStore, DuckDbStore]:
    data_dir = ensure_directory(settings.runtime.data_dir)
    parquet_store = ParquetOhlcvStore(root=ensure_directory(data_dir / "yf_adjusted"))
    duckdb_store = DuckDbStore(db_path=ensure_directory(data_dir / "db") / "sepa.duckdb")
    return parquet_store, duckdb_store


def _raw_dir(settings: AppSettings) -> Path:
    return ensure_directory(settings.runtime.data_dir / "raw")


def _signal_dir(settings: AppSettings) -> Path:
    return ensure_directory(settings.runtime.data_dir / "signals")


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid ISO date: {value}") from exc


def _lookback_days(settings: AppSettings) -> int:
    trend = settings.trend_template
    return max(
        settings.data.min_history_days,
        trend.lookback_52w,
        trend.sma_slow + trend.sma200_slope_lookback,
        settings.setup_vcp.base_lookback,
        max(settings.relative_strength.lookbacks),
        50,
    ) + 25


def _signal_dataclasses(result: ScanResult) -> list[Signal]:
    created_at = datetime.now(UTC)
    return [
        Signal(created_at=created_at, **row)
        for row in result.signals.to_dicts()
    ]


def _watchlist_candidate_dataclasses(result: ScanResult) -> list[WatchlistCandidate]:
    created_at = datetime.now(UTC)
    return [
        WatchlistCandidate(created_at=created_at, **row)
        for row in result.watchlist_candidates.to_dicts()
    ]


def _write_run_log(
    store: DuckDbStore,
    *,
    scan_date: date,
    result: ScanResult | None,
    started_at: datetime,
    status: str,
    error: str | None = None,
    bhavcopy_sha256: str | None = None,
    bhavcopy_url: str | None = None,
) -> None:
    store.insert_run(
        RunLog(
            run_id=str(uuid4()),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            scan_date=scan_date,
            market_filter_on=result.market_filter_on if result else None,
            num_symbols=result.num_symbols if result else 0,
            num_eligible=result.num_eligible if result else 0,
            num_watch=(
                result.signals.filter(pl.col("signal_type") == "WATCH").height if result else 0
            ),
            num_breakout=(
                result.signals.filter(pl.col("signal_type") == "BREAKOUT").height if result else 0
            ),
            status=status,
            error=error,
            num_trend_pass=result.num_trend_pass if result else 0,
            num_rs_pass=result.num_rs_pass if result else 0,
            num_setup_pass=result.num_setup_pass if result else 0,
            num_near_pivot=result.num_near_pivot if result else 0,
            bhavcopy_sha256=bhavcopy_sha256,
            bhavcopy_url=bhavcopy_url,
        )
    )


def run_backfill_command(
    *,
    config_dir: Path | None,
    start: date,
    end: date,
    final_only: bool,
    sleep_seconds: float,
) -> list[date]:
    settings = resolve_settings(config_dir)
    parquet_store, _ = _stores(settings)
    symbols = _resolve_runtime_symbols(settings, parquet_store)
    frame, _failed = download_adjusted_history(
        symbols,
        start=start,
        end=end + timedelta(days=1),
    )
    if frame.is_empty():
        return []
    parquet_store.upsert(frame)
    return sorted(frame["date"].unique().to_list())


def run_update_command(
    *,
    config_dir: Path | None,
    days: int,
    target_date: date | None,
    force: bool,
) -> tuple[date, None]:
    settings = resolve_settings(config_dir)
    parquet_store, _ = _stores(settings)
    symbols = _resolve_runtime_symbols(settings, parquet_store)

    if target_date is not None:
        start_date = target_date
        end_date = target_date + timedelta(days=1)
    else:
        end_anchor = date.today()
        start_date = previous_trading_days(end_anchor, days)[0]
        end_date = end_anchor + timedelta(days=1)

    frame, _failed = download_adjusted_history(
        symbols,
        start=start_date,
        end=end_date,
    )
    if not frame.is_empty():
        parquet_store.upsert(frame)
        return frame["date"].max(), None

    latest_existing = parquet_store.latest_date()
    if latest_existing is not None:
        return latest_existing, None
    return target_date or start_date, None


def _resolve_runtime_symbols(
    settings: AppSettings,
    parquet_store: ParquetOhlcvStore,
) -> list[str]:
    if settings.manual_universe:
        return sorted(set(settings.manual_universe))

    existing = parquet_store.read_frame(columns=["symbol"])
    if not existing.is_empty():
        return sorted(existing["symbol"].unique().to_list())

    raise typer.BadParameter(
        "No full symbol universe available. Populate config/universe.yaml or seed data/yf_adjusted/ohlcv_all.parquet first."
    )


def _build_channels(settings: AppSettings, *, no_telegram: bool) -> list:
    channels: list = []
    configured = set(settings.alerts.channels)
    if "console" in configured:
        channels.append(ConsoleAlertChannel())
    if "telegram" in configured and not no_telegram:
        if settings.telegram.enabled:
            channels.append(TelegramAlertChannel(settings))
        else:
            LOGGER.info("Telegram is disabled; running in console-only mode.")
    return channels


def run_scan_command(
    *,
    config_dir: Path | None,
    scan_date: date | None,
    export_parquet: bool,
    export_csv: bool,
    no_telegram: bool,
    force: bool = False,
    download_meta: object | None = None,
) -> ScanResult:
    settings = resolve_settings(config_dir)
    parquet_store, duckdb_store = _stores(settings)
    actual_scan_date = scan_date or parquet_store.latest_date()
    if actual_scan_date is None:
        raise typer.Exit(code=1)

    cache_path = settings.runtime.data_dir / "cache" / "nse_holidays_trading.json"
    holiday_session = bootstrap_nse_session(
        build_nse_session(),
        timeout=settings.data.http_timeout_sec,
    )
    cm_holidays = load_cm_holidays(holiday_session, cache_path)
    if not force and not is_cm_trading_day(actual_scan_date, cm_holidays):
        LOGGER.info("Skipping scan: %s is not a CM trading day", actual_scan_date)
        _write_run_log(
            duckdb_store,
            scan_date=actual_scan_date,
            result=None,
            started_at=datetime.now(UTC),
            status="skipped_non_trading_day",
        )
        raise typer.Exit(code=0)

    lookback_days = _lookback_days(settings)
    start_date = previous_trading_days(actual_scan_date, lookback_days)[0]
    frame = parquet_store.read_frame(start_date=start_date, end_date=actual_scan_date)
    if frame.is_empty():
        raise typer.Exit(code=1)

    started_at = datetime.now(UTC)
    try:
        result = scan_for_date(frame=frame, settings=settings, scan_date=actual_scan_date)
        duckdb_store.upsert_signals(_signal_dataclasses(result))
        duckdb_store.replace_watchlist_candidates(
            actual_scan_date,
            _watchlist_candidate_dataclasses(result),
        )
        if not result.diagnostics.is_empty():
            duckdb_store.replace_daily_diagnostics(actual_scan_date, result.diagnostics)

        if export_parquet:
            export_dataframe(
                result.signals,
                _signal_dir(settings) / f"{actual_scan_date.isoformat()}_signals.parquet",
            )
        if export_csv:
            export_dataframe(
                result.signals,
                _signal_dir(settings) / f"{actual_scan_date.isoformat()}_signals.csv",
                csv=True,
            )

        for channel in _build_channels(settings, no_telegram=no_telegram):
            channel.send(result.signals, market_filter_on=result.market_filter_on)

        _write_run_log(
            duckdb_store,
            scan_date=actual_scan_date,
            result=result,
            started_at=started_at,
            status="success",
        )
        return result
    except Exception as exc:
        _write_run_log(
            duckdb_store,
            scan_date=actual_scan_date,
            result=None,
            started_at=started_at,
            status="error",
            error=str(exc),
        )
        raise


def run_ui_command(*, config_dir: Path | None, host: str, port: int) -> None:
    """Launch the Multibagger Terminal (Vite + React frontend) via npm."""
    project_root = Path(__file__).resolve().parents[2]
    terminal_dir = project_root / "sepa-terminal"
    env = os.environ.copy()
    env["SEPA_CONFIG_DIR"] = str((config_dir or project_root / "config").resolve())
    subprocess.run(
        ["npm", "run", "dev", "--", "--host", host, "--port", str(port)],
        check=True,
        cwd=terminal_dir,
        env=env,
    )


def _trade_frame(report_trades) -> pl.DataFrame:
    if not report_trades:
        return pl.DataFrame()
    return pl.DataFrame([asdict(trade) for trade in report_trades])


def run_backtest_command(
    *,
    config_dir: Path | None,
    start_date: date,
    end_date: date,
    pending_days: int,
    max_hold_days: int,
    target_r: float | None,
    symbol: str | None,
    export_csv: bool,
) -> BacktestReport:
    settings = resolve_settings(config_dir)
    parquet_store, duckdb_store = _stores(settings)
    duckdb_store.initialize()
    params = BacktestParams(
        pending_days=pending_days,
        max_hold_days=max_hold_days,
        target_r=target_r,
        exit_mode=ExitMode.MINERVINI_PHASED,
    )
    signals = get_backtest_signals(
        duckdb_store._connect(),
        start_date=start_date,
        end_date=end_date,
        symbol=symbol,
    )

    if signals.is_empty():
        return BacktestReport(
            params=params,
            summary=summarize_backtest([], exit_mode=params.exit_mode),
            trades=pl.DataFrame(),
            equity_curve=build_equity_curve([]),
        )

    symbols = signals["symbol"].unique().to_list()
    ohlcv_end_date = end_date + timedelta(days=(pending_days + max_hold_days) * 2)
    ohlcv = get_backtest_ohlcv(
        parquet_store,
        symbols=symbols,
        start_date=start_date,
        end_date=ohlcv_end_date,
    )
    result = simulate_breakout_backtest(signals, ohlcv, params)
    trades = _trade_frame(result.trades)
    summary = summarize_backtest(result.trades, exit_mode=params.exit_mode)
    equity_curve = build_equity_curve(result.trades)

    if export_csv and not trades.is_empty():
        symbol_suffix = f"_{symbol}" if symbol else ""
        export_dataframe(
            trades,
            _signal_dir(settings) / f"{start_date.isoformat()}_{end_date.isoformat()}{symbol_suffix}_backtest.csv",
            csv=True,
        )

    return BacktestReport(
        params=params,
        summary=summary,
        trades=trades,
        equity_curve=equity_curve,
    )


def collect_health(*, config_dir: Path | None) -> dict[str, object]:
    settings = resolve_settings(config_dir)
    parquet_store, duckdb_store = _stores(settings)
    latest_ohlcv_date = parquet_store.latest_date()
    summary = duckdb_store.health_summary()
    summary["latest_ohlcv_date"] = latest_ohlcv_date
    summary["symbols_on_latest_ohlcv_date"] = (
        parquet_store.count_symbols_on_date(latest_ohlcv_date) if latest_ohlcv_date else 0
    )
    return summary


@app.callback()
def main() -> None:
    setup_logging()


@app.command()
def backfill(
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    final_only: bool = typer.Option(True, "--final-only/--allow-provisional"),
    sleep: float = typer.Option(0.4, help="Sleep seconds between archive downloads"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    processed = run_backfill_command(
        config_dir=config_dir,
        start=_parse_iso_date(start) or date.today(),
        end=_parse_iso_date(end) or date.today(),
        final_only=final_only,
        sleep_seconds=sleep,
    )
    typer.echo(f"processed_dates: {len(processed)}")


@app.command()
def update(
    days: int = typer.Option(5, help="Number of trading days to refresh"),
    date_: str | None = typer.Option(None, "--date", help="Specific trade date YYYY-MM-DD"),
    force: bool = typer.Option(False, help="Re-download archives even if cached"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    latest_date, _meta = run_update_command(
        config_dir=config_dir,
        days=days,
        target_date=_parse_iso_date(date_),
        force=force,
    )
    typer.echo(f"latest_date: {latest_date}")


@app.command("scan")
def scan_command(
    date_: str | None = typer.Option(None, "--date", help="Scan date YYYY-MM-DD"),
    export_parquet: bool = typer.Option(True, "--export-parquet/--no-export-parquet"),
    export_csv: bool = typer.Option(False, help="Also export CSV signal snapshot"),
    no_telegram: bool = typer.Option(False, help="Disable Telegram delivery for this run"),
    force: bool = typer.Option(False, help="Run even on non-trading days"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    result = run_scan_command(
        config_dir=config_dir,
        scan_date=_parse_iso_date(date_),
        export_parquet=export_parquet,
        export_csv=export_csv,
        no_telegram=no_telegram,
        force=force,
    )
    typer.echo(
        f"scan_date: {result.scan_date} watch={result.signals.filter(pl.col('signal_type') == 'WATCH').height} "
        f"breakout={result.signals.filter(pl.col('signal_type') == 'BREAKOUT').height}"
    )


@app.command()
def run(
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    latest_date, latest_meta = run_update_command(
        config_dir=config_dir,
        days=5,
        target_date=None,
        force=False,
    )
    run_scan_command(
        config_dir=config_dir,
        scan_date=latest_date,
        export_parquet=True,
        export_csv=False,
        no_telegram=False,
        download_meta=latest_meta,
    )


@app.command()
def health(
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    summary = collect_health(config_dir=config_dir)
    for key, value in summary.items():
        typer.echo(f"{key}: {value}")


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Host to bind"),
    port: int = typer.Option(5173, help="Port to bind"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    """Launch the Multibagger Terminal frontend."""
    run_ui_command(config_dir=config_dir, host=host, port=port)


@app.command("backtest")
def backtest_command(
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    pending_days: int = typer.Option(5, help="Trading days to keep an entry pending"),
    max_hold_days: int = typer.Option(90, help="Maximum holding period in trading days"),
    target_r: float | None = typer.Option(None, help="Optional profit target in R multiples (default from config)"),
    symbol: str | None = typer.Option(None, help="Optional symbol filter"),
    export_csv: bool = typer.Option(False, help="Export the trade table as CSV"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    settings = resolve_settings(config_dir)
    effective_target_r = target_r if target_r is not None else settings.risk.target_r
    report = run_backtest_command(
        config_dir=config_dir,
        start_date=_parse_iso_date(start) or date.today(),
        end_date=_parse_iso_date(end) or date.today(),
        pending_days=pending_days,
        max_hold_days=max_hold_days,
        target_r=effective_target_r,
        symbol=symbol,
        export_csv=export_csv,
    )
    if report.summary.total_signals == 0:
        typer.echo("No eligible BREAKOUT signals in range.")
        return

    s = report.summary
    typer.echo(f"total_signals: {s.total_signals}")
    typer.echo(f"filled_trades: {s.filled_trades}")
    typer.echo(f"win_rate: {s.win_rate:.4f}")
    typer.echo(f"cumulative_r: {s.cumulative_r:.4f}")
    typer.echo(f"expectancy: {s.expectancy:+.4f}R" if s.expectancy is not None else "expectancy: N/A")
    typer.echo(f"max_drawdown: {s.max_drawdown:.2f}R")
    typer.echo(f"exit_mode: {s.exit_mode}")


if __name__ == "__main__":
    app()

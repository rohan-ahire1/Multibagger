from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl
import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from sepa_scanner.calendar import previous_trading_days, trading_days_between
from sepa_scanner.main import (
    _lookback_days,
    _parse_iso_date,
    _signal_dataclasses,
    _stores,
    _watchlist_candidate_dataclasses,
    _write_run_log,
    resolve_settings,
)
from sepa_scanner.strategy.signal_engine import scan_batch

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _check_db_lock(db_path: Path) -> duckdb.DuckDBPyConnection:
    try:
        return duckdb.connect(str(db_path))
    except duckdb.IOException as exc:
        typer.echo(f"ERROR: Cannot acquire DuckDB lock.\n{exc}", err=True)
        typer.echo("Stop the Streamlit UI first (Ctrl+C in that terminal), then retry.", err=True)
        raise typer.Exit(code=1) from exc


def _scanned_dates(conn: duckdb.DuckDBPyConnection) -> set[date]:
    rows = conn.execute(
        "SELECT DISTINCT scan_date FROM runs WHERE status = 'success'"
    ).fetchall()
    return {row[0] for row in rows}


def main(
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--force", help="Skip dates already scanned"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    parsed_start = _parse_iso_date(start)
    parsed_end = _parse_iso_date(end)
    if parsed_start is None or parsed_end is None:
        raise typer.BadParameter("start and end are required")

    settings = resolve_settings(config_dir)
    parquet_store, duckdb_store = _stores(settings)
    conn = _check_db_lock(duckdb_store.db_path)

    all_dates = trading_days_between(parsed_start, parsed_end)
    if skip_existing:
        already_done = _scanned_dates(conn)
        dates = [d for d in all_dates if d not in already_done]
        skipped_existing = len(all_dates) - len(dates)
        if skipped_existing:
            typer.echo(f"Skipping {skipped_existing} already-scanned dates.")
    else:
        dates = all_dates

    conn.close()

    if not dates:
        typer.echo("Nothing to scan.")
        raise typer.Exit(code=0)

    lookback = _lookback_days(settings)
    ohlcv_start = previous_trading_days(dates[0], lookback)[0]
    typer.echo(f"Loading OHLCV from {ohlcv_start} to {dates[-1]} ...")
    frame = parquet_store.read_frame(start_date=ohlcv_start, end_date=dates[-1])
    if frame.is_empty():
        typer.echo("No OHLCV data in range.")
        raise typer.Exit(code=1)
    typer.echo(f"Loaded {frame.height:,} rows, {frame['symbol'].n_unique():,} symbols.")

    # Data-driven trading calendar: drop weekday dates with no panel rows (NSE holidays).
    # trading_days_between() is weekday-only, so holidays would otherwise fail every scan
    # with "Missing benchmark data" and pollute the runs table with error rows.
    traded_dates = set(frame["date"].unique().to_list())
    non_trading = [d for d in dates if d not in traded_dates]
    if non_trading:
        typer.echo(f"Skipping {len(non_trading)} non-trading days (holidays, no panel data).")
        dates = [d for d in dates if d in traded_dates]
    if not dates:
        typer.echo("No trading days with data in range.")
        raise typer.Exit(code=0)

    typer.echo("Preparing indicators (one-time) ...")

    total = len(dates)
    succeeded = 0
    failed = 0

    progress_bar = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with progress_bar as progress:
        task = progress.add_task("Scanning", total=total)
        started_at = datetime.now(UTC)

        for scan_date, result, error in scan_batch(frame, settings, dates):
            if result is not None:
                watch = result.signals.filter(pl.col("signal_type") == "WATCH").height
                breakout = result.signals.filter(pl.col("signal_type") == "BREAKOUT").height
                progress.console.print(
                    f"  {scan_date}: [green]watch={watch} breakout={breakout}[/green]"
                )
                duckdb_store.upsert_signals(_signal_dataclasses(result))
                duckdb_store.replace_watchlist_candidates(
                    scan_date, _watchlist_candidate_dataclasses(result)
                )
                if not result.diagnostics.is_empty():
                    duckdb_store.replace_daily_diagnostics(scan_date, result.diagnostics)
                _write_run_log(
                    duckdb_store,
                    scan_date=scan_date,
                    result=result,
                    started_at=started_at,
                    status="success",
                )
                succeeded += 1
            else:
                progress.console.print(f"  {scan_date}: [red]ERROR {error}[/red]")
                _write_run_log(
                    duckdb_store,
                    scan_date=scan_date,
                    result=None,
                    started_at=started_at,
                    status="error",
                    error=error,
                )
                failed += 1
            progress.advance(task)

    typer.echo(f"\nDone. succeeded={succeeded} failed={failed}")


if __name__ == "__main__":
    typer.run(main)

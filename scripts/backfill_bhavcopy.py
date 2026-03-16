from __future__ import annotations

from pathlib import Path

import typer

from sepa_scanner.main import _parse_iso_date, run_backfill_command


def main(
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    final_only: bool = typer.Option(True, "--final-only/--allow-provisional"),
    sleep: float = typer.Option(0.4, help="Sleep seconds between archive downloads"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    parsed_start = _parse_iso_date(start)
    parsed_end = _parse_iso_date(end)
    if parsed_start is None or parsed_end is None:
        raise typer.BadParameter("start and end are required")
    processed = run_backfill_command(
        config_dir=config_dir,
        start=parsed_start,
        end=parsed_end,
        final_only=final_only,
        sleep_seconds=sleep,
    )
    typer.echo(f"processed_dates: {len(processed)}")


if __name__ == "__main__":
    typer.run(main)

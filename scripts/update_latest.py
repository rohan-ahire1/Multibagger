from __future__ import annotations

from pathlib import Path

import typer

from sepa_scanner.main import _parse_iso_date, run_update_command


def main(
    days: int = typer.Option(5, help="Number of trading days to refresh"),
    date_: str | None = typer.Option(None, "--date", help="Specific trade date YYYY-MM-DD"),
    force: bool = typer.Option(False, help="Re-download archives even if cached"),
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    latest_date = run_update_command(
        config_dir=config_dir,
        days=days,
        target_date=_parse_iso_date(date_),
        force=force,
    )
    typer.echo(f"latest_date: {latest_date}")


if __name__ == "__main__":
    typer.run(main)

from __future__ import annotations

from pathlib import Path

import typer

from sepa_scanner.main import run_scan_command, run_update_command


def main(
    config_dir: Path | None = typer.Option(None, help="Override config directory"),
) -> None:
    latest_date, _failed = run_update_command(
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
    )


if __name__ == "__main__":
    typer.run(main)

from __future__ import annotations

from pathlib import Path

import polars as pl


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_dataframe(frame: pl.DataFrame, path: Path, *, csv: bool = False) -> Path:
    ensure_directory(path.parent)
    if csv:
        frame.write_csv(path)
    else:
        frame.write_parquet(path, compression="zstd")
    return path

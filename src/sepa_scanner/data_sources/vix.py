from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_vix_data(path: Path | None = None) -> pl.DataFrame:
    """Load India VIX data from local CSV.

    Expected columns: date, vix_close.
    """
    if path is None or not Path(path).exists():
        return pl.DataFrame(schema={"date": pl.Date, "vix_close": pl.Float64})
    return pl.read_csv(path, try_parse_dates=True)


def vix_position_multiplier(vix_value: float | None) -> float:
    """Compute position size multiplier based on India VIX level.

    VIX < 15: 1.0 (full size)
    VIX 15-20: 1.0 (normal)
    VIX 20-25: 0.5 (reduce)
    VIX > 25: 0.0 (no new entries)
    """
    if vix_value is None:
        return 1.0
    if vix_value > 25.0:
        return 0.0
    if vix_value > 20.0:
        return 0.5
    return 1.0

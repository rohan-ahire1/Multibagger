from __future__ import annotations

from datetime import date, datetime

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query

from sepa_scanner.api.deps import get_duckdb_store
from sepa_scanner.storage.duckdb_store import DuckDbStore

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _serialize_dates(row: dict) -> dict:
    """Convert date/datetime objects in a dict to ISO strings."""
    out = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


@router.get("/{symbol}/diagnostics")
def get_diagnostics(
    symbol: str,
    date: date = Query(..., alias="date"),
    store: DuckDbStore = Depends(get_duckdb_store),
) -> dict:
    df = store.fetch_daily_diagnostics(date)
    filtered = df.filter(pl.col("symbol") == symbol)
    if filtered.is_empty():
        raise HTTPException(status_code=404, detail=f"No diagnostics for {symbol} on {date}")
    row = filtered.to_dicts()[0]
    return _serialize_dates(row)


@router.get("/{symbol}/signal-history")
def get_signal_history(
    symbol: str,
    store: DuckDbStore = Depends(get_duckdb_store),
) -> list[dict]:
    conn = store._connect()
    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE symbol = ?
        ORDER BY signal_date DESC
        """,
        [symbol],
    ).fetchall()
    columns = [desc[0] for desc in conn.description]
    return [_serialize_dates(dict(zip(columns, row, strict=False))) for row in rows]

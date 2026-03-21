from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query

from sepa_scanner.api.deps import get_duckdb_store
from sepa_scanner.storage.duckdb_store import DuckDbStore

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


def _serialize_dates(row: dict) -> dict:
    """Convert date/datetime objects in a dict to ISO strings."""
    out = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


@router.get("/signals")
def get_signals(
    date: date = Query(..., alias="date"),
    type: str | None = Query(None, alias="type"),
    store: DuckDbStore = Depends(get_duckdb_store),
) -> list[dict]:
    rows = store.fetch_signals(date)
    if type is not None:
        rows = [r for r in rows if r.get("signal_type") == type]
    return [_serialize_dates(r) for r in rows]


@router.get("/watchlist")
def get_watchlist(
    date: date = Query(..., alias="date"),
    store: DuckDbStore = Depends(get_duckdb_store),
) -> list[dict]:
    rows = store.fetch_watchlist_candidates(date)
    return [_serialize_dates(r) for r in rows]


@router.get("/dates")
def get_scan_dates(
    store: DuckDbStore = Depends(get_duckdb_store),
) -> list[str]:
    conn = store._connect()
    rows = conn.execute(
        """
        SELECT DISTINCT CAST(scan_date AS DATE) as s_date
        FROM runs
        ORDER BY s_date DESC
        """
    ).fetchall()
    return [row[0].isoformat() for row in rows if row[0] is not None]

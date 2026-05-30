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
        SELECT DISTINCT CAST(r.scan_date AS DATE) AS s_date
        FROM runs r
        INNER JOIN daily_diagnostics d
            ON d.diag_date = r.scan_date
        WHERE r.status = 'success'
        ORDER BY s_date DESC
        """
    ).fetchall()
    return [row[0].isoformat() for row in rows if row[0] is not None]


@router.get("/breadth")
def get_market_breadth(
    date: date = Query(..., alias="date"),
    store: DuckDbStore = Depends(get_duckdb_store),
) -> dict:
    conn = store._connect()
    row = conn.execute(
        """
        SELECT 
            COUNT(CASE WHEN tt7_close_above_sma50 = TRUE THEN 1 END) as above_50,
            COUNT(*) as total,
            CASE WHEN COUNT(*) > 0 
                 THEN COUNT(CASE WHEN tt7_close_above_sma50 = TRUE THEN 1 END) * 1.0 / COUNT(*)
                 ELSE 0.0 
            END as breadth
        FROM daily_diagnostics
        WHERE diag_date = ? AND eligible = TRUE
        """,
        [date],
    ).fetchone()
    
    if not row or row[1] == 0:
        row = conn.execute(
            """
            SELECT 
                COUNT(CASE WHEN tt7_close_above_sma50 = TRUE THEN 1 END) as above_50,
                COUNT(*) as total,
                CASE WHEN COUNT(*) > 0 
                     THEN COUNT(CASE WHEN tt7_close_above_sma50 = TRUE THEN 1 END) * 1.0 / COUNT(*)
                     ELSE 0.0 
                END as breadth
            FROM daily_diagnostics
            WHERE diag_date = ?
            """,
            [date],
        ).fetchone()

    run_row = conn.execute(
        """
        SELECT market_filter_on
        FROM runs
        WHERE scan_date = ? AND status = 'success'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        [date],
    ).fetchone()
    market_filter_on = run_row[0] if run_row else True

    above_50 = row[0] if row else 0
    total = row[1] if row else 0
    breadth = row[2] if row else 0.0

    if breadth >= 0.40:
        regime = "Strong"
    elif breadth <= 0.20:
        regime = "Weak"
    else:
        regime = "Neutral"

    return {
        "date": date.isoformat(),
        "aboveSma50": above_50,
        "totalEligible": total,
        "breadth": float(breadth),
        "regime": regime,
        "marketFilterOn": market_filter_on,
    }

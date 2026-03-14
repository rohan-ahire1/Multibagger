from __future__ import annotations

from datetime import date

import polars as pl
from fastapi import APIRouter, Depends, Query

from sepa_scanner.api.deps import get_parquet_store
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

router = APIRouter(prefix="/api/ohlcv", tags=["ohlcv"])


@router.get("/{symbol}")
def get_ohlcv(
    symbol: str,
    start: date | None = Query(None),
    end: date | None = Query(None),
    store: ParquetOhlcvStore = Depends(get_parquet_store),
) -> list[dict]:
    df = (
        store.read_frame(
            start_date=start,
            end_date=end,
            columns=["date", "symbol", "open", "high", "low", "close", "volume"],
        )
        .filter(pl.col("symbol") == symbol)
        .sort("date")
    )
    rows = df.to_dicts()
    for row in rows:
        row["date"] = row["date"].isoformat()
    return rows

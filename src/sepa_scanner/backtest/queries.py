"""Query helpers for backtesting — no Streamlit dependency."""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


def _query_frame(
    conn: duckdb.DuckDBPyConnection, query: str, params: list[object] | None = None
) -> pl.DataFrame:
    return conn.execute(query, params or []).pl()


def get_backtest_signals(
    conn: duckdb.DuckDBPyConnection,
    start_date: date | None,
    end_date: date | None,
    symbol: str | None = None,
) -> pl.DataFrame:
    query = """
        SELECT *
        FROM signals
        WHERE signal_type = 'BREAKOUT'
    """
    params: list[object] = []
    if start_date is not None:
        query += "\nAND signal_date >= ?"
        params.append(start_date)
    if end_date is not None:
        query += "\nAND signal_date <= ?"
        params.append(end_date)
    if symbol is not None:
        query += "\nAND symbol = ?"
        params.append(symbol)
    query += "\nORDER BY signal_date ASC, symbol ASC"
    return _query_frame(conn, query, params)


def get_backtest_ohlcv(
    parquet_store: ParquetOhlcvStore,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    if not symbols:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "symbol": pl.String,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
            }
        )
    return (
        parquet_store.read_frame(
            start_date=start_date,
            end_date=end_date,
            columns=["date", "symbol", "open", "high", "low", "close", "volume"],
        )
        .filter(pl.col("symbol").is_in(symbols))
        .sort(["symbol", "date"])
    )

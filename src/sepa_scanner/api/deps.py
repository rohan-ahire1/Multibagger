from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sepa_scanner.settings import AppSettings, load_settings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


@lru_cache
def get_settings() -> AppSettings:
    return load_settings()


@lru_cache
def get_duckdb_store() -> DuckDbStore:
    settings = get_settings()
    store = DuckDbStore(settings.runtime.data_dir / "db" / "sepa.duckdb", read_only=True)
    store.initialize()
    return store


@lru_cache
def get_parquet_store() -> ParquetOhlcvStore:
    settings = get_settings()
    return ParquetOhlcvStore(settings.runtime.data_dir / "yf_adjusted")

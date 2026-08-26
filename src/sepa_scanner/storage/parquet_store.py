from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

import polars as pl

from sepa_scanner.utils.io import ensure_directory


CORE_COLUMNS = [
    "date",
    "security_id",
    "isin",
    "identity_quality",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


class ParquetOhlcvStore:
    FLAT_FILE = "ohlcv_all.parquet"

    def __init__(self, root: Path) -> None:
        self.root = root
        ensure_directory(root)

    @property
    def flat_path(self) -> Path:
        return self.root / self.FLAT_FILE

    def partition_path(self, year: int, month: int) -> Path:
        return self.root / f"year={year}" / f"month={month:02d}" / "ohlcv.parquet"

    def upsert(self, frame: pl.DataFrame) -> None:
        """Append/replace rows in the flat parquet file."""
        if frame.is_empty():
            return

        available_cols = [c for c in CORE_COLUMNS if c in frame.columns]
        incoming = frame.select(available_cols)

        if self.flat_path.exists():
            existing = pl.read_parquet(self.flat_path)
            if "security_id" in existing.columns and "security_id" not in incoming.columns:
                raise ValueError(
                    "refusing symbol-only upsert into an identity-keyed panel; "
                    "rebuild the PIT layer from raw/reference inputs instead"
                )
            incoming_dates = incoming["date"].unique().to_list()
            key_column = "security_id" if "security_id" in incoming.columns and "security_id" in existing.columns else "symbol"
            incoming_keys = incoming[key_column].unique().to_list()
            existing = existing.filter(~(
                pl.col("date").is_in(incoming_dates)
                & pl.col(key_column).is_in(incoming_keys)
            ))
            combined = pl.concat([existing, incoming], how="diagonal")
        else:
            combined = incoming

        key_column = "security_id" if "security_id" in combined.columns else "symbol"
        combined = (
            combined.unique(subset=["date", key_column], keep="last")
            .sort([key_column, "date", "symbol"])
        )
        combined.write_parquet(self.flat_path, compression="zstd")

    def scan_lazy(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        columns: Iterable[str] | None = None,
        symbols: Iterable[str] | str | None = None,
    ) -> pl.LazyFrame:
        if self.flat_path.exists():
            scan = pl.scan_parquet(self.flat_path)
        elif any(self.root.rglob("*.parquet")):
            scan = pl.scan_parquet(str(self.root / "**/*.parquet"), hive_partitioning=True)
        else:
            return pl.DataFrame(schema={column: pl.String for column in CORE_COLUMNS}).lazy()
        if symbols is not None:
            if isinstance(symbols, str):
                scan = scan.filter(pl.col("symbol") == symbols)
            else:
                scan = scan.filter(pl.col("symbol").is_in(list(symbols)))
        if start_date is not None:
            scan = scan.filter(pl.col("date") >= pl.lit(start_date))
        if end_date is not None:
            scan = scan.filter(pl.col("date") <= pl.lit(end_date))
        if columns is not None:
            scan = scan.select(list(columns))
        return scan

    def read_frame(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        columns: Iterable[str] | None = None,
        symbols: Iterable[str] | str | None = None,
    ) -> pl.DataFrame:
        return self.scan_lazy(
            start_date=start_date,
            end_date=end_date,
            columns=columns,
            symbols=symbols,
        ).collect()

    def latest_date(self) -> date | None:
        if not any(self.root.rglob("*.parquet")):
            return None
        row = self.scan_lazy(columns=["date"]).select(pl.col("date").max().alias("latest_date")).collect()
        if row.is_empty():
            return None
        return row.item(0, "latest_date")

    def count_symbols_on_date(self, target_date: date) -> int:
        if not any(self.root.rglob("*.parquet")):
            return 0
        frame = self.scan_lazy(start_date=target_date, end_date=target_date, columns=["date", "symbol"])
        return int(frame.select(pl.col("symbol").n_unique().alias("num_symbols")).collect().item(0, "num_symbols"))

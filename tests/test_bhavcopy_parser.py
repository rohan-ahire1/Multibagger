from __future__ import annotations

from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import polars as pl

from sepa_scanner.data_sources.nse_bhavcopy import (
    build_bhavcopy_url,
    parse_bhavcopy_zip,
    parse_legacy_bhavcopy_zip,
)
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


def _write_zip_from_fixture(tmp_path: Path, fixture_name: str) -> Path:
    fixture_path = Path("tests/fixtures") / fixture_name
    zip_path = tmp_path / f"{fixture_path.stem}.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(fixture_path, arcname=fixture_path.name)
    return zip_path


def test_build_bhavcopy_url_supports_final_and_provisional() -> None:
    trade_date = date(2026, 3, 6)

    assert build_bhavcopy_url(trade_date, final=True).endswith("_20260306_F_0000.csv.zip")
    assert build_bhavcopy_url(trade_date, final=False).endswith("_20260306_P_0000.csv.zip")


def test_parse_bhavcopy_zip_normalizes_and_filters_rows(tmp_path: Path) -> None:
    zip_path = _write_zip_from_fixture(tmp_path, "sample_bhavcopy.csv")

    frame = parse_bhavcopy_zip(zip_path=zip_path, allowed_series=["EQ"])

    assert frame.columns == [
        "date",
        "symbol",
        "series",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    ]
    assert frame.shape == (3, 9)
    assert frame["symbol"].to_list() == ["HDFCBANK", "NIFTYBEES", "RELIANCE"]
    assert frame["date"].to_list() == [date(2026, 3, 7)] * 3
    assert frame["volume"].dtype == pl.Int64
    assert frame["turnover"].dtype == pl.Float64


def test_parse_legacy_bhavcopy_zip_handles_two_digit_year_timestamp(tmp_path: Path) -> None:
    zip_path = tmp_path / "legacy_bhavcopy.zip"
    payload = """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
RELIANCE,EQ,2000,2020,1990,2010,2010,1995,1000,2010000,13-Jul-20,100,INE002A01018
"""

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("cm13JUL2020bhav.csv", payload)

    frame = parse_legacy_bhavcopy_zip(zip_path=zip_path, allowed_series=["EQ"])

    assert frame["date"].to_list() == [date(2020, 7, 13)]


def test_parquet_store_upsert_replaces_overlapping_dates(tmp_path: Path) -> None:
    store = ParquetOhlcvStore(root=tmp_path / "ohlcv")

    initial = pl.DataFrame(
        {
            "date": [date(2026, 3, 6), date(2026, 3, 7)],
            "symbol": ["RELIANCE", "RELIANCE"],
            "series": ["EQ", "EQ"],
            "open": [1200.0, 1210.0],
            "high": [1220.0, 1230.0],
            "low": [1190.0, 1200.0],
            "close": [1215.0, 1218.0],
            "volume": [1000000, 1100000],
            "turnover": [1215000000.0, 1339800000.0],
        }
    )
    replacement = pl.DataFrame(
        {
            "date": [date(2026, 3, 7)],
            "symbol": ["RELIANCE"],
            "series": ["EQ"],
            "open": [1212.0],
            "high": [1235.0],
            "low": [1208.0],
            "close": [1230.0],
            "volume": [1200000],
            "turnover": [1476000000.0],
        }
    )

    store.upsert(initial)
    store.upsert(replacement)
    flat_file = tmp_path / "ohlcv" / "ohlcv_all.parquet"

    assert flat_file.exists()

    frame = pl.read_parquet(flat_file).sort(["symbol", "date"])

    assert frame.shape == (2, 8)
    assert frame["close"].to_list() == [1215.0, 1230.0]
    assert frame["date"].to_list() == [date(2026, 3, 6), date(2026, 3, 7)]

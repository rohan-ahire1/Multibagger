from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.strategy.signal_engine import (
    BREAKOUT_FAIL_BELOW_PIVOT,
    BREAKOUT_FAIL_TT,
    WATCH_FAIL_RS,
    WATCH_FAIL_TT,
    _add_lifecycle_columns,
)
from sepa_scanner.types import DownloadManifest


def test_setup_active_streak() -> None:
    """Streak resets when setup_pass goes False."""
    df = pl.DataFrame(
        {
            "symbol": ["A"] * 7,
            "date": [date(2026, 1, d) for d in range(1, 8)],
            "setup_pass": [True, True, True, False, True, True, False],
            "pivot": [100.0] * 7,
        }
    )
    result = _add_lifecycle_columns(df)
    streaks = result["setup_active_streak"].to_list()
    assert streaks == [1, 2, 3, 0, 1, 2, 0]


def test_setup_active_streak_all_false() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["B"] * 4,
            "date": [date(2026, 1, d) for d in range(1, 5)],
            "setup_pass": [False, False, False, False],
            "pivot": [50.0] * 4,
        }
    )
    result = _add_lifecycle_columns(df)
    assert result["setup_active_streak"].to_list() == [0, 0, 0, 0]


def test_pivot_age_days() -> None:
    """pivot_age_days counts bars since pivot last changed."""
    df = pl.DataFrame(
        {
            "symbol": ["C"] * 6,
            "date": [date(2026, 1, d) for d in range(1, 7)],
            "setup_pass": [True] * 6,
            "pivot": [100.0, 100.0, 100.0, 110.0, 110.0, 120.0],
        }
    )
    result = _add_lifecycle_columns(df)
    ages = result["pivot_age_days"].to_list()
    assert ages == [0, 1, 2, 0, 1, 0]


def test_pivot_age_days_constant_pivot() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["D"] * 4,
            "date": [date(2026, 1, d) for d in range(1, 5)],
            "setup_pass": [True] * 4,
            "pivot": [200.0] * 4,
        }
    )
    result = _add_lifecycle_columns(df)
    assert result["pivot_age_days"].to_list() == [0, 1, 2, 3]


def test_lifecycle_multiple_symbols() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["X", "X", "X", "Y", "Y", "Y"],
            "date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)] * 2,
            "setup_pass": [True, True, False, False, True, True],
            "pivot": [10.0, 10.0, 20.0, 50.0, 50.0, 60.0],
        }
    ).sort(["symbol", "date"])
    result = _add_lifecycle_columns(df)

    x = result.filter(pl.col("symbol") == "X")
    assert x["setup_active_streak"].to_list() == [1, 2, 0]
    assert x["pivot_age_days"].to_list() == [0, 1, 0]

    y = result.filter(pl.col("symbol") == "Y")
    assert y["setup_active_streak"].to_list() == [0, 1, 2]
    assert y["pivot_age_days"].to_list() == [0, 1, 0]


def test_watch_fail_mask_bits() -> None:
    """TT fail + RS fail => mask = 3."""
    assert (WATCH_FAIL_TT | WATCH_FAIL_RS) == 3


def test_breakout_fail_mask_bits() -> None:
    """TT fail + below pivot => mask = 17."""
    assert (BREAKOUT_FAIL_TT | BREAKOUT_FAIL_BELOW_PIVOT) == 17


def test_downloads_table_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    store = DuckDbStore(db_path)
    store.initialize()

    manifest = DownloadManifest(
        download_id="dl-001",
        trade_date=date(2026, 3, 7),
        report_name="BhavCopy Final",
        url="https://nsearchives.nseindia.com/content/cm/test.zip",
        source="nse_bhavcopy",
        downloaded_at=datetime(2026, 3, 7, 16, 0, tzinfo=timezone.utc),
        http_status=200,
        content_length=123456,
        etag='"abc123"',
        last_modified="Fri, 07 Mar 2026 15:30:00 GMT",
        file_path="data/raw/2026-03-07.zip",
        sha256_zip="deadbeef" * 8,
        rows_total=2500,
        rows_after_series_filter=1800,
        rows_after_dedupe=1795,
        duplicates_dropped=5,
        parse_notes=None,
    )
    store.insert_download(manifest)

    rows = store._connect().execute(
        "SELECT * FROM downloads WHERE download_id = ?", ["dl-001"]
    ).fetchall()
    assert len(rows) == 1
    cols = [desc[0] for desc in store._connect().description]
    row_dict = dict(zip(cols, rows[0], strict=False))
    assert row_dict["trade_date"] == date(2026, 3, 7)
    assert row_dict["sha256_zip"] == "deadbeef" * 8
    assert row_dict["rows_total"] == 2500
    assert row_dict["duplicates_dropped"] == 5

    store.close()


def test_diagnostics_table_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    store = DuckDbStore(db_path)
    store.initialize()

    now = datetime(2026, 3, 7, 10, 0)
    diag_df = pl.DataFrame(
        {
            "diag_date": [date(2026, 3, 7)],
            "symbol": ["RELIANCE"],
            "eligible": [True],
            "tt_pass": [True],
            "tt1_close_above_sma150": [True],
            "tt2_close_above_sma200": [True],
            "tt3_sma150_above_sma200": [True],
            "tt4_sma200_rising": [True],
            "tt5_sma50_above_sma150": [True],
            "tt6_sma50_above_sma200": [True],
            "tt7_close_above_sma50": [True],
            "tt8_above_52w_low": [True],
            "tt9_near_52w_high": [False],
            "rs_rank": [85.0],
            "rs_pass": [True],
            "setup_pass": [True],
            "vcp1_range10_ok": [True],
            "vcp2_voldry_ok": [True],
            "vcp3_atr_trend_ok": [True],
            "vcp4_atr_contract_ok": [False],
            "vcp5_extension_ok": [True],
            "vcp6_base_depth_ok": [True],
            "contraction_count": [2],
            "contraction_quality": [0.65],
            "pivot": [1220.0],
            "near_pivot": [0.004],
            "setup_active_streak": [5],
            "pivot_age_days": [3],
            "watch_fail_mask": [0],
            "breakout_fail_mask": [16],
            "created_at": [now],
        }
    )

    store.replace_daily_diagnostics(date(2026, 3, 7), diag_df)
    fetched = store.fetch_daily_diagnostics(date(2026, 3, 7))
    assert fetched.height == 1
    row = fetched.to_dicts()[0]
    assert row["symbol"] == "RELIANCE"
    assert row["tt_pass"] is True
    assert row["tt9_near_52w_high"] is False
    assert row["setup_active_streak"] == 5
    assert row["pivot_age_days"] == 3
    assert row["watch_fail_mask"] == 0
    assert row["breakout_fail_mask"] == 16

    store.replace_daily_diagnostics(date(2026, 3, 7), pl.DataFrame())
    assert store.fetch_daily_diagnostics(date(2026, 3, 7)).height == 0

    store.close()

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.types import RunLog, SetupReview, Signal, WatchlistCandidate


def test_duckdb_store_initializes_and_upserts_records(tmp_path: Path) -> None:
    db_path = tmp_path / "sepa.duckdb"
    store = DuckDbStore(db_path)
    started_at = datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc)
    created_at = datetime(2026, 3, 7, 10, 5, tzinfo=timezone.utc)

    store.initialize()
    store.insert_run(
        RunLog(
            run_id="run-1",
            started_at=started_at,
            finished_at=created_at,
            scan_date=date(2026, 3, 7),
            market_filter_on=True,
            num_symbols=1200,
            num_eligible=850,
            num_watch=2,
            num_breakout=1,
            status="success",
            error=None,
        )
    )
    first_signal = Signal(
        signal_date=date(2026, 3, 7),
        symbol="RELIANCE",
        signal_type="WATCH",
        close=1215.0,
        pivot=1220.0,
        near_pivot=0.0041,
        volume=1000000,
        vol50=750000.0,
        vol_mult=1.33,
        rs_rank=82.0,
        range10=0.05,
        voldry=0.74,
        atr_pct=0.021,
        adv50=1_250_000_000.0,
        entry_ref=1221.22,
        stop=1134.0,
        r_per_share=87.22,
        created_at=created_at,
    )
    replacement_signal = replace(
        first_signal,
        close=1216.0,
        created_at=datetime(2026, 3, 7, 10, 6, tzinfo=timezone.utc),
    )

    store.upsert_signals([first_signal, replacement_signal])

    rows = store.fetch_signals(signal_date=date(2026, 3, 7))
    health = store.health_summary()

    assert len(rows) == 1
    assert rows[0]["close"] == 1216.0
    assert health["last_run_status"] == "success"
    assert health["latest_signal_date"] == date(2026, 3, 7)
    assert health["signals_on_latest_date"] == 1


def test_duckdb_store_fetches_recent_breakouts_by_scan_session(tmp_path: Path) -> None:
    store = DuckDbStore(tmp_path / "sepa.duckdb")
    store.initialize()
    conn = store._connect()
    conn.execute(
        """
        INSERT INTO runs (
            run_id, started_at, scan_date, num_symbols, num_eligible,
            num_watch, num_breakout, status
        ) VALUES
            ('d1', NOW(), '2026-03-02', 100, 80, 0, 1, 'success'),
            ('d2', NOW(), '2026-03-03', 100, 80, 0, 0, 'success'),
            ('d3', NOW(), '2026-03-04', 100, 80, 0, 1, 'success')
        """
    )
    conn.execute(
        """
        INSERT INTO signals (
            signal_date, symbol, signal_type, close, "pivot", near_pivot,
            volume, vol50, vol_mult, rs_rank, range10, voldry, atr_pct,
            adv50, entry_ref, stop, r_per_share, created_at
        ) VALUES
            ('2026-03-02', 'OLD', 'BREAKOUT', 100, 99, 0, 1000, 500, 2, 90, 0.05, 0.5, 0.02, 1000000, 100, 95, 5, NOW()),
            ('2026-03-04', 'RECENT', 'BREAKOUT', 100, 99, 0, 1000, 500, 2, 90, 0.05, 0.5, 0.02, 1000000, 100, 95, 5, NOW())
        """
    )

    symbols = store.fetch_recent_breakout_symbols(date(2026, 3, 5), 2)

    assert symbols == {"RECENT"}


def test_duckdb_store_upserts_and_fetches_watchlist_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "sepa.duckdb"
    store = DuckDbStore(db_path)
    created_at = datetime(2026, 3, 7, 10, 5, tzinfo=timezone.utc)

    first_candidate = WatchlistCandidate(
        watch_date=date(2026, 3, 7),
        symbol="RELIANCE",
        close=1215.0,
        pivot=1220.0,
        near_pivot=0.0041,
        rs_rank=82.0,
        range10=0.05,
        voldry=0.74,
        atr_pct=0.021,
        adv50=1_250_000_000.0,
        vol50=750_000.0,
        created_at=created_at,
    )
    second_candidate = WatchlistCandidate(
        watch_date=date(2026, 3, 7),
        symbol="TCS",
        close=4010.0,
        pivot=4100.0,
        near_pivot=0.0219,
        rs_rank=91.0,
        range10=0.04,
        voldry=0.66,
        atr_pct=0.018,
        adv50=980_000_000.0,
        vol50=220_000.0,
        created_at=created_at,
    )
    replacement_candidate = replace(
        first_candidate,
        close=1216.0,
        created_at=datetime(2026, 3, 7, 10, 6, tzinfo=timezone.utc),
    )

    store.upsert_watchlist_candidates(
        [first_candidate, second_candidate, replacement_candidate]
    )

    rows = store.fetch_watchlist_candidates(watch_date=date(2026, 3, 7))

    assert len(rows) == 2
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["close"] == 1216.0
    assert rows[1]["symbol"] == "TCS"

    store.replace_watchlist_candidates(date(2026, 3, 7), [second_candidate])

    replaced_rows = store.fetch_watchlist_candidates(watch_date=date(2026, 3, 7))
    assert len(replaced_rows) == 1
    assert replaced_rows[0]["symbol"] == "TCS"


def test_duckdb_store_fetches_valid_scan_sessions_with_signal_activity(tmp_path: Path) -> None:
    store = DuckDbStore(tmp_path / "sepa.duckdb")
    store.initialize()
    conn = store._connect()

    conn.execute(
        """
        INSERT INTO runs (
            run_id, started_at, scan_date, num_symbols, num_eligible,
            num_watch, num_breakout, status
        ) VALUES
            ('latest', '2026-03-09 10:00:00', '2026-03-09', 100, 80, 1, 2, 'success'),
            ('quiet', '2026-03-08 10:00:00', '2026-03-08', 100, 80, 0, 0, 'success'),
            ('failed', '2026-03-07 10:00:00', '2026-03-07', 100, 80, 0, 1, 'failed')
        """
    )
    conn.execute(
        """
        INSERT INTO daily_diagnostics (diag_date, symbol, created_at) VALUES
            ('2026-03-09', 'RELIANCE', NOW()),
            ('2026-03-08', 'TCS', NOW()),
            ('2026-03-07', 'INFY', NOW())
        """
    )
    conn.execute(
        """
        INSERT INTO signals (
            signal_date, symbol, signal_type, close, "pivot", near_pivot,
            volume, vol50, vol_mult, rs_rank, range10, voldry, atr_pct,
            adv50, entry_ref, stop, r_per_share, created_at
        ) VALUES
            ('2026-03-09', 'RELIANCE', 'BREAKOUT', 100, 99, 0, 1000, 500, 2, 95, 0.05, 0.5, 0.02, 1000000, 100, 95, 5, NOW()),
            ('2026-03-09', 'HDFCBANK', 'BREAKOUT', 100, 99, 0, 1000, 500, 2, 90, 0.05, 0.5, 0.02, 1000000, 100, 95, 5, NOW()),
            ('2026-03-09', 'TCS', 'WATCH', 100, 101, 0.01, 1000, 500, 2, 85, 0.05, 0.5, 0.02, 1000000, 100, 95, 5, NOW())
        """
    )

    sessions = store.fetch_scan_sessions()

    assert [row["date"] for row in sessions] == [date(2026, 3, 9), date(2026, 3, 8)]
    assert sessions[0]["breakout_count"] == 2
    assert sessions[0]["watch_count"] == 1
    assert sessions[0]["breakout_symbols"] == ["RELIANCE", "HDFCBANK"]
    assert sessions[1]["breakout_count"] == 0
    assert sessions[1]["watch_count"] == 0


def test_duckdb_store_upserts_and_fetches_setup_reviews(tmp_path: Path) -> None:
    db_path = tmp_path / "sepa.duckdb"
    store = DuckDbStore(db_path)
    reviewed_at = datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc)

    first_review = SetupReview(
        scan_date=date(2026, 3, 7),
        symbol="RELIANCE",
        review_status="shortlisted",
        priority=2,
        note="Tight handle, volume dry-up looks clean",
        reviewed_at=reviewed_at,
        updated_at=reviewed_at,
    )
    replacement_review = replace(
        first_review,
        priority=3,
        note="Promote if market confirms",
        updated_at=datetime(2026, 3, 8, 11, 0, tzinfo=timezone.utc),
    )

    store.upsert_setup_reviews([first_review, replacement_review])

    rows = store.fetch_setup_reviews(scan_date=date(2026, 3, 7))

    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["review_status"] == "shortlisted"
    assert rows[0]["priority"] == 3
    assert rows[0]["note"] == "Promote if market confirms"

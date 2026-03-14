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


def test_upsert_and_fetch_corporate_actions(tmp_path: Path) -> None:
    import polars as pl

    store = DuckDbStore(tmp_path / "test.duckdb")
    store.initialize()

    actions = pl.DataFrame({
        "symbol": ["IRCTC", "TCS"],
        "isin": [None, None],
        "action_type": ["split", "bonus"],
        "ex_date": [date(2022, 10, 28), date(2018, 7, 18)],
        "ratio_num": [None, 1],
        "ratio_den": [None, 1],
        "old_face_value": [10.0, None],
        "new_face_value": [2.0, None],
        "adjustment_factor": [0.2, 0.5],
        "description": ["5:1 split", "1:1 bonus"],
        "source": ["seed", "seed"],
    })
    store.upsert_corporate_actions(actions)
    result = store.fetch_corporate_actions()
    assert result.height == 2
    assert set(result["symbol"].to_list()) == {"IRCTC", "TCS"}

    filtered = store.fetch_corporate_actions_for_symbols(["IRCTC"])
    assert filtered.height == 1
    assert filtered["symbol"].to_list() == ["IRCTC"]

    store.close()

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from sepa_scanner.types import DownloadManifest, RunLog, SetupReview, Signal, WatchlistCandidate
from sepa_scanner.utils.io import ensure_directory


class DuckDbStore:
    def __init__(self, db_path: Path, *, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        if not read_only:
            ensure_directory(db_path.parent)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._initialized = False

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path), read_only=self.read_only)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        if self.read_only:
            self._initialized = True
            return
        schema_path = Path(__file__).with_name("schema.sql")
        with schema_path.open("r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        self._connect().execute(schema_sql)
        self._initialized = True

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def insert_run(self, run: RunLog) -> None:
        self.initialize()
        self._connect().execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id,
                started_at,
                finished_at,
                scan_date,
                market_filter_on,
                num_symbols,
                num_eligible,
                num_watch,
                num_breakout,
                status,
                error,
                num_trend_pass,
                num_rs_pass,
                num_setup_pass,
                num_near_pivot,
                bhavcopy_sha256,
                bhavcopy_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run.run_id,
                self._normalize_datetime(run.started_at),
                self._normalize_datetime(run.finished_at),
                run.scan_date,
                run.market_filter_on,
                run.num_symbols,
                run.num_eligible,
                run.num_watch,
                run.num_breakout,
                run.status,
                run.error,
                run.num_trend_pass,
                run.num_rs_pass,
                run.num_setup_pass,
                run.num_near_pivot,
                run.bhavcopy_sha256,
                run.bhavcopy_url,
            ],
        )

    def insert_download(self, manifest: DownloadManifest) -> None:
        self.initialize()
        self._connect().execute(
            """
            INSERT OR REPLACE INTO downloads (
                download_id, trade_date, report_name, url, source,
                downloaded_at, http_status, content_length, etag,
                last_modified, file_path, sha256_zip,
                rows_total, rows_after_series_filter,
                rows_after_dedupe, duplicates_dropped, parse_notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                manifest.download_id,
                manifest.trade_date,
                manifest.report_name,
                manifest.url,
                manifest.source,
                self._normalize_datetime(manifest.downloaded_at),
                manifest.http_status,
                manifest.content_length,
                manifest.etag,
                manifest.last_modified,
                manifest.file_path,
                manifest.sha256_zip,
                manifest.rows_total,
                manifest.rows_after_series_filter,
                manifest.rows_after_dedupe,
                manifest.duplicates_dropped,
                manifest.parse_notes,
            ],
        )

    def upsert_signals(self, signals: list[Signal]) -> None:
        if not signals:
            return
        self.initialize()
        rows = [
            (
                signal.signal_date,
                signal.symbol,
                signal.signal_type,
                signal.close,
                signal.pivot,
                signal.near_pivot,
                signal.volume,
                signal.vol50,
                signal.vol_mult,
                signal.rs_rank,
                signal.range10,
                signal.voldry,
                signal.atr_pct,
                signal.adv50,
                signal.entry_ref,
                signal.stop,
                signal.r_per_share,
                self._normalize_datetime(signal.created_at),
            )
            for signal in signals
        ]
        self._connect().executemany(
            """
            INSERT OR REPLACE INTO signals (
                signal_date,
                symbol,
                signal_type,
                close,
                "pivot",
                near_pivot,
                volume,
                vol50,
                vol_mult,
                rs_rank,
                range10,
                voldry,
                atr_pct,
                adv50,
                entry_ref,
                stop,
                r_per_share,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def fetch_signals(self, signal_date) -> list[dict]:
        self.initialize()
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT *
            FROM signals
            WHERE signal_date = ?
            ORDER BY signal_type, symbol
            """,
            [signal_date],
        ).fetchall()
        columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def upsert_watchlist_candidates(self, candidates: list[WatchlistCandidate]) -> None:
        if not candidates:
            return
        self.initialize()
        rows = [
            (
                candidate.watch_date,
                candidate.symbol,
                candidate.close,
                candidate.pivot,
                candidate.near_pivot,
                candidate.rs_rank,
                candidate.range10,
                candidate.voldry,
                candidate.atr_pct,
                candidate.adv50,
                candidate.vol50,
                self._normalize_datetime(candidate.created_at),
            )
            for candidate in candidates
        ]
        self._connect().executemany(
            """
            INSERT OR REPLACE INTO watchlist_candidates (
                watch_date,
                symbol,
                close,
                "pivot",
                near_pivot,
                rs_rank,
                range10,
                voldry,
                atr_pct,
                adv50,
                vol50,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def replace_watchlist_candidates(
        self, watch_date: date, candidates: list[WatchlistCandidate]
    ) -> None:
        self.initialize()
        conn = self._connect()
        conn.begin()
        try:
            conn.execute(
                "DELETE FROM watchlist_candidates WHERE watch_date = ?",
                [watch_date],
            )
            if candidates:
                conn.executemany(
                    """
                    INSERT INTO watchlist_candidates (
                        watch_date,
                        symbol,
                        close,
                        "pivot",
                        near_pivot,
                        rs_rank,
                        range10,
                        voldry,
                        atr_pct,
                        adv50,
                        vol50,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            candidate.watch_date,
                            candidate.symbol,
                            candidate.close,
                            candidate.pivot,
                            candidate.near_pivot,
                            candidate.rs_rank,
                            candidate.range10,
                            candidate.voldry,
                            candidate.atr_pct,
                            candidate.adv50,
                            candidate.vol50,
                            self._normalize_datetime(candidate.created_at),
                        )
                        for candidate in candidates
                    ],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fetch_watchlist_candidates(
        self, watch_date: date, limit: int | None = None
    ) -> list[dict]:
        self.initialize()
        params: list[object] = [watch_date]
        query = """
            SELECT *
            FROM watchlist_candidates
            WHERE watch_date = ?
            ORDER BY near_pivot ASC, rs_rank DESC, adv50 DESC, symbol ASC
        """
        if limit is not None:
            query = f"{query}\nLIMIT ?"
            params.append(limit)

        conn = self._connect()
        rows = conn.execute(query, params).fetchall()
        columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def upsert_setup_reviews(self, reviews: list[SetupReview]) -> None:
        if not reviews:
            return
        self.initialize()
        rows = [
            (
                review.scan_date,
                review.symbol,
                review.review_status,
                review.priority,
                review.note,
                self._normalize_datetime(review.reviewed_at),
                self._normalize_datetime(review.updated_at),
            )
            for review in reviews
        ]
        self._connect().executemany(
            """
            INSERT OR REPLACE INTO setup_reviews (
                scan_date,
                symbol,
                review_status,
                priority,
                note,
                reviewed_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def fetch_setup_reviews(self, scan_date: date) -> list[dict]:
        self.initialize()
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT *
            FROM setup_reviews
            WHERE scan_date = ?
            ORDER BY priority DESC, updated_at DESC, symbol ASC
            """,
            [scan_date],
        ).fetchall()
        columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row, strict=False)) for row in rows]

    def replace_daily_diagnostics(self, diag_date: date, df: pl.DataFrame) -> None:
        self.initialize()
        conn = self._connect()
        conn.begin()
        try:
            conn.execute(
                "DELETE FROM daily_diagnostics WHERE diag_date = ?", [diag_date]
            )
            if not df.is_empty():
                conn.execute(
                    "INSERT INTO daily_diagnostics SELECT * FROM df"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fetch_daily_diagnostics(self, diag_date: date) -> pl.DataFrame:
        self.initialize()
        return self._connect().execute(
            """
            SELECT *
            FROM daily_diagnostics
            WHERE diag_date = ?
            ORDER BY symbol ASC
            """,
            [diag_date],
        ).pl()

    def health_summary(self) -> dict:
        self.initialize()
        row = self._connect().execute(
            """
            WITH latest_run AS (
                SELECT status
                FROM runs
                ORDER BY started_at DESC
                LIMIT 1
            ),
            latest_signal_date AS (
                SELECT MAX(signal_date) AS signal_date
                FROM signals
            )
            SELECT
                (SELECT status FROM latest_run) AS last_run_status,
                (SELECT signal_date FROM latest_signal_date) AS latest_signal_date,
                COALESCE(
                    (
                        SELECT COUNT(*)
                        FROM signals
                        WHERE signal_date = (SELECT signal_date FROM latest_signal_date)
                    ),
                    0
                ) AS signals_on_latest_date
            """
        ).fetchone()
        return {
            "last_run_status": row[0],
            "latest_signal_date": row[1],
            "signals_on_latest_date": row[2],
        }

    def upsert_corporate_actions(self, df: pl.DataFrame) -> None:
        if df.is_empty():
            return
        self.initialize()
        conn = self._connect()
        conn.execute("INSERT OR REPLACE INTO corporate_actions SELECT * FROM df")

    def fetch_corporate_actions(self) -> pl.DataFrame:
        self.initialize()
        return self._connect().execute(
            "SELECT * FROM corporate_actions ORDER BY symbol, ex_date"
        ).pl()

    def fetch_corporate_actions_for_symbols(self, symbols: list[str]) -> pl.DataFrame:
        self.initialize()
        if not symbols:
            return pl.DataFrame(schema={
                "symbol": pl.String, "isin": pl.String,
                "action_type": pl.String, "ex_date": pl.Date,
                "ratio_num": pl.Int32, "ratio_den": pl.Int32,
                "old_face_value": pl.Float64, "new_face_value": pl.Float64,
                "adjustment_factor": pl.Float64,
                "description": pl.String, "source": pl.String,
            })
        return self._connect().execute(
            "SELECT * FROM corporate_actions WHERE symbol IN (SELECT UNNEST(?)) ORDER BY symbol, ex_date",
            [symbols],
        ).pl()

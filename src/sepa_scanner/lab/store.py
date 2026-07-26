"""DuckDB persistence for lab runs.

A run row is written before the work starts so the UI can show it immediately,
then updated as it progresses. Equity and trades are written once, on success.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from sepa_scanner.lab.result import BacktestRunResult

_ACTIVE_STATUSES = ("queued", "running")


class LabStoreLocked(RuntimeError):
    """The lab database is held by another process (usually a running API)."""


class LabStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> duckdb.DuckDBPyConnection:
        # Always the same configuration. DuckDB refuses a second connection to
        # an open database when the config differs, so mixing a read_only
        # reader with the job worker's writer breaks every read mid-run.
        try:
            return duckdb.connect(str(self.db_path))
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower():
                raise
            # DuckDB is single-writer *across processes*. The usual cause is a
            # running `sepa ui` (its API holds the lock), so say that instead of
            # surfacing a raw IO error about lock files.
            raise LabStoreLocked(
                f"{self.db_path} is locked by another process. The lab store is "
                "single-writer: stop `sepa ui` (or whatever else holds the "
                "database) before running a lab backtest from the CLI.\n\n"
                f"original error: {exc}"
            ) from exc

    def initialize(self) -> None:
        schema = (
            Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
        ).read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(schema)

    def fail_orphaned_runs(self) -> int:
        """Mark runs abandoned by a process restart as failed.

        The job runner is in-process, so anything still queued or running at
        startup has no worker behind it and would otherwise poll forever.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT count(*) FROM lab_runs WHERE status IN (?, ?)", _ACTIVE_STATUSES
            ).fetchone()
            orphaned = int(rows[0]) if rows else 0
            if orphaned:
                conn.execute(
                    """
                    UPDATE lab_runs
                       SET status = 'failed',
                           error = 'interrupted by restart',
                           finished_at = ?
                     WHERE status IN (?, ?)
                    """,
                    [datetime.now(UTC), *_ACTIVE_STATUSES],
                )
            return orphaned

    def create_run(
        self,
        *,
        run_id: str,
        strategy_id: str,
        kind: str,
        params: dict[str, Any],
        params_hash: str,
        start_date: date,
        end_date: date,
        data_source: str,
        label: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO lab_runs (
                    run_id, strategy_id, label, params_json, params_hash,
                    start_date, end_date, kind, data_source, status,
                    progress_pct, stage, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, 'queued', ?)
                """,
                [
                    run_id,
                    strategy_id,
                    label,
                    json.dumps(params, default=str),
                    params_hash,
                    start_date,
                    end_date,
                    kind,
                    data_source,
                    datetime.now(UTC),
                ],
            )

    def update_progress(self, run_id: str, *, stage: str, progress_pct: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE lab_runs SET status='running', stage=?, progress_pct=? WHERE run_id=?",
                [stage, progress_pct, run_id],
            )

    def mark_failed(self, run_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE lab_runs
                   SET status='failed', error=?, finished_at=?, stage='failed'
                 WHERE run_id=?
                """,
                [error[:2000], datetime.now(UTC), run_id],
            )

    def save_result(self, result: BacktestRunResult) -> None:
        payload = result.to_dict()
        with self._connect() as conn:
            # Publish the completed run atomically with its child rows. Without
            # an explicit transaction, a poller can observe status='done'
            # after the run update but before equity/trades have been inserted.
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("DELETE FROM lab_equity WHERE run_id=?", [result.run_id])
                conn.execute("DELETE FROM lab_trades WHERE run_id=?", [result.run_id])

                if result.equity_curve:
                    conn.executemany(
                        "INSERT INTO lab_equity VALUES (?, ?, ?, ?, ?)",
                        [
                            [
                                result.run_id,
                                p.date,
                                p.equity,
                                p.drawdown_pct,
                                p.benchmark_equity,
                            ]
                            for p in result.equity_curve
                        ],
                    )
                if result.trades:
                    conn.executemany(
                        "INSERT INTO lab_trades VALUES "
                        + "("
                        + ",".join(["?"] * 20)
                        + ")",
                        [
                            [
                                result.run_id,
                                t.symbol,
                                t.direction,
                                t.signal_date,
                                t.entry_date,
                                t.exit_date,
                                t.entry_price,
                                t.exit_price,
                                t.shares,
                                t.stop,
                                t.r_multiple,
                                t.pnl,
                                t.pnl_pct,
                                t.mfe_r,
                                t.mae_r,
                                t.hold_days,
                                t.exit_reason,
                                t.status,
                                t.sector,
                                json.dumps(t.meta, default=str),
                            ]
                            for t in result.trades
                        ],
                    )

                # Status is the publication flag: it changes only after every
                # row needed by the completed-run API response is in place.
                conn.execute(
                    """
                    UPDATE lab_runs
                       SET status='done', stage='done', progress_pct=100,
                           metrics_json=?, extras_json=?, warnings_json=?,
                           params_json=?, params_hash=?, data_source=?, finished_at=?
                     WHERE run_id=?
                    """,
                    [
                        json.dumps(payload["metrics"]),
                        json.dumps(payload["extras"]),
                        json.dumps(payload["warnings"]),
                        json.dumps(payload["params"], default=str),
                        result.params_hash,
                        result.data_source,
                        datetime.now(UTC),
                        result.run_id,
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM lab_runs WHERE run_id=?", [run_id]).pl()
        if row.is_empty():
            return None
        record = row.to_dicts()[0]
        return _decode(record)

    def list_runs(
        self,
        *,
        strategy_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM lab_runs WHERE 1=1"
        params: list[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            frame = conn.execute(query, params).pl()
        return [_decode(r) for r in frame.to_dicts()]

    def get_equity(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            frame = conn.execute(
                "SELECT d AS date, equity, drawdown_pct, benchmark_equity "
                "FROM lab_equity WHERE run_id=? ORDER BY d",
                [run_id],
            ).pl()
        return frame.to_dicts()

    def get_trades(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            frame = conn.execute(
                "SELECT * FROM lab_trades WHERE run_id=? ORDER BY signal_date, symbol",
                [run_id],
            ).pl()
        rows = frame.to_dicts()
        for row in rows:
            row.pop("run_id", None)
            raw = row.pop("meta_json", None)
            row["meta"] = json.loads(raw) if raw else {}
        return rows

    def delete_run(self, run_id: str) -> bool:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT count(*) FROM lab_runs WHERE run_id=?", [run_id]
            ).fetchone()
            if not existing or not existing[0]:
                return False
            conn.execute("DELETE FROM lab_equity WHERE run_id=?", [run_id])
            conn.execute("DELETE FROM lab_trades WHERE run_id=?", [run_id])
            conn.execute("DELETE FROM lab_runs WHERE run_id=?", [run_id])
            return True


def _decode(record: dict[str, Any]) -> dict[str, Any]:
    for key, target in (
        ("params_json", "params"),
        ("metrics_json", "metrics"),
        ("extras_json", "extras"),
        ("warnings_json", "warnings"),
    ):
        raw = record.pop(key, None)
        record[target] = json.loads(raw) if raw else None
    return record

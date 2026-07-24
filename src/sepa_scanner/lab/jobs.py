"""In-process job runner for lab backtests.

Full-history runs take minutes and hold a large price panel in memory, so a
single worker is the default: it bounds memory and serialises DuckDB writes.
Jobs do not survive a restart — the store marks orphans failed at startup
rather than leaving the UI polling something with no worker behind it.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from typing import Any

from sepa_scanner.lab.registry import get_strategy
from sepa_scanner.lab.runner import run_backtest
from sepa_scanner.lab.store import LabStore

LOGGER = logging.getLogger("sepa_scanner.lab.jobs")

_STAGES = (
    ("loading_panel", 10.0),
    ("generating_signals", 30.0),
    ("simulating", 60.0),
    ("computing_metrics", 85.0),
    ("persisting", 95.0),
)


class JobRunner:
    def __init__(self, store: LabStore, *, max_workers: int = 1) -> None:
        self.store = store
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="lab-run"
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        strategy_id: str,
        start_date: date,
        end_date: date,
        *,
        params: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> str:
        # Resolve the strategy before queueing so an unknown id is a request
        # error, not a job that fails asynchronously.
        strategy = get_strategy(strategy_id)
        run_id = uuid.uuid4().hex[:16]

        self.store.create_run(
            run_id=run_id,
            strategy_id=strategy_id,
            kind=strategy.kind.value,
            params=params or {},
            params_hash="",
            start_date=start_date,
            end_date=end_date,
            data_source="pending",
            label=label,
        )

        future = self._pool.submit(
            self._execute, run_id, strategy_id, start_date, end_date, params
        )
        with self._lock:
            self._futures[run_id] = future
        return run_id

    def _execute(
        self,
        run_id: str,
        strategy_id: str,
        start_date: date,
        end_date: date,
        params: dict[str, Any] | None,
    ) -> None:
        try:
            for stage, pct in _STAGES[:2]:
                self.store.update_progress(run_id, stage=stage, progress_pct=pct)

            result = run_backtest(
                strategy_id, start_date, end_date, params=params, run_id=run_id
            )

            self.store.update_progress(
                run_id, stage="persisting", progress_pct=95.0
            )
            self.store.save_result(result)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as run state
            LOGGER.exception("lab run %s failed", run_id)
            self.store.mark_failed(run_id, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._futures.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
        return future is not None and not future.done()

    def shutdown(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

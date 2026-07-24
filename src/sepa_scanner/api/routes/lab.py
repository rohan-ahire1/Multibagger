"""Strategy lab API: list strategies, run backtests as jobs, compare results."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sepa_scanner.api.deps import get_settings
from sepa_scanner.lab.jobs import JobRunner
from sepa_scanner.lab.registry import get_strategy, list_strategies, resolve_params
from sepa_scanner.lab.store import LabStore
from sepa_scanner.settings import AppSettings

import sepa_scanner.lab_strategies  # noqa: F401  (registers strategies on import)

router = APIRouter(prefix="/api/lab", tags=["lab"])

_store: LabStore | None = None
_runner: JobRunner | None = None


def get_store(settings: AppSettings = Depends(get_settings)) -> LabStore:
    global _store
    if _store is None:
        store = LabStore(settings.runtime.data_dir / "db" / "sepa.duckdb")
        store.initialize()
        orphaned = store.fail_orphaned_runs()
        if orphaned:
            import logging

            logging.getLogger("sepa_scanner.lab").warning(
                "marked %d interrupted lab run(s) as failed", orphaned
            )
        _store = store
    return _store


def get_runner(store: LabStore = Depends(get_store)) -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner(store)
    return _runner


class RunRequest(BaseModel):
    strategy_id: str
    start_date: date
    end_date: date
    params: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class CompareRequest(BaseModel):
    run_ids: list[str]


@router.get("/strategies")
def list_lab_strategies(settings: AppSettings = Depends(get_settings)) -> dict:
    strategies = []
    for strategy in list_strategies():
        strategies.append(
            {
                "id": strategy.id,
                "name": strategy.name,
                "kind": strategy.kind.value,
                "description": strategy.description,
                "data_requirement": getattr(
                    strategy, "data_requirement", None
                ).value,
                "param_specs": [asdict(spec) for spec in strategy.param_specs],
                "defaults": strategy.defaults(settings),
            }
        )
    return {"strategies": strategies}


@router.get("/strategies/{strategy_id}/defaults")
def strategy_defaults(
    strategy_id: str, settings: AppSettings = Depends(get_settings)
) -> dict:
    try:
        strategy = get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"strategy_id": strategy_id, "defaults": strategy.defaults(settings)}


@router.post("/runs", status_code=202)
def create_run(
    req: RunRequest,
    settings: AppSettings = Depends(get_settings),
    runner: JobRunner = Depends(get_runner),
) -> dict:
    try:
        strategy = get_strategy(req.strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if req.end_date <= req.start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")

    # Validate before queueing so a bad parameter is a 422 rather than a job
    # that fails minutes later.
    try:
        resolve_params(strategy, settings, req.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = runner.submit(
        req.strategy_id,
        req.start_date,
        req.end_date,
        params=req.params,
        label=req.label,
    )
    return {"run_id": run_id, "status": "queued"}


@router.get("/runs")
def list_runs(
    strategy_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    store: LabStore = Depends(get_store),
) -> dict:
    return {"runs": store.list_runs(strategy_id=strategy_id, status=status, limit=limit)}


@router.get("/runs/{run_id}/status")
def run_status(run_id: str, store: LabStore = Depends(get_store)) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    return {
        "run_id": run_id,
        "status": run["status"],
        "progress_pct": run["progress_pct"],
        "stage": run["stage"],
        "error": run["error"],
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, store: LabStore = Depends(get_store)) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")

    # An unfinished run has no curve or trades yet; return the envelope so the
    # client can render progress without a second request.
    if run["status"] != "done":
        return {"run": run, "equity_curve": [], "trades": []}

    return {
        "run": run,
        "equity_curve": store.get_equity(run_id),
        "trades": store.get_trades(run_id),
    }


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, store: LabStore = Depends(get_store)) -> dict:
    if not store.delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    return {"deleted": run_id}


@router.post("/compare")
def compare_runs(req: CompareRequest, store: LabStore = Depends(get_store)) -> dict:
    if len(req.run_ids) < 2:
        raise HTTPException(status_code=422, detail="compare needs at least two runs")

    loaded: list[dict[str, Any]] = []
    for run_id in req.run_ids:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        if run["status"] != "done":
            raise HTTPException(
                status_code=409, detail=f"run {run_id!r} is {run['status']}, not done"
            )
        loaded.append({"run": run, "curve": store.get_equity(run_id)})

    # Align on the overlapping window. Intersecting every run at once would let
    # a single non-overlapping run empty the window for all of them, so build
    # the common window greedily from the longest curve and set aside any run
    # that would collapse it. Those are reported, never silently dropped.
    ordered = sorted(loaded, key=lambda item: len(item["curve"]), reverse=True)

    common: set[date] = set()
    disjoint: list[str] = []
    for item in ordered:
        dates = {row["date"] for row in item["curve"]}
        if not common:
            common = dates
            continue
        overlap = common & dates
        if overlap:
            common = overlap
        else:
            disjoint.append(item["run"]["run_id"])

    series = []
    for item in loaded:
        run = item["run"]
        if run["run_id"] in disjoint:
            continue
        points = sorted(
            (row for row in item["curve"] if row["date"] in common),
            key=lambda r: r["date"],
        )
        if not points:
            disjoint.append(run["run_id"])
            continue
        base = points[0]["equity"]
        series.append(
            {
                "run_id": run["run_id"],
                "strategy_id": run["strategy_id"],
                "label": run["label"],
                "metrics": run["metrics"],
                "points": [
                    {
                        "date": p["date"].isoformat(),
                        "value": (p["equity"] / base) * 100.0 if base else None,
                        "drawdown_pct": p["drawdown_pct"],
                    }
                    for p in points
                ],
            }
        )

    sources = {item["run"]["data_source"] for item in loaded}
    warnings = []
    if len(sources) > 1:
        warnings.append(
            f"runs span different data sources ({', '.join(sorted(sources))}); "
            "their metrics are not directly comparable"
        )
    if disjoint:
        warnings.append(
            f"{len(disjoint)} run(s) share no dates with the others and were excluded"
        )

    return {
        "series": series,
        "disjoint_run_ids": disjoint,
        "common_days": len(common),
        "warnings": warnings,
    }

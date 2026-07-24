from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from sepa_scanner.api.app import create_app
from sepa_scanner.lab.contracts import StrategyKind
from sepa_scanner.lab.jobs import JobRunner
from sepa_scanner.lab.result import (
    BacktestRunResult,
    EquityPoint,
    RunMetrics,
    TradeRecord,
)
from sepa_scanner.lab.store import LabStore

import sepa_scanner.lab_strategies  # noqa: F401


@pytest.fixture
def store(tmp_path) -> LabStore:
    s = LabStore(tmp_path / "lab.duckdb")
    s.initialize()
    return s


def _metrics(**over) -> RunMetrics:
    base = dict(
        cagr_pct=12.0, total_return_pct=100.0, terminal_equity=2_000_000.0,
        max_drawdown_pct=20.0, max_drawdown_days=200, calmar=0.6, sharpe=0.9,
        sortino=1.1, volatility_pct=13.0, trades=10, win_rate=40.0,
    )
    base.update(over)
    return RunMetrics(**base)


def _result(run_id: str, strategy_id: str = "sepa_breakout", **over) -> BacktestRunResult:
    return BacktestRunResult(
        run_id=run_id,
        strategy_id=strategy_id,
        kind=StrategyKind.SIGNAL,
        params={"rs_rank_min": 70.0},
        params_hash="abc123",
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        data_source=over.pop("data_source", "nse_bhavcopy"),
        metrics=over.pop("metrics", _metrics()),
        equity_curve=over.pop("equity_curve", [
            EquityPoint(date(2020, 1, 1), 1_000_000.0, 0.0, 1_000_000.0),
            EquityPoint(date(2020, 6, 1), 1_500_000.0, -2.0, 1_100_000.0),
            EquityPoint(date(2020, 12, 31), 2_000_000.0, 0.0, 1_200_000.0),
        ]),
        trades=[
            TradeRecord(symbol="RELIANCE", signal_date=date(2020, 2, 1),
                        entry_date=date(2020, 2, 2), exit_date=date(2020, 3, 1),
                        r_multiple=2.5, status="closed", meta={"rs_rank": 91.0}),
        ],
        warnings=["careful"],
        created_at=datetime.now(UTC),
    )


# --- store ----------------------------------------------------------------


def test_run_roundtrip(store):
    store.create_run(
        run_id="r1", strategy_id="sepa_breakout", kind="signal", params={},
        params_hash="", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31),
        data_source="pending", label="first",
    )
    queued = store.get_run("r1")
    assert queued["status"] == "queued"
    assert queued["label"] == "first"

    store.save_result(_result("r1"))
    done = store.get_run("r1")
    assert done["status"] == "done"
    assert done["metrics"]["cagr_pct"] == 12.0
    assert done["warnings"] == ["careful"]
    assert len(store.get_equity("r1")) == 3

    trades = store.get_trades("r1")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "RELIANCE"
    assert trades[0]["meta"] == {"rs_rank": 91.0}


def test_saving_twice_does_not_duplicate_rows(store):
    store.create_run(
        run_id="r1", strategy_id="s", kind="signal", params={}, params_hash="",
        start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
    )
    store.save_result(_result("r1"))
    store.save_result(_result("r1"))
    assert len(store.get_equity("r1")) == 3
    assert len(store.get_trades("r1")) == 1


def test_delete_removes_children(store):
    store.create_run(
        run_id="r1", strategy_id="s", kind="signal", params={}, params_hash="",
        start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
    )
    store.save_result(_result("r1"))
    assert store.delete_run("r1") is True
    assert store.get_run("r1") is None
    assert store.get_equity("r1") == []
    assert store.get_trades("r1") == []
    assert store.delete_run("r1") is False


def test_orphaned_runs_are_failed_on_startup(store):
    store.create_run(
        run_id="r1", strategy_id="s", kind="signal", params={}, params_hash="",
        start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
    )
    assert store.fail_orphaned_runs() == 1
    run = store.get_run("r1")
    assert run["status"] == "failed"
    assert "interrupted by restart" in run["error"]
    # Idempotent: a second startup has nothing left to fail.
    assert store.fail_orphaned_runs() == 0


def test_mark_failed_records_the_error(store):
    store.create_run(
        run_id="r1", strategy_id="s", kind="signal", params={}, params_hash="",
        start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
    )
    store.mark_failed("r1", "ValueError: boom")
    run = store.get_run("r1")
    assert run["status"] == "failed"
    assert "boom" in run["error"]


def test_list_runs_filters(store):
    for i, sid in enumerate(["sepa_breakout", "episodic_pivots"]):
        store.create_run(
            run_id=f"r{i}", strategy_id=sid, kind="signal", params={}, params_hash="",
            start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
        )
    assert len(store.list_runs()) == 2
    assert len(store.list_runs(strategy_id="sepa_breakout")) == 1
    assert len(store.list_runs(status="queued")) == 2
    assert len(store.list_runs(status="done")) == 0


# --- jobs -----------------------------------------------------------------


def test_failing_strategy_is_recorded_not_raised(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("sepa_scanner.lab.jobs.run_backtest", boom)
    runner = JobRunner(store)
    run_id = runner.submit("sepa_breakout", date(2020, 1, 1), date(2020, 12, 31))

    deadline = time.time() + 30
    while runner.is_active(run_id) and time.time() < deadline:
        time.sleep(0.05)

    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert "engine exploded" in run["error"]
    runner.shutdown()


def test_submit_rejects_unknown_strategy(store):
    runner = JobRunner(store)
    with pytest.raises(KeyError):
        runner.submit("no_such_strategy", date(2020, 1, 1), date(2020, 12, 31))
    runner.shutdown()


# --- api ------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_strategies_endpoint_exposes_param_specs(client):
    body = client.get("/api/lab/strategies").json()
    ids = {s["id"] for s in body["strategies"]}
    assert {"sepa_breakout", "episodic_pivots"} <= ids

    sepa = next(s for s in body["strategies"] if s["id"] == "sepa_breakout")
    assert sepa["param_specs"]
    spec = next(p for p in sepa["param_specs"] if p["name"] == "rs_rank_min")
    assert spec["type"] == "float"
    assert spec["group"] == "Entry"
    assert sepa["defaults"]["rs_rank_min"] is not None


def test_defaults_endpoint_404s_for_unknown(client):
    assert client.get("/api/lab/strategies/nope/defaults").status_code == 404


def test_run_request_validation(client):
    def post(**over):
        body = {
            "strategy_id": "sepa_breakout",
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
        }
        body.update(over)
        return client.post("/api/lab/runs", json=body)

    assert post(params={"unknown_param": 1}).status_code == 422
    assert post(params={"rs_rank_min": 999}).status_code == 422
    assert post(end_date="2019-01-01").status_code == 422
    assert post(strategy_id="nope").status_code == 404


def test_compare_requires_two_runs(client):
    assert client.post("/api/lab/compare", json={"run_ids": ["a"]}).status_code == 422


def test_compare_404s_on_unknown_run(client):
    assert client.post(
        "/api/lab/compare", json={"run_ids": ["nope1", "nope2"]}
    ).status_code == 404


def test_unknown_run_endpoints_404(client):
    assert client.get("/api/lab/runs/nope").status_code == 404
    assert client.get("/api/lab/runs/nope/status").status_code == 404
    assert client.delete("/api/lab/runs/nope").status_code == 404


def test_compare_aligns_rebases_and_flags_mismatches(tmp_path):
    """Compare must rebase to a common start and never silently drop a run."""
    from sepa_scanner.api.routes import lab as lab_routes

    store = LabStore(tmp_path / "cmp.duckdb")
    store.initialize()

    def make(run_id, points, source="nse_bhavcopy"):
        store.create_run(
            run_id=run_id, strategy_id="sepa_breakout", kind="signal", params={},
            params_hash="", start_date=points[0][0], end_date=points[-1][0],
            data_source=source,
        )
        store.save_result(_result(
            run_id,
            data_source=source,
            equity_curve=[EquityPoint(d, e, 0.0, None) for d, e in points],
        ))

    # Overlapping on 2020-06-01 and 2020-12-31.
    make("a", [(date(2020, 1, 1), 1_000_000.0), (date(2020, 6, 1), 1_200_000.0),
               (date(2020, 12, 31), 1_500_000.0)])
    make("b", [(date(2020, 6, 1), 500_000.0), (date(2020, 12, 31), 750_000.0)])
    # Shares no dates with the others, and a different panel.
    make("c", [(date(2019, 1, 1), 100.0), (date(2019, 6, 1), 120.0)],
         source="yfinance")

    app = create_app()
    app.dependency_overrides[lab_routes.get_store] = lambda: store
    client = TestClient(app)

    body = client.post("/api/lab/compare", json={"run_ids": ["a", "b", "c"]}).json()

    assert body["common_days"] == 2
    assert body["disjoint_run_ids"] == ["c"]
    series = {s["run_id"]: s for s in body["series"]}
    assert set(series) == {"a", "b"}

    # Both rebased to 100 at the first shared date, despite different capital.
    assert series["a"]["points"][0]["value"] == pytest.approx(100.0)
    assert series["b"]["points"][0]["value"] == pytest.approx(100.0)
    # a: 1.2m -> 1.5m = +25%; b: 500k -> 750k = +50%.
    assert series["a"]["points"][-1]["value"] == pytest.approx(125.0)
    assert series["b"]["points"][-1]["value"] == pytest.approx(150.0)

    assert any("different data sources" in w for w in body["warnings"])
    assert any("share no dates" in w for w in body["warnings"])


def test_compare_rejects_unfinished_runs(tmp_path):
    from sepa_scanner.api.routes import lab as lab_routes

    store = LabStore(tmp_path / "cmp2.duckdb")
    store.initialize()
    for run_id in ("a", "b"):
        store.create_run(
            run_id=run_id, strategy_id="s", kind="signal", params={}, params_hash="",
            start_date=date(2020, 1, 1), end_date=date(2020, 12, 31), data_source="x",
        )

    app = create_app()
    app.dependency_overrides[lab_routes.get_store] = lambda: store
    client = TestClient(app)

    res = client.post("/api/lab/compare", json={"run_ids": ["a", "b"]})
    assert res.status_code == 409
    assert "queued" in res.json()["detail"]


@pytest.mark.slow
def test_run_completes_end_to_end_through_the_api(tmp_path):
    """Submit a real backtest and poll it to completion, as the UI does."""
    from sepa_scanner.api.routes import lab as lab_routes

    store = LabStore(tmp_path / "e2e.duckdb")
    store.initialize()
    runner = JobRunner(store)

    app = create_app()
    app.dependency_overrides[lab_routes.get_store] = lambda: store
    app.dependency_overrides[lab_routes.get_runner] = lambda: runner
    client = TestClient(app)

    created = client.post(
        "/api/lab/runs",
        json={
            "strategy_id": "episodic_pivots",
            "start_date": "2018-01-01",
            "end_date": "2019-12-31",
            "label": "e2e",
        },
    )
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    assert created.json()["status"] == "queued"

    deadline = time.time() + 600
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/lab/runs/{run_id}/status").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.5)

    assert status["status"] == "done", f"run did not finish cleanly: {status}"

    body = client.get(f"/api/lab/runs/{run_id}").json()
    assert body["run"]["label"] == "e2e"
    assert body["run"]["metrics"]["cagr_pct"] is not None
    assert body["run"]["data_source"] == "nse_bhavcopy"
    assert len(body["equity_curve"]) > 100
    assert body["trades"]

    # Appears on the leaderboard, and deletes cleanly.
    listed = client.get("/api/lab/runs").json()["runs"]
    assert any(r["run_id"] == run_id for r in listed)
    assert client.delete(f"/api/lab/runs/{run_id}").status_code == 200
    assert client.get(f"/api/lab/runs/{run_id}").status_code == 404

    runner.shutdown()

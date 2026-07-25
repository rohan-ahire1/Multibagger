"""`sepa lab` — run and inspect strategy-lab backtests from the terminal.

Runs synchronously and writes to the same tables the API and UI read, so a
terminal run shows up on the leaderboard like any other.
"""

from __future__ import annotations

import json
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from sepa_scanner.lab.registry import get_strategy, list_strategies, resolve_params
from sepa_scanner.lab.runner import run_backtest
from sepa_scanner.lab.store import LabStore, LabStoreLocked
from sepa_scanner.settings import load_settings

import sepa_scanner.lab_strategies  # noqa: F401  (registers strategies)

app = typer.Typer(help="Strategy lab: run any registered strategy.")
console = Console()


def _store() -> LabStore:
    settings = load_settings()
    store = LabStore(settings.runtime.data_dir / "db" / "sepa.duckdb")
    try:
        store.initialize()
    except LabStoreLocked as exc:
        # A traceback here tells the user nothing actionable; the message does.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    return store


def _fmt(value: float | int | None, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


@app.command("list")
def list_command(
    params: bool = typer.Option(False, "--params", help="Also show each parameter"),
) -> None:
    """List registered strategies."""
    settings = load_settings()
    for strategy in list_strategies():
        console.print(
            f"[bold]{strategy.id}[/bold]  [dim]{strategy.kind.value} · "
            f"{getattr(strategy, 'data_requirement').value}[/dim]"
        )
        console.print(f"  {strategy.description}")
        if params:
            defaults = strategy.defaults(settings)
            for spec in strategy.param_specs:
                bounds = ""
                if spec.min is not None or spec.max is not None:
                    bounds = f" [{spec.min if spec.min is not None else '-inf'}, {spec.max if spec.max is not None else 'inf'}]"
                if spec.choices:
                    bounds = f" {list(spec.choices)}"
                console.print(
                    f"    {spec.name:<28} = {defaults.get(spec.name)!r:<20}"
                    f"[dim]{spec.type}{bounds}[/dim]"
                )
        console.print()


@app.command("run")
def run_command(
    strategy_id: str = typer.Argument(..., help="Strategy id, e.g. sepa_breakout"),
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    param: list[str] = typer.Option(
        None, "--param", "-p", help="Override, repeatable: -p rs_rank_min=75"
    ),
    label: str | None = typer.Option(None, help="Label for the run"),
    save: bool = typer.Option(True, help="Persist to the lab tables"),
) -> None:
    """Run a backtest and print its metrics."""
    settings = load_settings()
    try:
        strategy = get_strategy(strategy_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    overrides: dict[str, object] = {}
    for item in param or []:
        if "=" not in item:
            raise typer.BadParameter(f"--param expects name=value, got {item!r}")
        name, _, raw = item.partition("=")
        overrides[name.strip()] = raw.strip()

    try:
        resolve_params(strategy, settings, overrides)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    store = _store() if save else None

    console.print(f"Running [bold]{strategy_id}[/bold] {start_date} → {end_date}…")
    result = run_backtest(strategy_id, start_date, end_date, params=overrides)

    if store is not None:
        store.create_run(
            run_id=result.run_id,
            strategy_id=result.strategy_id,
            kind=result.kind.value,
            params=result.params,
            params_hash=result.params_hash,
            start_date=start_date,
            end_date=end_date,
            data_source=result.data_source,
            label=label,
        )
        store.save_result(result)

    m = result.metrics
    table = Table(title=f"{strategy_id}  {start_date} → {end_date}", show_header=False)
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("CAGR", _fmt(m.cagr_pct, "%"))
    table.add_row("Max drawdown", _fmt(m.max_drawdown_pct, "%"))
    table.add_row("Calmar", _fmt(m.calmar))
    table.add_row("Sharpe", _fmt(m.sharpe))
    table.add_row("Sortino", _fmt(m.sortino))
    table.add_row("Volatility", _fmt(m.volatility_pct, "%"))
    table.add_row("Total return", _fmt(m.total_return_pct, "%"))
    table.add_row("Benchmark CAGR", _fmt(m.benchmark_cagr_pct, "%"))
    table.add_row("Excess CAGR", _fmt(m.excess_cagr_pct, " pts"))
    if m.trades is not None:
        table.add_row("Trades", str(m.trades))
        table.add_row("Win rate", _fmt(m.win_rate, "%", 1))
        table.add_row("Avg R", _fmt(m.avg_r))
        table.add_row("Payoff", _fmt(m.payoff_ratio))
    console.print(table)

    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")

    if store is not None:
        console.print(f"[dim]saved as run {result.run_id}[/dim]")


@app.command("runs")
def runs_command(
    strategy_id: str | None = typer.Option(None, "--strategy", help="Filter by strategy"),
    limit: int = typer.Option(25, help="Rows to show"),
) -> None:
    """Show the run leaderboard."""
    runs = _store().list_runs(strategy_id=strategy_id, limit=limit)
    if not runs:
        console.print("[dim]no runs yet[/dim]")
        return

    table = Table()
    for column in ("run", "strategy", "label", "window", "status", "CAGR", "MaxDD", "Calmar", "trades"):
        table.add_column(column, justify="right" if column in {"CAGR", "MaxDD", "Calmar", "trades"} else "left")

    for run in runs:
        metrics = run.get("metrics") or {}
        table.add_row(
            run["run_id"][:8],
            run["strategy_id"],
            run.get("label") or "",
            f"{run['start_date']}→{run['end_date']}",
            run["status"],
            _fmt(metrics.get("cagr_pct"), "%"),
            _fmt(metrics.get("max_drawdown_pct"), "%"),
            _fmt(metrics.get("calmar")),
            str(metrics.get("trades") or "—"),
        )
    console.print(table)


@app.command("show")
def show_command(
    run_id: str = typer.Argument(..., help="Run id (full or unique prefix)"),
    trades: int = typer.Option(10, help="Best and worst trades to print"),
) -> None:
    """Show one run in detail."""
    store = _store()
    run = store.get_run(run_id)
    if run is None:
        matches = [r for r in store.list_runs(limit=1000) if r["run_id"].startswith(run_id)]
        if len(matches) != 1:
            raise typer.BadParameter(
                f"run {run_id!r} not found" if not matches else f"{run_id!r} is ambiguous"
            )
        run = matches[0]

    console.print_json(json.dumps({k: v for k, v in run.items() if k != "metrics"}, default=str))
    console.print_json(json.dumps(run.get("metrics"), default=str))

    rows = [t for t in store.get_trades(run["run_id"]) if t.get("r_multiple") is not None]
    if not rows or not trades:
        return
    rows.sort(key=lambda t: t["r_multiple"], reverse=True)

    table = Table(title="best / worst")
    for column in ("symbol", "entry", "exit", "R", "reason"):
        table.add_column(column, justify="right" if column == "R" else "left")
    for row in [*rows[:trades], *rows[-trades:]]:
        table.add_row(
            row["symbol"],
            str(row.get("entry_date") or "—"),
            str(row.get("exit_date") or "—"),
            _fmt(row.get("r_multiple")),
            row.get("exit_reason") or "",
        )
    console.print(table)

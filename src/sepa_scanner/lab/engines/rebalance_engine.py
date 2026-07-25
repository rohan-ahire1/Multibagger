"""Rebalance engine: periodic target weights instead of dated entries.

Wraps ``strategies/daily_factors/engine.simulate_rebalanced_portfolio`` rather
than reimplementing it. That function *is* the engine those recorded results
came from, so wrapping it makes the migration a pure extraction: any difference
against ``strategies/daily_factors/output/summary.json`` is a bug, not a
modelling choice.

Trades here are orders, not positions with stops, so R-multiples do not apply.
Metrics still come from the shared equity-curve summariser, which is what keeps
a factor portfolio comparable with a breakout system.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.lab.result import TradeRecord

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _factors_engine():
    from strategies.daily_factors import engine

    return engine


def weights_to_targets(weights: pl.DataFrame) -> dict[date, dict[str, float]]:
    """Contract frame -> the {rebalance_date: {symbol: weight}} the engine takes."""
    targets: dict[date, dict[str, float]] = {}
    for row in weights.sort(["rebalance_date", "symbol"]).iter_rows(named=True):
        targets.setdefault(row["rebalance_date"], {})[row["symbol"]] = float(row["weight"])
    return targets


def trading_calendar(panel: pl.DataFrame) -> list[date]:
    return panel["date"].unique().sort().to_list()


def run_rebalance_engine(
    weights: pl.DataFrame,
    panel: pl.DataFrame,
    config: Any,
    *,
    name: str = "lab",
) -> dict[str, Any]:
    """Execute target weights and return daily equity plus orders."""
    engine = _factors_engine()
    result = engine.simulate_rebalanced_portfolio(
        name,
        weights_to_targets(weights),
        panel,
        trading_calendar(panel),
        config,
    )
    return {
        "daily_equity": result.daily_equity,
        "orders": result.orders,
        "metrics": result.metrics,
    }


def orders_to_trade_records(orders: pl.DataFrame) -> list[TradeRecord]:
    """One record per order.

    Deliberately not paired into round trips: a rebalance strategy trims and
    adds without ever "closing" a position, so inventing entry/exit pairs would
    fabricate holding periods and returns that the strategy never expressed.
    """
    if orders is None or orders.is_empty():
        return []

    records: list[TradeRecord] = []
    for row in orders.to_dicts():
        side = row.get("side", "buy")
        gross = row.get("gross_value")
        friction = row.get("friction_cost")
        records.append(
            TradeRecord(
                symbol=row["symbol"],
                direction="long",
                signal_date=row.get("signal_date"),
                entry_date=row.get("execution_date") if side == "buy" else None,
                exit_date=row.get("execution_date") if side == "sell" else None,
                entry_price=row.get("raw_price") if side == "buy" else None,
                exit_price=row.get("raw_price") if side == "sell" else None,
                shares=row.get("shares"),
                pnl=None,
                status="closed" if side == "sell" else "open",
                exit_reason=f"rebalance_{side}",
                meta={
                    "side": side,
                    "gross_value": gross,
                    "friction_cost": friction,
                },
            )
        )
    return records


def turnover_pct(orders: pl.DataFrame, initial_capital: float) -> float | None:
    """Traded value as a percentage of starting capital."""
    if orders is None or orders.is_empty() or "gross_value" not in orders.columns:
        return None
    total = float(orders["gross_value"].abs().sum())
    return (total / initial_capital) * 100.0 if initial_capital else None

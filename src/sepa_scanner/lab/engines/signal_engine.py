"""Adapter over the canonical portfolio simulator.

This module deliberately contains no trading logic. Its entire job is to build
the simulator's arguments and map its output into the lab result shape, so that
a lab run of SEPA executes byte-for-byte the same code path as
``optimize_joint_wf.py --parity``. Any behaviour added here is behaviour that
the canonical harness does not have, which is how engines drift apart.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from sepa_scanner.lab.engines.portfolio_sim import simulate_portfolio_python
from sepa_scanner.lab.result import TradeRecord

# Passed explicitly by the canonical harness. Omitting any of them selects a
# different simulator default than the parity run uses.
EXIT_KWARGS: tuple[str, ...] = (
    "breakeven_activation_r",
    "time_stop_days",
    "partial_profit_r",
    "trail_pct_bull",
    "trail_pct_bear",
    "trail_widen_r",
)


def run_signal_engine(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    params: Any,
    portfolio: dict[str, Any],
    sector_map: dict[str, str],
    *,
    exit_config: dict[str, Any] | None,
    exit_kwargs: dict[str, Any],
    entry_gate: dict[date, bool] | None = None,
    entry_size_mult: dict[date, float] | None = None,
    simulation_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the canonical simulator and return its raw dict result."""
    missing = [k for k in EXIT_KWARGS if k not in exit_kwargs]
    if missing:
        raise ValueError(f"exit_kwargs missing canonical keys: {missing}")

    return simulate_portfolio_python(
        signals,
        ohlcv,
        params,
        portfolio,
        sector_map,
        exit_config=exit_config,
        entry_gate=entry_gate or {},
        entry_size_mult=entry_size_mult or {},
        **{k: exit_kwargs[k] for k in EXIT_KWARGS},
        **(simulation_overrides or {}),
    )


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


# The simulator's `trades` frame is not purely closed trades: a position still
# open at the end of the window appears with status "open" (marked to market),
# and an expired trigger appears as "not_filled". Counting either as a closed
# trade would contaminate win rate, average R, and expectancy.
_OPEN_STATUSES = frozenset({"open"})
_UNFILLED_STATUSES = frozenset({"not_filled", "pending_entry", "expired"})


def lifecycle_status(raw_status: str | None) -> str:
    if raw_status in _OPEN_STATUSES:
        return "open"
    if raw_status in _UNFILLED_STATUSES:
        return "not_filled"
    return "closed"


def to_trade_records(trades: pl.DataFrame) -> list[TradeRecord]:
    """Map simulator trade rows into the unified record shape."""
    if trades is None or trades.is_empty():
        return []

    records: list[TradeRecord] = []
    for row in trades.to_dicts():
        entry_date = _as_date(row.get("entry_date"))
        exit_date = _as_date(row.get("exit_date"))
        hold = row.get("holding_days")
        records.append(
            TradeRecord(
                symbol=row["symbol"],
                direction=row.get("direction") or "long",
                signal_date=_as_date(row.get("signal_date")),
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=_float(row.get("entry_price")),
                exit_price=_float(row.get("exit_price")),
                shares=_float(row.get("shares")),
                stop=_float(row.get("stop")),
                r_multiple=_float(row.get("r_multiple")),
                pnl=_float(row.get("pnl")),
                pnl_pct=_float(row.get("return_pct")),
                mfe_r=_float(row.get("mfe_r")),
                mae_r=_float(row.get("mae_r")),
                hold_days=int(hold) if hold is not None else None,
                exit_reason=row.get("status"),
                status=lifecycle_status(row.get("status")),
                sector=row.get("sector"),
                meta={
                    key: row[key]
                    for key in ("rs_rank", "range10", "voldry", "giveback_r", "pivot")
                    if row.get(key) is not None
                },
            )
        )
    return records


def count_entries_in_window(trades: pl.DataFrame, start: date, end: date) -> int:
    """Trades whose entry falls inside the window.

    Mirrors the canonical harness's trade count: the simulator loads OHLCV past
    ``end`` so open positions can be managed to completion, and those later
    entries must not inflate the count.
    """
    if trades is None or trades.is_empty() or "entry_date" not in trades.columns:
        return 0
    return trades.filter(
        pl.col("entry_date").is_not_null()
        & (pl.col("entry_date") >= start)
        & (pl.col("entry_date") <= end)
    ).height

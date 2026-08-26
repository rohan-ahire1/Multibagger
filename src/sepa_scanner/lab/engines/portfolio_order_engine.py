"""Deterministic, cash-backed order engine for ranked portfolio decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any, Callable

import polars as pl

from sepa_scanner.lab.costs import indian_delivery_cost
from sepa_scanner.lab.result import TradeRecord


CostModel = Callable[[str, float], float]


@dataclass(frozen=True, slots=True)
class PortfolioOrderConfig:
    initial_capital: float
    risk_factor: float
    market_regime: pl.DataFrame
    rebalance_every_n_weeks: int
    rebalance_tolerance: float
    require_full_target: bool
    participation_limit_adv: float
    slippage_bps: float
    cost_model: CostModel = indian_delivery_cost
    pending_order_expiry_sessions: int = 1
    cash_yield_pct: float = 0.0
    rank_exit_pct: float = 0.20


def _schema() -> dict[str, pl.DataType]:
    return {
        "date": pl.Date, "decision_date": pl.Date, "symbol": pl.String,
        "side": pl.String, "shares": pl.Int64, "raw_price": pl.Float64,
        "fill_price": pl.Float64, "notional": pl.Float64, "cost": pl.Float64,
        "slippage_cost": pl.Float64, "status": pl.String, "reason": pl.String,
    }


def _frame(rows: list[dict[str, Any]], schema: dict[str, pl.DataType] | None = None) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=schema or _schema()) if rows else pl.DataFrame(schema=schema or _schema())


def _cost(model: CostModel, side: str, notional: float) -> float:
    return float(model(side, notional))


def portfolio_trade_records(orders: pl.DataFrame) -> list[TradeRecord]:
    """Pair filled integer-share orders FIFO into actual round-trip trade records."""
    lots: dict[str, list[dict[str, Any]]] = {}
    records: list[TradeRecord] = []
    for order in orders.filter(pl.col("status") == "filled").sort(["date", "symbol", "side"]).iter_rows(named=True):
        symbol = str(order["symbol"])
        shares = int(order["shares"])
        if order["side"] == "buy":
            lots.setdefault(symbol, []).append({**order, "remaining": shares})
            continue
        remaining = shares
        for lot in lots.get(symbol, []):
            if remaining <= 0:
                break
            # Fully consumed lots stay in the list; without this guard they match
            # zero shares and still emit a phantom trade dated from their original
            # entry, inflating the trade count and the average hold.
            if int(lot["remaining"]) <= 0:
                continue
            matched = min(remaining, int(lot["remaining"]))
            entry_cost = float(lot["cost"]) * matched / int(lot["shares"])
            exit_cost = float(order["cost"]) * matched / shares
            pnl = (float(order["fill_price"]) - float(lot["fill_price"])) * matched - entry_cost - exit_cost
            entry_value = float(lot["fill_price"]) * matched + entry_cost
            records.append(TradeRecord(
                symbol=symbol, signal_date=lot["decision_date"], entry_date=lot["date"],
                exit_date=order["date"], entry_price=float(lot["fill_price"]),
                exit_price=float(order["fill_price"]), shares=float(matched), pnl=pnl,
                pnl_pct=(pnl / entry_value * 100.0) if entry_value else None,
                hold_days=(order["date"] - lot["date"]).days,
                exit_reason=str(order["reason"]), status="closed",
                meta={"entry_cost": entry_cost, "exit_cost": exit_cost},
            ))
            lot["remaining"] -= matched
            remaining -= matched
    return records


def run_portfolio_order_engine(
    decisions: pl.DataFrame, panel: pl.DataFrame, config: PortfolioOrderConfig,
) -> dict[str, Any]:
    """Execute the spec's T+1, sells-first, full-target-or-skip order process."""
    if config.initial_capital <= 0 or config.risk_factor <= 0:
        raise ValueError("initial_capital and risk_factor must be positive")
    if config.rebalance_every_n_weeks < 1 or config.pending_order_expiry_sessions < 0:
        raise ValueError("rebalance and expiry sessions must be non-negative / positive")
    if panel.is_empty():
        raise ValueError("panel is empty")
    clean = panel.sort(["date", "symbol"])
    sessions: list[date] = clean["date"].unique().sort().to_list()
    bars = {(row["date"], row["symbol"]): row for row in clean.iter_rows(named=True)}
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in decisions.sort(["decision_date", "rank", "symbol"]).iter_rows(named=True):
        by_date.setdefault(row["decision_date"], []).append(row)
    regime = {
        row["date"]: bool(row["allows_new_buys"])
        for row in config.market_regime.iter_rows(named=True)
    } if not config.market_regime.is_empty() else {}
    cash = float(config.initial_capital)
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    decision_cycle = 0
    delisted_pnl_impact = 0.0

    def queue(order: dict[str, Any]) -> None:
        order["age"] = 0
        pending.append(order)

    def reject(order: dict[str, Any], today: date, reason: str) -> None:
        record = {**order, "date": today, "status": "rejected", "reason": reason,
                  "raw_price": None, "fill_price": None, "notional": None,
                  "cost": None, "slippage_cost": None}
        rejections.append(record)

    for session_index, today in enumerate(sessions):
        # A missing current bar has no causal explanation in this panel.  In
        # particular, deciding it is terminal by looking for a later bar reads
        # future listing/delisting information.  Carry the last observed value
        # until a dated reference status is supplied; no terminal sale is
        # inferred from a future absence.

        # Pending sells are intentionally processed before buys on every session.
        next_pending: list[dict[str, Any]] = []
        for order in sorted(pending, key=lambda item: (0 if item["side"] == "sell" else 1, item["symbol"])):
            bar = bars.get((today, order["symbol"]))
            unavailable = bar is None or float(bar["volume"] or 0) <= 0
            unavailable = unavailable or (bar is not None and bar.get("tradable") is False)
            unavailable = unavailable or (bar is not None and bar.get("price_band_ok") is False)
            if unavailable:
                order["age"] += 1
                if order["age"] > config.pending_order_expiry_sessions:
                    reject(order, today, "missing_or_zero_volume_expired")
                else:
                    next_pending.append(order)
                continue
            adv_rows = clean.filter(
                (pl.col("symbol") == order["symbol"]) & (pl.col("date") < today)
            ).tail(20)
            adv = float(adv_rows["turnover"].median()) if adv_rows.height else 0.0
            raw = float(bar["open"])
            fill = raw * (1.0 + config.slippage_bps / 10_000.0) if order["side"] == "buy" else raw * (1.0 - config.slippage_bps / 10_000.0)
            notional = fill * int(order["shares"])
            if adv <= 0 or notional > config.participation_limit_adv * adv:
                order["age"] += 1
                if order["age"] > config.pending_order_expiry_sessions:
                    reject(order, today, "participation_limit_expired")
                else:
                    next_pending.append(order)
                continue
            cost = _cost(config.cost_model, order["side"], notional)
            if order["side"] == "buy" and notional + cost > cash + 1e-9:
                reject(order, today, "insufficient_cash_at_fill")
                continue
            record = {"date": today, "decision_date": order["decision_date"], "symbol": order["symbol"],
                      "side": order["side"], "shares": int(order["shares"]), "raw_price": raw,
                      "fill_price": fill, "notional": notional, "cost": cost,
                      "slippage_cost": abs(fill - raw) * int(order["shares"]), "status": "filled",
                      "reason": order["reason"]}
            orders.append(record)
            if order["side"] == "buy":
                cash -= notional + cost
                current = positions.setdefault(order["symbol"], {"shares": 0, "last_close": fill, "last_volume": 0})
                current["shares"] += int(order["shares"])
            else:
                position = positions.get(order["symbol"])
                if position is not None:
                    position["shares"] -= int(order["shares"])
                    if position["shares"] <= 0:
                        positions.pop(order["symbol"], None)
                cash += notional - cost
        pending = next_pending

        if config.cash_yield_pct:
            cash *= 1.0 + config.cash_yield_pct / 252.0
        equity = cash
        for symbol, position in positions.items():
            bar = bars.get((today, symbol))
            if bar is not None:
                position["last_close"] = float(bar["close"])
                position["last_volume"] = float(bar["volume"] or 0)
            equity += position["shares"] * position["last_close"]
            position_rows.append({"date": today, "symbol": symbol, "shares": int(position["shares"]), "close": position["last_close"]})
        equity_rows.append({"date": today, "equity": equity, "cash": cash, "holdings_value": equity - cash})

        rows = by_date.get(today)
        if not rows:
            continue
        decision_cycle += 1
        snapshots = {str(row["symbol"]): row for row in rows}
        # Sells are queued first and rank percentiles always originate from the
        # complete universe decision frame, never the eligible subset.
        planned_sells: set[str] = set()
        for symbol, position in sorted(positions.items()):
            row = snapshots.get(symbol)
            if row is None or not bool(row["is_member"]) or float(row["rank_pct"]) > config.rank_exit_pct or not bool(row["trend_ok"]) or not bool(row["gap_ok"]):
                queue({"decision_date": today, "symbol": symbol, "side": "sell", "shares": int(position["shares"]), "reason": "decision_exit"})
                planned_sells.add(symbol)

        if decision_cycle % config.rebalance_every_n_weeks == 0:
            for symbol, position in sorted(positions.items()):
                if symbol in planned_sells:
                    continue
                row = snapshots.get(symbol)
                if row is None or float(row["atr20"]) <= 0:
                    continue
                target = floor(equity * config.risk_factor / float(row["atr20"]))
                current = int(position["shares"])
                if target <= 0:
                    continue
                target_value = target * float(row.get("close", position["last_close"]))
                current_value = current * position["last_close"]
                if abs(target_value - current_value) / target_value < config.rebalance_tolerance:
                    continue
                if target != current:
                    queue({"decision_date": today, "symbol": symbol, "side": "buy" if target > current else "sell",
                           "shares": abs(target - current), "reason": "resize"})

        if not regime.get(today, False):
            continue
        projected_cash = cash
        for symbol in planned_sells:
            position = positions[symbol]
            projected_cash += position["shares"] * position["last_close"]
        for row in sorted(rows, key=lambda item: (-float(item["score"]), str(item["symbol"]))):
            if float(row["rank_pct"]) > config.rank_exit_pct:
                break
            symbol = str(row["symbol"])
            if symbol in positions or symbol in planned_sells or not (bool(row["is_member"]) and bool(row["trend_ok"]) and bool(row["gap_ok"])):
                continue
            target = floor(equity * config.risk_factor / float(row["atr20"]))
            if target <= 0:
                continue
            reference = float(row.get("close", 0.0))
            if reference <= 0:
                continue
            full_cost = reference * target * (1.0 + config.slippage_bps / 10_000.0)
            full_cost += _cost(config.cost_model, "buy", full_cost)
            shares = target
            if full_cost > projected_cash:
                if config.require_full_target:
                    continue
                shares = floor(projected_cash / (reference * (1.0 + config.slippage_bps / 10_000.0)))
                if shares <= 0:
                    continue
                full_cost = reference * shares * (1.0 + config.slippage_bps / 10_000.0)
                full_cost += _cost(config.cost_model, "buy", full_cost)
            queue({"decision_date": today, "symbol": symbol, "side": "buy", "shares": int(shares), "reason": "rank_entry"})
            projected_cash -= full_cost

    orders_frame = _frame(orders)
    rejection_frame = _frame(rejections)
    daily = pl.DataFrame(equity_rows, schema={"date": pl.Date, "equity": pl.Float64, "cash": pl.Float64, "holdings_value": pl.Float64})
    positions_frame = pl.DataFrame(position_rows, schema={"date": pl.Date, "symbol": pl.String, "shares": pl.Int64, "close": pl.Float64})
    return {
        "daily_equity": daily, "orders": orders_frame, "positions": positions_frame,
        "rejections": rejection_frame, "open_positions": sorted(positions),
        "trade_records": portfolio_trade_records(orders_frame),
        "cost_drag": float(orders_frame["cost"].sum()) if orders_frame.height else 0.0,
        "slippage_drag": float(orders_frame["slippage_cost"].sum()) if orders_frame.height else 0.0,
        "delisted_pnl_impact": delisted_pnl_impact,
    }

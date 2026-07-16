from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from strategies.mean_reversion.exit_engine import MRExitConfig, evaluate_mr_exit
from strategies.mean_reversion.indicators import adx, atr, connors_rsi, cumulative_rsi, ibs, rsi, sma


@dataclass(slots=True)
class MRPendingSignal:
    symbol: str
    signal_date: date
    close_at_signal: float
    stop: float
    expiration_date: date
    rsi2: float
    atr14: float


@dataclass(slots=True)
class MROpenPosition:
    symbol: str
    signal_date: date
    entry_date: date
    entry_price: float
    stop: float
    active_stop: float
    shares: float
    initial_risk: float
    cost_basis: float
    mae_price: float
    mfe_price: float
    rsi2_at_entry: float
    atr14: float


@dataclass(slots=True)
class MRDirectionalPendingSignal:
    symbol: str
    direction: str
    signal_date: date
    close_at_signal: float
    stop: float
    expiration_date: date
    rsi2: float
    atr14: float


@dataclass(slots=True)
class MRDirectionalOpenPosition:
    symbol: str
    direction: str
    signal_date: date
    entry_date: date
    entry_price: float
    stop: float
    active_stop: float
    shares: float
    initial_risk: float
    cost_basis: float
    mae_price: float
    mfe_price: float
    rsi2_at_entry: float
    atr14: float


TRADE_SCHEMA: dict[str, Any] = {
    "symbol": pl.String,
    "signal_date": pl.Date,
    "entry_date": pl.Date,
    "exit_date": pl.Date,
    "entry_price": pl.Float64,
    "exit_price": pl.Float64,
    "return_pct": pl.Float64,
    "r_multiple": pl.Float64,
    "holding_days": pl.Int64,
    "status": pl.String,
    "exit_reason": pl.String,
    "mae_price": pl.Float64,
    "mfe_price": pl.Float64,
    "mae_r": pl.Float64,
    "mfe_r": pl.Float64,
    "rsi2_at_signal": pl.Float64,
    "atr14": pl.Float64,
    "sector": pl.String,
    "shares": pl.Float64,
    "pnl": pl.Float64,
}


DIRECTIONAL_TRADE_SCHEMA: dict[str, Any] = {
    **TRADE_SCHEMA,
    "direction": pl.String,
}


DAILY_SCHEMA: dict[str, Any] = {
    "date": pl.Date,
    "equity": pl.Float64,
    "cash": pl.Float64,
    "invested": pl.Float64,
    "positions": pl.Int64,
    "drawdown_pct": pl.Float64,
}


def build_mr_symbol_data(ohlcv: pl.DataFrame) -> tuple[dict[str, dict[str, Any]], list[date]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    if ohlcv.is_empty():
        return by_symbol, []

    unique_dates = sorted(set(ohlcv["date"].to_list()))
    for group in ohlcv.sort(["symbol", "date"]).partition_by("symbol", maintain_order=True):
        symbol = str(group["symbol"][0])
        dates = group["date"].to_list()
        opens = [float(value) for value in group["open"].to_list()]
        highs = [float(value) for value in group["high"].to_list()]
        lows = [float(value) for value in group["low"].to_list()]
        closes = [float(value) for value in group["close"].to_list()]
        volumes = [float(value) for value in group["volume"].to_list()]
        by_symbol[symbol] = {
            "dates": dates,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes,
            "date_to_idx": {d: idx for idx, d in enumerate(dates)},
            "sma3": sma(closes, 3),
            "sma5": sma(closes, 5),
            "sma200": sma(closes, 200),
            "rsi2": rsi(closes, 2),
            "crsi": connors_rsi(closes),
            "cum_rsi": cumulative_rsi(closes, 2, 2),
            "ibs_values": ibs(highs, lows, closes),
            "adx14": adx(highs, lows, closes, 14),
            "atr14": atr(highs, lows, closes, 14),
        }
    benchmark_symbol = next((symbol for symbol in ("NIFTY 50", "^NSEI", "NIFTYBEES") if symbol in by_symbol), None)
    if benchmark_symbol is not None:
        benchmark_data = by_symbol[benchmark_symbol]
        benchmark_sma200 = sma(benchmark_data["closes"], 200)
        market_above_200sma = {
            benchmark_data["dates"][idx]: benchmark_data["closes"][idx] > benchmark_sma200[idx]
            for idx in range(len(benchmark_data["dates"]))
            if not math.isnan(benchmark_sma200[idx])
        }
    else:
        market_above_200sma = {value: True for value in unique_dates}
    by_symbol["_market_regime"] = {
        "benchmark_symbol": benchmark_symbol,
        "market_above_200sma": market_above_200sma,
    }
    return by_symbol, unique_dates


def build_benchmark_regime(ohlcv: pl.DataFrame) -> dict[date, bool]:
    """Return benchmark trend regime by date.

    Dates with a complete benchmark SMA200 are True when the benchmark close is
    above its SMA200 and False otherwise. If no benchmark is present, every
    available OHLCV date is treated as bullish.
    """
    benchmark_symbol = None
    symbols = set(ohlcv["symbol"].unique().to_list()) if not ohlcv.is_empty() else set()
    for candidate in ("NIFTY 50", "^NSEI", "NIFTYBEES"):
        if candidate in symbols:
            benchmark_symbol = candidate
            break
    if benchmark_symbol is None:
        unique_dates = sorted(set(ohlcv["date"].to_list()))
        return {d: True for d in unique_dates}

    bench = ohlcv.filter(pl.col("symbol") == benchmark_symbol).sort("date")
    dates_list = bench["date"].to_list()
    close_list = [float(value) for value in bench["close"].to_list()]
    sma200 = sma(close_list, 200)
    result: dict[date, bool] = {}
    for idx, d in enumerate(dates_list):
        if not math.isnan(sma200[idx]):
            result[d] = close_list[idx] > sma200[idx]
    return result


def simulate_mr_portfolio(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    max_positions: int = 10,
    max_sector_positions: int = 3,
    initial_capital: float = 1_000_000.0,
    risk_per_trade_pct: float = 0.02,
    exit_config: MRExitConfig = MRExitConfig(),
    sector_map: dict[str, str] | None = None,
    round_trip_cost_pct: float = 0.0,
    cooldown_days: int = 0,
    sector_weights: dict[str, float] | None = None,
    exit_execution: str = "same_close",
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Run a simple next-day-entry Connors RSI2 mean reversion simulation."""
    if exit_execution not in {"same_close", "next_open"}:
        raise ValueError("exit_execution must be 'same_close' or 'next_open'")
    sector_map = sector_map or {}
    sector_weights = sector_weights or {}
    symbol_data, unique_dates = build_mr_symbol_data(ohlcv)
    if start_date is not None:
        unique_dates = [value for value in unique_dates if value >= start_date]
    if end_date is not None:
        unique_dates = [value for value in unique_dates if value <= end_date]

    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals.sort(["signal_date", "rsi2", "symbol"]).to_dicts():
        signals_by_date[signal["signal_date"]].append(signal)

    cash = float(initial_capital)
    equity_peak = float(initial_capital)
    open_positions: list[MROpenPosition] = []
    pending_signals: list[MRPendingSignal] = []
    closed_trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    filled_signals = 0
    expired_signals = 0
    skipped_signals = 0
    last_exit_date: dict[str, date] = {}
    deferred_exits: dict[str, str] = {}
    entry_slippage = 1.0 + (slippage_bps / 10_000.0)
    exit_slippage = 1.0 - (slippage_bps / 10_000.0)

    def position_equity(today: date) -> tuple[float, float]:
        invested = 0.0
        mark_to_market = 0.0
        for pos in open_positions:
            data = symbol_data.get(pos.symbol)
            idx = data["date_to_idx"].get(today) if data else None
            if idx is None:
                mark = pos.entry_price
            else:
                mark = float(data["closes"][idx])
            invested += pos.cost_basis
            mark_to_market += pos.shares * mark
        return invested, cash + mark_to_market

    def close_position(pos: MROpenPosition, exit_date: date, exit_price: float, reason: str) -> dict[str, Any]:
        pnl = (exit_price - pos.entry_price) * pos.shares
        holding_days = (exit_date - pos.entry_date).days
        r_multiple = (exit_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
        mae_r = (pos.mae_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
        mfe_r = (pos.mfe_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
        return {
            "symbol": pos.symbol,
            "signal_date": pos.signal_date,
            "entry_date": pos.entry_date,
            "exit_date": exit_date,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "return_pct": ((exit_price / pos.entry_price) - 1.0) * 100.0,
            "r_multiple": r_multiple,
            "holding_days": holding_days,
            "status": "stopped" if reason == "stopped" else "closed",
            "exit_reason": reason,
            "mae_price": pos.mae_price,
            "mfe_price": pos.mfe_price,
            "mae_r": mae_r,
            "mfe_r": mfe_r,
            "rsi2_at_signal": pos.rsi2_at_entry,
            "atr14": pos.atr14,
            "sector": sector_map.get(pos.symbol, "UNKNOWN"),
            "shares": pos.shares,
            "pnl": pnl,
        }

    for day_idx, today in enumerate(unique_dates):
        idx = 0
        while idx < len(open_positions):
            pos = open_positions[idx]
            data = symbol_data.get(pos.symbol)
            bar_idx = data["date_to_idx"].get(today) if data else None
            if bar_idx is None:
                idx += 1
                continue

            open_price = float(data["opens"][bar_idx])
            high_price = float(data["highs"][bar_idx])
            low_price = float(data["lows"][bar_idx])
            close_price = float(data["closes"][bar_idx])
            pos.mae_price = min(pos.mae_price, low_price)
            pos.mfe_price = max(pos.mfe_price, high_price)

            deferred_reason = deferred_exits.pop(pos.symbol, None)
            if deferred_reason is not None:
                exit_price = open_price * exit_slippage
                proceeds = pos.shares * exit_price
                exit_cost = proceeds * (round_trip_cost_pct / 2.0)
                cash += proceeds - exit_cost
                closed_trades.append(close_position(pos, today, exit_price, deferred_reason))
                last_exit_date[pos.symbol] = today
                open_positions.pop(idx)
                continue

            action = evaluate_mr_exit(
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                active_stop=pos.active_stop,
                today=today,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                sma5=float(data["sma5"][bar_idx]),
                sma3=float(data["sma3"][bar_idx]),
                rsi2=float(data["rsi2"][bar_idx]),
                cum_rsi2=float(data["cum_rsi"][bar_idx]),
                config=exit_config,
            )
            if action.action == "SELL_ALL":
                stop_exit = action.reason == "stopped"
                if not stop_exit and exit_execution == "next_open":
                    deferred_exits[pos.symbol] = f"{action.reason}_next_open"
                    idx += 1
                    continue
                exit_price = open_price if open_price <= pos.active_stop else pos.active_stop if low_price <= pos.active_stop else close_price
                exit_price *= exit_slippage
                proceeds = pos.shares * exit_price
                exit_cost = proceeds * (round_trip_cost_pct / 2.0)
                cash += proceeds - exit_cost
                closed_trades.append(close_position(pos, today, exit_price, action.reason))
                last_exit_date[pos.symbol] = today
                open_positions.pop(idx)
                continue
            idx += 1

        next_pending: list[MRPendingSignal] = []
        for pending in pending_signals:
            if today > pending.expiration_date:
                expired_signals += 1
                continue

            data = symbol_data.get(pending.symbol)
            bar_idx = data["date_to_idx"].get(today) if data else None
            if bar_idx is None or today <= pending.signal_date:
                next_pending.append(pending)
                continue

            open_price = float(data["opens"][bar_idx]) * entry_slippage
            if open_price <= pending.stop:
                skipped_signals += 1
                continue
            last_exit = last_exit_date.get(pending.symbol)
            if last_exit is not None and (today - last_exit).days < cooldown_days:
                skipped_signals += 1
                continue
            if any(pos.symbol == pending.symbol for pos in open_positions):
                skipped_signals += 1
                continue
            if len(open_positions) >= max_positions:
                next_pending.append(pending)
                continue

            sector = sector_map.get(pending.symbol, "UNKNOWN")
            if sector != "UNKNOWN":
                sector_positions = sum(1 for pos in open_positions if sector_map.get(pos.symbol, "UNKNOWN") == sector)
                if sector_positions >= max_sector_positions:
                    next_pending.append(pending)
                    continue

            invested, equity = position_equity(today)
            _ = invested
            risk_per_share = open_price - pending.stop
            if risk_per_share <= 0.0:
                skipped_signals += 1
                continue
            sector_weight = sector_weights.get(sector, 1.0)
            shares = ((equity * risk_per_trade_pct) / risk_per_share) * sector_weight
            cost_basis = shares * open_price
            entry_cost_rate = round_trip_cost_pct / 2.0
            max_affordable_cost_basis = cash / (1.0 + entry_cost_rate) if entry_cost_rate > -1.0 else cash
            if cost_basis > max_affordable_cost_basis and open_price > 0:
                shares = max_affordable_cost_basis / open_price
                cost_basis = shares * open_price
            if shares <= 0.0 or cost_basis <= 0.0:
                skipped_signals += 1
                continue

            entry_cost = cost_basis * entry_cost_rate
            cash -= cost_basis + entry_cost
            filled_signals += 1
            open_positions.append(
                MROpenPosition(
                    symbol=pending.symbol,
                    signal_date=pending.signal_date,
                    entry_date=today,
                    entry_price=open_price,
                    stop=pending.stop,
                    active_stop=pending.stop,
                    shares=shares,
                    initial_risk=risk_per_share,
                    cost_basis=cost_basis,
                    mae_price=open_price,
                    mfe_price=open_price,
                    rsi2_at_entry=pending.rsi2,
                    atr14=pending.atr14,
                )
            )

        pending_signals = next_pending

        for signal in signals_by_date.get(today, []):
            expiration_date = unique_dates[day_idx + 1] if day_idx + 1 < len(unique_dates) else today
            pending_signals.append(
                MRPendingSignal(
                    symbol=str(signal["symbol"]),
                    signal_date=signal["signal_date"],
                    close_at_signal=float(signal["close"]),
                    stop=float(signal["stop"]),
                    expiration_date=expiration_date,
                    rsi2=float(signal["rsi2"]),
                    atr14=float(signal["atr14"]),
                )
            )

        invested, equity = position_equity(today)
        equity_peak = max(equity_peak, equity)
        daily_rows.append(
            {
                "date": today,
                "equity": equity,
                "cash": cash,
                "invested": invested,
                "positions": len(open_positions),
                "drawdown_pct": ((equity / equity_peak) - 1.0) * 100.0 if equity_peak > 0 else 0.0,
            }
        )

    if open_positions and unique_dates:
        last_day = unique_dates[-1]
        for pos in open_positions:
            data = symbol_data.get(pos.symbol)
            bar_idx = data["date_to_idx"].get(last_day) if data else None
            exit_price = float(data["closes"][bar_idx]) if data and bar_idx is not None else pos.entry_price
            exit_price *= exit_slippage
            proceeds = pos.shares * exit_price
            exit_cost = proceeds * (round_trip_cost_pct / 2.0)
            cash += proceeds - exit_cost
            closed_trades.append(close_position(pos, last_day, exit_price, "end_of_data"))

    return {
        "final_equity": float(daily_rows[-1]["equity"]) if daily_rows else float(initial_capital),
        "daily_equity": pl.DataFrame(daily_rows, schema=DAILY_SCHEMA) if daily_rows else pl.DataFrame(schema=DAILY_SCHEMA),
        "trades": pl.DataFrame(closed_trades, schema=TRADE_SCHEMA) if closed_trades else pl.DataFrame(schema=TRADE_SCHEMA),
        "total_signals": int(signals.height),
        "filled_signals": filled_signals,
        "expired_signals": expired_signals,
        "skipped_signals": skipped_signals,
    }


def simulate_mr_long_short_portfolio(
    long_signals: pl.DataFrame,
    short_signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    max_positions: int = 5,
    max_sector_positions: int = 3,
    initial_capital: float = 500_000.0,
    risk_per_trade_pct: float = 0.04,
    exit_config: MRExitConfig = MRExitConfig(exit_mode="sma3", stop_atr_mult=3.5),
    sector_map: dict[str, str] | None = None,
    round_trip_cost_pct: float = 0.002,
) -> dict[str, Any]:
    """Run shared-capital long+short mean-reversion simulation.

    Short positions reserve full notional as collateral. This is conservative for
    futures/margin research and avoids accidentally manufacturing leverage.
    """
    sector_map = sector_map or {}
    symbol_data, unique_dates = build_mr_symbol_data(ohlcv)
    if start_date is not None:
        unique_dates = [value for value in unique_dates if value >= start_date]
    if end_date is not None:
        unique_dates = [value for value in unique_dates if value <= end_date]

    long_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    if not long_signals.is_empty():
        for signal in long_signals.sort(["signal_date", "rsi2", "symbol"]).to_dicts():
            long_by_date[signal["signal_date"]].append(signal)

    short_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    if not short_signals.is_empty():
        for signal in short_signals.sort(["signal_date", "rsi2", "symbol"], descending=[False, True, False]).to_dicts():
            short_by_date[signal["signal_date"]].append(signal)

    cash = float(initial_capital)
    equity_peak = float(initial_capital)
    open_positions: list[MRDirectionalOpenPosition] = []
    pending_signals: list[MRDirectionalPendingSignal] = []
    closed_trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    filled_long_signals = 0
    filled_short_signals = 0
    expired_signals = 0
    skipped_signals = 0

    def position_equity(today: date) -> tuple[float, float]:
        invested = 0.0
        mark_to_market = 0.0
        for pos in open_positions:
            data = symbol_data.get(pos.symbol)
            idx = data["date_to_idx"].get(today) if data else None
            mark = float(data["closes"][idx]) if idx is not None else pos.entry_price
            invested += pos.cost_basis
            if pos.direction == "long":
                mark_to_market += pos.shares * mark
            else:
                mark_to_market += pos.cost_basis + ((pos.entry_price - mark) * pos.shares)
        return invested, cash + mark_to_market

    def close_position(pos: MRDirectionalOpenPosition, exit_date: date, exit_price: float, reason: str) -> dict[str, Any]:
        if pos.direction == "long":
            pnl = (exit_price - pos.entry_price) * pos.shares
            r_multiple = (exit_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
            return_pct = ((exit_price / pos.entry_price) - 1.0) * 100.0 if pos.entry_price > 0 else 0.0
            mae_r = (pos.mae_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
            mfe_r = (pos.mfe_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
        else:
            pnl = (pos.entry_price - exit_price) * pos.shares
            r_multiple = (pos.entry_price - exit_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
            return_pct = ((pos.entry_price / exit_price) - 1.0) * 100.0 if exit_price > 0 else 0.0
            mae_r = (pos.entry_price - pos.mae_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0
            mfe_r = (pos.entry_price - pos.mfe_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0

        return {
            "symbol": pos.symbol,
            "direction": pos.direction,
            "signal_date": pos.signal_date,
            "entry_date": pos.entry_date,
            "exit_date": exit_date,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "return_pct": return_pct,
            "r_multiple": r_multiple,
            "holding_days": (exit_date - pos.entry_date).days,
            "status": "stopped" if reason == "stopped" else "closed",
            "exit_reason": reason,
            "mae_price": pos.mae_price,
            "mfe_price": pos.mfe_price,
            "mae_r": mae_r,
            "mfe_r": mfe_r,
            "rsi2_at_signal": pos.rsi2_at_entry,
            "atr14": pos.atr14,
            "sector": sector_map.get(pos.symbol, "UNKNOWN"),
            "shares": pos.shares,
            "pnl": pnl,
        }

    def exit_action(pos: MRDirectionalOpenPosition, today: date, bar_idx: int, data: dict[str, Any]) -> tuple[bool, float, str]:
        open_price = float(data["opens"][bar_idx])
        high_price = float(data["highs"][bar_idx])
        low_price = float(data["lows"][bar_idx])
        close_price = float(data["closes"][bar_idx])
        sma3_value = float(data["sma3"][bar_idx])
        sma5_value = float(data["sma5"][bar_idx])
        holding_days = (today - pos.entry_date).days

        if pos.direction == "long":
            pos.mae_price = min(pos.mae_price, low_price)
            pos.mfe_price = max(pos.mfe_price, high_price)
            action = evaluate_mr_exit(
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                active_stop=pos.active_stop,
                today=today,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                sma5=sma5_value,
                sma3=sma3_value,
                rsi2=float(data["rsi2"][bar_idx]),
                cum_rsi2=float(data["cum_rsi"][bar_idx]),
                config=exit_config,
            )
            if action.action != "SELL_ALL":
                return False, close_price, ""
            exit_price = open_price if open_price <= pos.active_stop else pos.active_stop if low_price <= pos.active_stop else close_price
            return True, exit_price, action.reason

        pos.mae_price = max(pos.mae_price, high_price)
        pos.mfe_price = min(pos.mfe_price, low_price)
        if high_price >= pos.active_stop:
            exit_price = open_price if open_price >= pos.active_stop else pos.active_stop
            return True, exit_price, "stopped"
        if exit_config.exit_mode == "sma5" and not math.isnan(sma5_value) and close_price < sma5_value:
            return True, close_price, "mean_revert_sma5"
        if exit_config.exit_mode == "sma3" and not math.isnan(sma3_value) and close_price < sma3_value:
            return True, close_price, "mean_revert_sma3"
        if holding_days >= exit_config.max_hold_days:
            return True, close_price, "max_hold"
        return False, close_price, ""

    for day_idx, today in enumerate(unique_dates):
        idx = 0
        while idx < len(open_positions):
            pos = open_positions[idx]
            data = symbol_data.get(pos.symbol)
            bar_idx = data["date_to_idx"].get(today) if data else None
            if bar_idx is None:
                idx += 1
                continue
            should_exit, exit_price, reason = exit_action(pos, today, bar_idx, data)
            if should_exit:
                if pos.direction == "long":
                    proceeds = pos.shares * exit_price
                    exit_cost = proceeds * (round_trip_cost_pct / 2.0)
                    cash += proceeds - exit_cost
                else:
                    exit_notional = pos.shares * exit_price
                    pnl = (pos.entry_price - exit_price) * pos.shares
                    exit_cost = exit_notional * (round_trip_cost_pct / 2.0)
                    cash += pos.cost_basis + pnl - exit_cost
                closed_trades.append(close_position(pos, today, exit_price, reason))
                open_positions.pop(idx)
                continue
            idx += 1

        next_pending: list[MRDirectionalPendingSignal] = []
        for pending in pending_signals:
            if today > pending.expiration_date:
                expired_signals += 1
                continue
            data = symbol_data.get(pending.symbol)
            bar_idx = data["date_to_idx"].get(today) if data else None
            if bar_idx is None or today <= pending.signal_date:
                next_pending.append(pending)
                continue

            open_price = float(data["opens"][bar_idx])
            if pending.direction == "long" and open_price <= pending.stop:
                skipped_signals += 1
                continue
            if pending.direction == "short" and open_price >= pending.stop:
                skipped_signals += 1
                continue
            if any(pos.symbol == pending.symbol for pos in open_positions):
                skipped_signals += 1
                continue
            if len(open_positions) >= max_positions:
                next_pending.append(pending)
                continue

            sector = sector_map.get(pending.symbol, "UNKNOWN")
            if sector != "UNKNOWN":
                sector_positions = sum(1 for pos in open_positions if sector_map.get(pos.symbol, "UNKNOWN") == sector)
                if sector_positions >= max_sector_positions:
                    next_pending.append(pending)
                    continue

            _, equity = position_equity(today)
            risk_per_share = abs(open_price - pending.stop)
            if risk_per_share <= 0.0:
                skipped_signals += 1
                continue
            shares = (equity * risk_per_trade_pct) / risk_per_share
            cost_basis = shares * open_price
            entry_cost = cost_basis * (round_trip_cost_pct / 2.0)
            if cost_basis + entry_cost > cash and open_price > 0:
                shares = cash / (open_price * (1.0 + (round_trip_cost_pct / 2.0)))
                cost_basis = shares * open_price
                entry_cost = cost_basis * (round_trip_cost_pct / 2.0)
            if shares <= 0.0 or cost_basis <= 0.0:
                skipped_signals += 1
                continue

            cash -= cost_basis + entry_cost
            if pending.direction == "long":
                filled_long_signals += 1
            else:
                filled_short_signals += 1
            open_positions.append(
                MRDirectionalOpenPosition(
                    symbol=pending.symbol,
                    direction=pending.direction,
                    signal_date=pending.signal_date,
                    entry_date=today,
                    entry_price=open_price,
                    stop=pending.stop,
                    active_stop=pending.stop,
                    shares=shares,
                    initial_risk=risk_per_share,
                    cost_basis=cost_basis,
                    mae_price=open_price,
                    mfe_price=open_price,
                    rsi2_at_entry=pending.rsi2,
                    atr14=pending.atr14,
                )
            )

        pending_signals = next_pending

        expiration_date = unique_dates[day_idx + 1] if day_idx + 1 < len(unique_dates) else today
        for signal in long_by_date.get(today, []):
            pending_signals.append(
                MRDirectionalPendingSignal(
                    symbol=str(signal["symbol"]),
                    direction="long",
                    signal_date=signal["signal_date"],
                    close_at_signal=float(signal["close"]),
                    stop=float(signal["stop"]),
                    expiration_date=expiration_date,
                    rsi2=float(signal["rsi2"]),
                    atr14=float(signal["atr14"]),
                )
            )
        for signal in short_by_date.get(today, []):
            pending_signals.append(
                MRDirectionalPendingSignal(
                    symbol=str(signal["symbol"]),
                    direction="short",
                    signal_date=signal["signal_date"],
                    close_at_signal=float(signal["close"]),
                    stop=float(signal["stop"]),
                    expiration_date=expiration_date,
                    rsi2=float(signal["rsi2"]),
                    atr14=float(signal["atr14"]),
                )
            )

        invested, equity = position_equity(today)
        equity_peak = max(equity_peak, equity)
        daily_rows.append(
            {
                "date": today,
                "equity": equity,
                "cash": cash,
                "invested": invested,
                "positions": len(open_positions),
                "drawdown_pct": ((equity / equity_peak) - 1.0) * 100.0 if equity_peak > 0 else 0.0,
            }
        )

    if open_positions and unique_dates:
        last_day = unique_dates[-1]
        for pos in open_positions:
            data = symbol_data.get(pos.symbol)
            bar_idx = data["date_to_idx"].get(last_day) if data else None
            exit_price = float(data["closes"][bar_idx]) if data and bar_idx is not None else pos.entry_price
            if pos.direction == "long":
                proceeds = pos.shares * exit_price
                exit_cost = proceeds * (round_trip_cost_pct / 2.0)
                cash += proceeds - exit_cost
            else:
                exit_notional = pos.shares * exit_price
                pnl = (pos.entry_price - exit_price) * pos.shares
                exit_cost = exit_notional * (round_trip_cost_pct / 2.0)
                cash += pos.cost_basis + pnl - exit_cost
            closed_trades.append(close_position(pos, last_day, exit_price, "end_of_data"))

    return {
        "final_equity": float(daily_rows[-1]["equity"]) if daily_rows else float(initial_capital),
        "daily_equity": pl.DataFrame(daily_rows, schema=DAILY_SCHEMA) if daily_rows else pl.DataFrame(schema=DAILY_SCHEMA),
        "trades": pl.DataFrame(closed_trades, schema=DIRECTIONAL_TRADE_SCHEMA) if closed_trades else pl.DataFrame(schema=DIRECTIONAL_TRADE_SCHEMA),
        "long_signals": int(long_signals.height),
        "short_signals": int(short_signals.height),
        "filled_long_signals": filled_long_signals,
        "filled_short_signals": filled_short_signals,
        "filled_signals": filled_long_signals + filled_short_signals,
        "expired_signals": expired_signals,
        "skipped_signals": skipped_signals,
    }

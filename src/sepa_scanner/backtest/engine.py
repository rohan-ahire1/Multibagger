from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import polars as pl

from sepa_scanner.backtest.models import BacktestParams, BacktestResult, BacktestTrade, ExitMode

_MIN_SYMBOLS_FOR_PARALLEL = 8
_EMA21_ALPHA = 2.0 / (21 + 1)


def _risk_per_share(entry_price: float, stop: float) -> float | None:
    risk = entry_price - stop
    return risk if risk > 0 else None


def _update_excursions(
    mae_price: float,
    mfe_price: float,
    *prices: float,
) -> tuple[float, float]:
    for price in prices:
        if price < mae_price:
            mae_price = price
        if price > mfe_price:
            mfe_price = price
    return mae_price, mfe_price


def _close_trade(
    trade: BacktestTrade,
    *,
    status: str,
    exit_date: date,
    exit_price: float,
    holding_days: int,
    mae_price: float | None = None,
    mfe_price: float | None = None,
) -> BacktestTrade:
    if trade.entry_price is None:
        return trade
    risk = _risk_per_share(trade.entry_price, trade.stop)
    return_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100.0
    r_multiple = ((exit_price - trade.entry_price) / risk) if risk else None

    mae_r: float | None = None
    mfe_r: float | None = None
    giveback_pct: float | None = None
    if risk:
        if mae_price is not None:
            mae_r = (trade.entry_price - mae_price) / risk
        if mfe_price is not None:
            mfe_r = (mfe_price - trade.entry_price) / risk
        if mfe_r is not None and r_multiple is not None and mfe_r > 1e-9:
            giveback_pct = (mfe_r - r_multiple) / mfe_r

    return BacktestTrade(
        symbol=trade.symbol,
        signal_date=trade.signal_date,
        status=status,
        entry_ref=trade.entry_ref,
        stop=trade.stop,
        pivot=trade.pivot,
        entry_date=trade.entry_date,
        entry_price=trade.entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        holding_days=holding_days,
        return_pct=return_pct,
        r_multiple=r_multiple,
        mae_price=mae_price,
        mfe_price=mfe_price,
        mae_r=mae_r,
        mfe_r=mfe_r,
        giveback_pct=giveback_pct,
    )


def _empty_trade(signal: dict[str, object], status: str) -> BacktestTrade:
    return BacktestTrade(
        symbol=str(signal["symbol"]),
        signal_date=signal["signal_date"],
        status=status,
        entry_ref=float(signal["entry_ref"]),
        stop=float(signal["stop"]),
        pivot=float(signal["pivot"]) if signal.get("pivot") is not None else None,
    )


def _simulate_signal(
    signal: dict[str, object],
    symbol_prices: list[dict[str, object]],
    params: BacktestParams,
) -> BacktestTrade:
    future_bars = [row for row in symbol_prices if row["date"] > signal["signal_date"]]
    if not future_bars:
        return _empty_trade(signal, "incomplete_data")

    entry_ref = float(signal["entry_ref"])
    initial_stop = float(signal["stop"])
    pending_bars = future_bars[: params.pending_days]
    if len(pending_bars) < params.pending_days:
        pending_status = "incomplete_data"
    else:
        pending_status = "not_filled"

    entry_index: int | None = None
    entry_price: float | None = None
    entry_date: date | None = None
    for idx, bar in enumerate(pending_bars):
        if float(bar["open"]) >= entry_ref:
            entry_index = idx
            entry_price = float(bar["open"])
            entry_date = bar["date"]
            break
        if float(bar["high"]) >= entry_ref:
            entry_index = idx
            entry_price = entry_ref
            entry_date = bar["date"]
            break

    if entry_index is None or entry_price is None or entry_date is None:
        return _empty_trade(signal, pending_status)

    trade = BacktestTrade(
        symbol=str(signal["symbol"]),
        signal_date=signal["signal_date"],
        status="open",
        entry_ref=entry_ref,
        stop=initial_stop,
        pivot=float(signal["pivot"]) if signal.get("pivot") is not None else None,
        entry_date=entry_date,
        entry_price=entry_price,
    )

    risk = _risk_per_share(entry_price, initial_stop)
    target_price = entry_price + (risk * params.target_r) if (risk and params.target_r is not None) else None
    exit_bars = future_bars[entry_index : entry_index + params.max_hold_days]
    incomplete_after_entry = len(exit_bars) < params.max_hold_days

    active_stop = initial_stop
    trail_activated = False
    low_buffer: list[float] = []
    ema21: float | None = None
    mae_price = entry_price
    mfe_price = entry_price

    for offset, bar in enumerate(exit_bars):
        open_price = float(bar["open"])
        high_price = float(bar["high"])
        low_price = float(bar["low"])
        close_price = float(bar["close"])
        holding_days = offset + 1
        is_entry_day = offset == 0

        if not is_entry_day and open_price <= active_stop:
            mae_price, mfe_price = _update_excursions(mae_price, mfe_price, open_price)
            return _close_trade(
                trade, status="stopped", exit_date=bar["date"],
                exit_price=open_price, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )
        if target_price is not None and not is_entry_day and open_price >= target_price:
            mae_price, mfe_price = _update_excursions(mae_price, mfe_price, open_price)
            return _close_trade(
                trade, status="target", exit_date=bar["date"],
                exit_price=open_price, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )

        mae_price, mfe_price = _update_excursions(mae_price, mfe_price, low_price, high_price)

        hit_stop = low_price <= active_stop
        hit_target = target_price is not None and high_price >= target_price
        if hit_stop and hit_target:
            return _close_trade(
                trade, status="stopped", exit_date=bar["date"],
                exit_price=active_stop, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )
        if hit_stop:
            return _close_trade(
                trade, status="stopped", exit_date=bar["date"],
                exit_price=active_stop, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )
        if hit_target and target_price is not None:
            return _close_trade(
                trade, status="target", exit_date=bar["date"],
                exit_price=target_price, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )
        if holding_days == params.max_hold_days:
            return _close_trade(
                trade, status="time_stop", exit_date=bar["date"],
                exit_price=close_price, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )

        # --- Trailing stop update + same-bar trigger check ---
        if params.exit_mode != ExitMode.V1_FIXED and risk:
            prev_stop = active_stop

            if not trail_activated:
                if high_price >= entry_price + risk * params.trail_activation_r:
                    trail_activated = True
                    active_stop = max(active_stop, entry_price)

            if trail_activated:
                if params.exit_mode == ExitMode.V2_TRAIL_10D:
                    low_buffer.append(low_price)
                    if len(low_buffer) > 10:
                        low_buffer = low_buffer[-10:]
                    active_stop = max(active_stop, min(low_buffer))
                elif params.exit_mode == ExitMode.V2_TRAIL_21EMA:
                    if ema21 is None:
                        ema21 = close_price
                    else:
                        ema21 = _EMA21_ALPHA * close_price + (1 - _EMA21_ALPHA) * ema21
                    active_stop = max(active_stop, ema21)
            else:
                if params.exit_mode == ExitMode.V2_TRAIL_10D:
                    low_buffer.append(low_price)
                    if len(low_buffer) > 10:
                        low_buffer = low_buffer[-10:]
                if params.exit_mode == ExitMode.V2_TRAIL_21EMA:
                    if ema21 is None:
                        ema21 = close_price
                    else:
                        ema21 = _EMA21_ALPHA * close_price + (1 - _EMA21_ALPHA) * ema21

            # Trigger check: if the trail ratcheted up above close on this
            # bar, exit immediately instead of waiting for next bar's open.
            if not is_entry_day and active_stop > prev_stop and close_price <= active_stop:
                return _close_trade(
                    trade, status="stopped", exit_date=bar["date"],
                    exit_price=active_stop, holding_days=holding_days,
                    mae_price=mae_price, mfe_price=mfe_price,
                )

    if incomplete_after_entry:
        last_bar = exit_bars[-1]
        return _close_trade(
            trade,
            status="incomplete_data",
            exit_date=last_bar["date"],
            exit_price=float(last_bar["close"]),
            holding_days=len(exit_bars),
            mae_price=mae_price,
            mfe_price=mfe_price,
        )

    return trade


def _simulate_symbol_group(
    symbol_signals: list[dict[str, object]],
    symbol_prices: list[dict[str, object]],
    params: BacktestParams,
) -> list[BacktestTrade]:
    trades: list[BacktestTrade] = []
    active_until_date: date | None = None
    for signal in symbol_signals:
        if active_until_date is not None and signal["signal_date"] < active_until_date:
            trades.append(_empty_trade(signal, "skipped_overlap"))
            continue
        trade = _simulate_signal(signal, symbol_prices, params)
        trades.append(trade)
        if trade.entry_date is not None:
            if trade.exit_date is not None:
                active_until_date = trade.exit_date
            elif symbol_prices:
                active_until_date = symbol_prices[-1]["date"]
    return trades


def simulate_breakout_backtest(
    signals_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    params: BacktestParams,
) -> BacktestResult:
    signals = (
        signals_df.filter(pl.col("signal_type") == "BREAKOUT")
        .sort(["signal_date", "symbol"])
        .to_dicts()
    )
    if not signals:
        return BacktestResult(trades=[])

    ohlcv_rows = (
        ohlcv_df.sort(["symbol", "date"])
        .select("date", "symbol", "open", "high", "low", "close")
        .to_dicts()
    )
    prices_by_symbol: dict[str, list[dict[str, object]]] = {}
    for row in ohlcv_rows:
        prices_by_symbol.setdefault(str(row["symbol"]), []).append(row)

    signals_by_symbol: dict[str, list[dict[str, object]]] = {}
    for signal in signals:
        signals_by_symbol.setdefault(str(signal["symbol"]), []).append(signal)

    all_trades: list[BacktestTrade] = []
    if len(signals_by_symbol) >= _MIN_SYMBOLS_FOR_PARALLEL:
        with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            futures = [
                pool.submit(
                    _simulate_symbol_group,
                    sym_signals,
                    prices_by_symbol.get(sym, []),
                    params,
                )
                for sym, sym_signals in signals_by_symbol.items()
            ]
            for future in futures:
                all_trades.extend(future.result())
    else:
        for sym, sym_signals in signals_by_symbol.items():
            all_trades.extend(
                _simulate_symbol_group(sym_signals, prices_by_symbol.get(sym, []), params)
            )

    all_trades.sort(key=lambda t: (t.signal_date, t.symbol))
    return BacktestResult(trades=all_trades)

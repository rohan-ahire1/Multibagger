from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from typing import Callable, Mapping

import numpy as np
import polars as pl

try:
    import multibagger_core
except ImportError:
    multibagger_core = None

logger = logging.getLogger(__name__)

from sepa_scanner.backtest.exit_engine import (
    BarData,
    ExitAction,
    ExitConfig,
    TradeState,
    evaluate_exit,
)
from sepa_scanner.backtest.models import BacktestParams, BacktestResult, BacktestTrade, ExitMode

_MIN_SYMBOLS_FOR_PARALLEL = 8
_EMA21_ALPHA = 2.0 / (21 + 1)
_EMA50_ALPHA = 2.0 / (50 + 1)


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
    entry_filter: Callable[[date], bool] | None = None,
) -> BacktestTrade:
    if params.exit_mode == ExitMode.MINERVINI_PHASED:
        return _simulate_signal_phased(signal, symbol_prices, params, entry_filter=entry_filter)
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
        if entry_filter is not None and not entry_filter(bar["date"]):
            continue
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
    breakeven_activated = False
    highest_close = entry_price
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

        # --- Dead money check: exit if not at +1R within dead_money_days ---
        # Check both the flag AND whether this bar's high would reach +1R
        reached_1r_this_bar = risk and high_price >= entry_price + risk * 1.0
        if (
            params.dead_money_days is not None
            and not breakeven_activated
            and not reached_1r_this_bar
            and holding_days >= params.dead_money_days
        ):
            return _close_trade(
                trade, status="dead_money", exit_date=bar["date"],
                exit_price=close_price, holding_days=holding_days,
                mae_price=mae_price, mfe_price=mfe_price,
            )

        # --- Trailing stop update + same-bar trigger check ---
        if params.exit_mode != ExitMode.V1_FIXED and risk:
            prev_stop = active_stop

            # Stage 1: breakeven at +1R (hardcoded threshold)
            if not breakeven_activated:
                if high_price >= entry_price + risk * 1.0:
                    breakeven_activated = True
                    active_stop = max(active_stop, entry_price)

            # Stage 2: trail activation at +trail_activation_r
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
                elif params.exit_mode == ExitMode.V3_TRAIL_PCT:
                    highest_close = max(highest_close, close_price)
                    pct_trail_stop = highest_close * (1.0 - params.trail_pct)
                    active_stop = max(active_stop, pct_trail_stop)
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
                if params.exit_mode == ExitMode.V3_TRAIL_PCT:
                    highest_close = max(highest_close, close_price)

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


def _simulate_signal_phased(
    signal: dict[str, object],
    symbol_prices: list[dict[str, object]],
    params: BacktestParams,
    entry_filter: Callable[[date], bool] | None = None,
) -> BacktestTrade:
    """Simulate a signal using the Minervini multi-phase exit engine."""
    future_bars = [row for row in symbol_prices if row["date"] > signal["signal_date"]]
    if not future_bars:
        return _empty_trade(signal, "incomplete_data")

    entry_ref = float(signal["entry_ref"])
    initial_stop = float(signal["stop"])

    # Entry fill logic (same as legacy)
    pending_bars = future_bars[: params.pending_days]
    if len(pending_bars) < params.pending_days:
        pending_status = "incomplete_data"
    else:
        pending_status = "not_filled"

    entry_index: int | None = None
    entry_price: float | None = None
    entry_date: date | None = None
    for idx, bar in enumerate(pending_bars):
        if entry_filter is not None and not entry_filter(bar["date"]):
            continue
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

    risk = _risk_per_share(entry_price, initial_stop)
    if risk is None or risk <= 0:
        return _empty_trade(signal, "not_filled")

    exit_config = ExitConfig()
    rs_rank = float(signal.get("rs_rank", 80.0))
    market_regime = "bull"  # TODO: use actual regime detection

    trade_state = TradeState(
        symbol=str(signal["symbol"]),
        entry_date=entry_date,
        entry_price=entry_price,
        initial_risk=risk,
        initial_stop=initial_stop,
        planned_shares=100,  # normalized to 100 shares for R-multiple calculation
    )

    exit_bars = future_bars[entry_index : entry_index + params.max_hold_days]
    incomplete_after_entry = len(exit_bars) < params.max_hold_days

    # Rolling indicator state
    close_buffer: list[float] = []
    low_buffer: list[float] = []
    volume_buffer: list[float] = []
    range_buffer: list[float] = []
    ema21: float | None = None
    ema50: float | None = None
    mae_price = entry_price
    mfe_price = entry_price

    # Seed with pre-entry bars for indicators
    pre_entry_bars = [row for row in symbol_prices if row["date"] <= entry_date]
    for pb in pre_entry_bars[-200:]:  # use up to 200 bars for SMA200
        c = float(pb["close"])
        close_buffer.append(c)
        low_buffer.append(float(pb["low"]))
        volume_buffer.append(float(pb.get("volume", 0)))
        h, l = float(pb["high"]), float(pb["low"])
        range_buffer.append((h - l) / l if l > 0 else 0)
        if ema21 is None:
            ema21 = c
        else:
            ema21 = _EMA21_ALPHA * c + (1 - _EMA21_ALPHA) * ema21
        if ema50 is None:
            ema50 = c
        else:
            ema50 = _EMA50_ALPHA * c + (1 - _EMA50_ALPHA) * ema50

    prev_close = close_buffer[-1] if close_buffer else entry_price
    final_exit_price: float | None = None
    final_exit_date: date | None = None
    final_status = "open"
    final_holding_days = 0

    for offset, bar in enumerate(exit_bars):
        open_price = float(bar["open"])
        high_price = float(bar["high"])
        low_price = float(bar["low"])
        close_price = float(bar["close"])
        volume = int(bar.get("volume", 0))
        holding_days = offset + 1
        is_entry_day = offset == 0

        # Update excursions
        mae_price = min(mae_price, low_price)
        mfe_price = max(mfe_price, high_price)

        # Gap-down through stop: exit at open
        if not is_entry_day and open_price <= trade_state.active_stop:
            final_exit_price = open_price
            final_exit_date = bar["date"]
            final_status = "stopped"
            final_holding_days = holding_days
            trade_state.exit_reason = trade_state.phase.value + "_gap_stop"
            break

        # Update indicators
        close_buffer.append(close_price)
        low_buffer.append(low_price)
        volume_buffer.append(float(volume))
        h, l = high_price, low_price
        range_buffer.append((h - l) / l if l > 0 else 0)

        if ema21 is None:
            ema21 = close_price
        else:
            ema21 = _EMA21_ALPHA * close_price + (1 - _EMA21_ALPHA) * ema21
        if ema50 is None:
            ema50 = close_price
        else:
            ema50 = _EMA50_ALPHA * close_price + (1 - _EMA50_ALPHA) * ema50

        sma_50 = sum(close_buffer[-50:]) / min(len(close_buffer), 50) if close_buffer else 0
        sma_200 = sum(close_buffer[-200:]) / min(len(close_buffer), 200) if close_buffer else 0
        low_10d = min(low_buffer[-10:]) if low_buffer else low_price
        avg_range_20 = sum(range_buffer[-20:]) / min(len(range_buffer), 20) if range_buffer else 0
        avg_volume_50 = sum(volume_buffer[-50:]) / min(len(volume_buffer), 50) if volume_buffer else 1

        bar_data = BarData(
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            sma_50=sma_50,
            sma_200=sma_200,
            ema_21=ema21 or 0,
            ema_50=ema50 or 0,
            low_10d=low_10d,
            prev_close=prev_close,
            avg_range_20=avg_range_20,
            avg_volume_50=avg_volume_50,
        )

        actions = evaluate_exit(
            trade_state,
            bar=bar_data,
            config=exit_config,
            market_regime=market_regime,
            rs_rank=rs_rank,
        )

        for action in actions:
            if action.action == "SELL_ALL":
                final_exit_price = close_price
                final_exit_date = bar["date"]
                final_status = "stopped"
                final_holding_days = holding_days
                trade_state.exit_reason = action.reason
                break
            elif action.action == "SELL_PARTIAL":
                if trade_state.partial_exit_date is None:
                    trade_state.partial_exit_date = bar["date"]

        if final_exit_price is not None:
            break

        # Max hold days
        if holding_days >= params.max_hold_days:
            final_exit_price = close_price
            final_exit_date = bar["date"]
            final_status = "time_stop"
            final_holding_days = holding_days
            trade_state.exit_reason = "time_stop"
            break

        prev_close = close_price

    # Handle incomplete data
    if final_exit_price is None:
        if incomplete_after_entry and exit_bars:
            last_bar = exit_bars[-1]
            final_exit_price = float(last_bar["close"])
            final_exit_date = last_bar["date"]
            final_status = "incomplete_data"
            final_holding_days = len(exit_bars)
            trade_state.exit_reason = "incomplete_data"

    # Build BacktestTrade
    return_pct: float | None = None
    r_multiple: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    giveback_pct: float | None = None

    if final_exit_price is not None and risk:
        return_pct = ((final_exit_price - entry_price) / entry_price) * 100.0
        r_multiple = (final_exit_price - entry_price) / risk
        mae_r = (entry_price - mae_price) / risk
        mfe_r = (mfe_price - entry_price) / risk
        if mfe_r > 1e-9 and r_multiple is not None:
            giveback_pct = (mfe_r - r_multiple) / mfe_r

    # Weighted R for partial exits
    if trade_state.partial_taken and r_multiple is not None:
        partial_pct = exit_config.partial_profit_pct
        remaining_pct = 1.0 - partial_pct
        r_multiple = partial_pct * trade_state.partial_r + remaining_pct * r_multiple
        if return_pct is not None:
            partial_return = (
                (trade_state.partial_exit_price - entry_price) / entry_price
            ) * 100.0
            final_return = (
                (final_exit_price - entry_price) / entry_price
            ) * 100.0
            return_pct = partial_pct * partial_return + remaining_pct * final_return

    return BacktestTrade(
        symbol=str(signal["symbol"]),
        signal_date=signal["signal_date"],
        status=final_status,
        entry_ref=entry_ref,
        stop=initial_stop,
        pivot=float(signal["pivot"]) if signal.get("pivot") is not None else None,
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=final_exit_date,
        exit_price=final_exit_price,
        holding_days=final_holding_days if final_holding_days > 0 else None,
        return_pct=return_pct,
        r_multiple=r_multiple,
        mae_price=mae_price,
        mfe_price=mfe_price,
        mae_r=mae_r,
        mfe_r=mfe_r,
        giveback_pct=giveback_pct,
        exit_phase=trade_state.phase.value,
        exit_reason=trade_state.exit_reason or None,
        partial_exit_price=trade_state.partial_exit_price if trade_state.partial_taken else None,
        partial_exit_date=trade_state.partial_exit_date,
        partial_exit_r=trade_state.partial_r if trade_state.partial_taken else None,
        partial_exit_pct=exit_config.partial_profit_pct if trade_state.partial_taken else None,
        trail_mode_used=trade_state.trail_mode if trade_state.trail_activated else None,
    )


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
    portfolio_manager: PortfolioManager | None = None,
    use_rust: bool = True,
    entry_gate: Mapping[date, bool] | None = None,
) -> BacktestResult:
    signals = (
        signals_df.filter(pl.col("signal_type") == "BREAKOUT")
        .sort(["signal_date", "symbol"])
        .to_dicts()
    )
    if not signals:
        return BacktestResult(trades=[])

    if use_rust and multibagger_core is not None:
        try:
            return _simulate_with_rust(signals_df, ohlcv_df, params, portfolio_manager, entry_gate=entry_gate)
        except Exception as e:
            logger.warning(f"Rust backtest failed, falling back to Python: {e}")

    ohlcv_rows = (
        ohlcv_df.sort(["symbol", "date"])
        .select("date", "symbol", "open", "high", "low", "close", "volume")
        .to_dicts()
    )
    prices_by_symbol: dict[str, list[dict[str, object]]] = {}
    for row in ohlcv_rows:
        prices_by_symbol.setdefault(str(row["symbol"]), []).append(row)

    signals_by_symbol: dict[str, list[dict[str, object]]] = {}
    for signal in signals:
        signals_by_symbol.setdefault(str(signal["symbol"]), []).append(signal)

    # --- Portfolio-constrained mode: sequential execution ---
    if portfolio_manager is not None:
        return _simulate_with_portfolio(
            signals, prices_by_symbol, signals_by_symbol, params, portfolio_manager, entry_gate=entry_gate,
        )

    # --- Standard mode: per-symbol parallelism ---
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


def _simulate_with_portfolio(
    signals: list[dict[str, object]],
    prices_by_symbol: dict[str, list[dict[str, object]]],
    signals_by_symbol: dict[str, list[dict[str, object]]],
    params: BacktestParams,
    pm: PortfolioManager,
    entry_gate: Mapping[date, bool] | None = None,
) -> BacktestResult:
    """Run backtest sequentially with portfolio-level risk controls.

    Signals are processed in chronological order. Before each fill, the
    portfolio manager checks position limits, heat, sector caps, and
    drawdown state. Rejected signals are marked ``skipped_portfolio``.

    Positions are opened when trades fill and closed only when a later
    signal date reaches or exceeds the trade's exit date (not immediately
    after simulation), so that same-date signals correctly see open
    positions from earlier fills.
    """
    # Track per-symbol overlap to preserve the existing overlap-skip logic
    active_until: dict[str, date] = {}
    all_trades: list[BacktestTrade] = []

    # Deferred closes: (exit_date, symbol) pairs to close when signal_date >= exit_date
    pending_closes: list[tuple[date, str]] = []
    entry_filter = (lambda entry_date: bool(entry_gate.get(entry_date, False))) if entry_gate is not None else None

    # Create a mutable list of signals to allow injection of recovery trades
    signals_queue = list(signals)
    i = 0
    while i < len(signals_queue):
        signal = signals_queue[i]
        i += 1
        
        sig_date = signal["signal_date"]
        sym = str(signal["symbol"])
        symbol_prices = prices_by_symbol.get(sym, [])
        
        # ... (rest of existing loop logic for release positions and overlap)

        # Release positions whose exit_date <= current signal date
        still_pending: list[tuple[date, str]] = []
        for exit_dt, close_sym in pending_closes:
            if exit_dt <= sig_date:
                pm.close_trade(close_sym)
            else:
                still_pending.append((exit_dt, close_sym))
        pending_closes = still_pending

        # Per-symbol overlap check (same as _simulate_symbol_group)
        if sym in active_until and signal["signal_date"] < active_until[sym]:
            all_trades.append(_empty_trade(signal, "skipped_overlap"))
            continue

        # Sector defaults to "UNKNOWN" when not provided on the signal
        sector = str(signal.get("sector", "UNKNOWN"))
        base_depth = signal.get("range10") # Use range10 as base_depth proxy

        trade = _simulate_signal(signal, symbol_prices, params, entry_filter=entry_filter)
        if trade.entry_date is None:
            all_trades.append(trade)
            continue

        risk_per_share = _risk_per_share(trade.entry_price, trade.stop) if trade.entry_price else None
        if risk_per_share and risk_per_share > 0 and trade.entry_price is not None:
            position_value = pm.preview_position_value(risk_per_share, trade.entry_price, base_depth=base_depth)
            if not pm.can_open_trade(sector=sector, position_value=position_value, base_depth=base_depth):
                all_trades.append(_empty_trade(signal, "skipped_portfolio"))
                continue
            pm.open_trade(
                symbol=sym,
                sector=sector,
                risk_per_share=risk_per_share,
                entry_price=trade.entry_price,
                base_depth=base_depth,
            )

        all_trades.append(trade)

        # SQUAT RECOVERY: If stopped out, check for pivot reclaim within 10 days
        if trade.status == "stopped" and trade.exit_date:
            pivot = signal.get("pivot")
            vol50 = signal.get("vol50")
            if pivot and vol50:
                recovery_bars = [b for b in symbol_prices if b["date"] > trade.exit_date][:10]
                for bar in recovery_bars:
                    if bar["close"] >= pivot and bar["volume"] >= vol50 * 1.5:
                        # Re-trigger a breakout signal on this date
                        recovery_sig = signal.copy()
                        recovery_sig["signal_date"] = bar["date"]
                        recovery_sig["signal_type"] = "BUY_RECOVERY"
                        # Insert into the queue maintaining date order if possible, 
                        # but simple append + re-sort or immediate processing also works
                        signals_queue.append(recovery_sig)
                        signals_queue.sort(key=lambda s: s["signal_date"])
                        break

        # Track overlap
        if trade.exit_date is not None:
            active_until[sym] = trade.exit_date
            # Defer position close until we reach a signal on or after the exit date
            pending_closes.append((trade.exit_date, sym))
        elif symbol_prices:
            active_until[sym] = symbol_prices[-1]["date"]

    all_trades.sort(key=lambda t: (t.signal_date, t.symbol))
    return BacktestResult(trades=all_trades)


def _simulate_with_rust(
    signals_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    params: BacktestParams,
    pm: PortfolioManager | None = None,
    entry_gate: Mapping[date, bool] | None = None,
) -> BacktestResult:
    """Helper to prepare data and call the Rust engine."""
    if multibagger_core is None:
        raise ImportError("multibagger_core not installed")

    # 1. Sort and prepare contiguous OHLCV data
    ohlcv_sorted = ohlcv_df.sort(["symbol", "date"])

    all_dates = [d.strftime("%Y-%m-%d") for d in ohlcv_sorted["date"]]
    opens = ohlcv_sorted["open"].to_numpy().astype(np.float64)
    highs = ohlcv_sorted["high"].to_numpy().astype(np.float64)
    lows = ohlcv_sorted["low"].to_numpy().astype(np.float64)
    closes = ohlcv_sorted["close"].to_numpy().astype(np.float64)

    # 2. Calculate symbol indices
    symbol_indices = {}
    current_idx = 0
    counts = ohlcv_sorted.group_by("symbol", maintain_order=True).len()
    for row in counts.iter_rows():
        sym, count = row[0], row[1]
        symbol_indices[sym] = (current_idx, current_idx + count)
        current_idx += count

    # 3. Prepare signals JSON
    breakout_signals = signals_df.filter(pl.col("signal_type") == "BREAKOUT")
    if "sector" in breakout_signals.columns:
        breakout_signals = breakout_signals.with_columns(
            pl.col("sector").cast(pl.String).fill_null("UNKNOWN").alias("sector")
        )
    else:
        breakout_signals = breakout_signals.with_columns(pl.lit("UNKNOWN").alias("sector"))

    rust_signals = []
    for sig in breakout_signals.to_dicts():
        sig_copy = {
            "symbol": str(sig["symbol"]),
            "signal_date": sig["signal_date"].strftime("%Y-%m-%d"),
            "entry_ref": float(sig["entry_ref"]),
            "stop": float(sig["stop"]),
            "pivot": float(sig["pivot"]) if sig.get("pivot") is not None else None,
            "rs_rank": float(sig.get("rs_rank", 80.0)),
            "sector": str(sig.get("sector", "UNKNOWN")),
        }
        rust_signals.append(sig_copy)

    # 4. Prepare params JSON
    params_json = json.dumps(
        {
            "pending_days": params.pending_days,
            "max_hold_days": params.max_hold_days,
            "target_r": params.target_r,
            "exit_mode": params.exit_mode.value,
            "trail_activation_r": params.trail_activation_r,
            "trail_pct": params.trail_pct,
            "dead_money_days": params.dead_money_days,
        }
    )

    # 5. Execute Rust Engine
    if pm is not None:
        allowed_entry_dates = None
        if entry_gate is not None:
            allowed_entry_dates = [
                gate_date.isoformat()
                for gate_date, allowed in sorted(entry_gate.items())
                if allowed
            ]
        portfolio_json = json.dumps({
            "initial_capital": pm.initial_capital,
            "risk_per_trade_pct": pm.risk_per_trade_pct,
            "max_positions": pm.max_positions,
            "max_sector_positions": pm.max_sector_positions,
            "max_sector_pct": pm.max_sector_pct,
            "max_heat_pct": pm.max_heat_pct,
            "regime_heat_mult_strong": pm.regime_heat_mult_strong,
            "drawdown_half_pct": pm.drawdown_half_pct,
            "drawdown_stop_pct": pm.drawdown_stop_pct,
            "drawdown_close_pct": pm.drawdown_close_pct,
            "allowed_entry_dates": allowed_entry_dates,
        })
        results_json = multibagger_core.run_portfolio_backtest_rust(
            json.dumps(rust_signals),
            params_json,
            portfolio_json,
            all_dates,
            opens,
            highs,
            lows,
            closes,
            symbol_indices,
        )
    else:
        results_json = multibagger_core.run_bulk_backtest_rust_v2(
            json.dumps(rust_signals),
            params_json,
            all_dates,
            opens,
            highs,
            lows,
            closes,
            symbol_indices,
        )

    rust_trades_raw = []
    
    if pm is not None:
        rust_dict = json.loads(results_json)
        rust_trades_raw = rust_dict["trades"]
        initial_cap = rust_dict["initial_capital"]
        final_eq = rust_dict["final_equity"]
        print("\n" + "="*50)
        print("💰 PORTFOLIO SIMULATION RESULTS")
        print(f" -> Initial Capital: ₹{initial_cap:,.2f}")
        print(f" -> Final Equity:    ₹{final_eq:,.2f}")
        print(f" -> Total Return:    {((final_eq - initial_cap) / initial_cap) * 100:.2f}%")
        print("="*50 + "\n")
    else:
        rust_trades_raw = json.loads(results_json)

    # 6. Convert back to BacktestTrade objects
    final_trades = []
    for rt in rust_trades_raw:
        final_trades.append(
            BacktestTrade(
                symbol=rt["symbol"],
                signal_date=date.fromisoformat(rt["signal_date"]),
                status=rt["status"],
                entry_ref=rt["entry_ref"],
                stop=rt["stop"],
                pivot=rt["pivot"],
                entry_date=date.fromisoformat(rt["entry_date"]) if rt["entry_date"] else None,
                entry_price=rt["entry_price"],
                exit_date=date.fromisoformat(rt["exit_date"]) if rt["exit_date"] else None,
                exit_price=rt["exit_price"],
                holding_days=rt["holding_days"],
                return_pct=rt["return_pct"],
                r_multiple=rt["r_multiple"],
                mae_price=rt["mae_price"],
                mfe_price=rt["mfe_price"],
            )
        )

    return BacktestResult(trades=final_trades)

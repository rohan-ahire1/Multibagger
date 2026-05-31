from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl
from sepa_scanner.backtest.exit_engine import (
    BarData,
    ExitConfig,
    TradeState,
    evaluate_exit,
    evaluate_exit_baseline,
    ExitPhase
)
from sepa_scanner.backtest.models import BacktestParams, BacktestTrade, ExitMode
from sepa_scanner.settings import AppSettings
from sepa_scanner.strategy.ranking import (
    add_signal_score,
    add_signal_size_multiplier,
    rank_signals,
    select_top_signals_by_day,
)

@dataclass
class OpenPosition:
    symbol: str
    sector: str
    entry_date: date
    entry_price: float
    shares: float
    initial_risk: float
    active_stop: float
    mae_price: float
    mfe_price: float
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    original_shares: float
    realized_pnl: float = 0.0
    partial_taken: bool = False
    breakeven_activated: bool = False
    trail_activated: bool = False
    phase: ExitPhase = ExitPhase.INITIAL_STOP
    trail_mode: str = "adaptive"
    dead_money_deadline_days: int | None = None
    dead_money_extended: bool = False
    pyramid_adds_taken: int = 0
    next_pyramid_r: float | None = None
    cost_basis: float = 0.0
    partial_exit_price: float | None = None
    partial_exit_date: date | None = None
    partial_exit_r: float | None = None
    tighten_activated: bool = False
    target_price: float | None = None

@dataclass
class PendingSignal:
    symbol: str
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    rs_rank: float
    size_mult: float
    expiration_date: date

def empty_trade_dict(symbol: str, signal_date: date, status: str, entry_ref: float, stop: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "status": status,
        "entry_ref": entry_ref,
        "stop": stop,
        "pivot": None,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "holding_days": None,
        "return_pct": None,
        "r_multiple": None,
        "mae_price": None,
        "mfe_price": None,
        "shares": 0.0,
        "pnl": 0.0,
        "sector": "UNKNOWN",
    }

def close_trade_dict(
    symbol: str,
    signal_date: date,
    status: str,
    entry_ref: float,
    stop: float,
    pivot: float | None,
    entry_date: date,
    entry_price: float,
    exit_date: date,
    exit_price: float,
    holding_days: int,
    mae_price: float,
    mfe_price: float,
    shares: float,
    sector: str,
    original_shares: float,
    realized_pnl: float,
) -> dict[str, Any]:
    net_stop = stop * 0.999 * 0.99896342
    risk = entry_price - net_stop
    pnl = realized_pnl + (shares * (exit_price - entry_price)) - (16.0 if shares > 0.0 else 0.0)
    total_shares = original_shares
    
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "status": status,
        "entry_ref": entry_ref,
        "stop": stop,
        "pivot": pivot,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "holding_days": holding_days,
        "return_pct": ((pnl / (total_shares * entry_price)) * 100.0) if entry_price and total_shares > 1e-5 else None,
        "r_multiple": (pnl / (total_shares * risk)) if risk > 0.0 and total_shares > 1e-5 else None,
        "mae_price": mae_price,
        "mfe_price": mfe_price,
        "shares": total_shares,
        "pnl": pnl,
        "sector": sector,
    }

def build_symbol_data(ohlcv: pl.DataFrame) -> tuple[dict[str, dict[str, Any]], list[date]]:
    symbol_data = {}
    unique_dates = ohlcv["date"].unique().sort().to_list()

    for symbol, group in ohlcv.group_by("symbol"):
        group_sorted = group.sort("date")
        closes = group_sorted["close"].to_list()
        highs = group_sorted["high"].to_list()
        lows = group_sorted["low"].to_list()
        opens = group_sorted["open"].to_list()
        volumes = group_sorted["volume"].to_list()

        # Technical indicators needed by BarData / evaluate_exit
        sma_50 = _rolling_mean(closes, 50)
        sma_200 = _rolling_mean(closes, 200)
        ema_10 = _ema(closes, 10)
        ema_21 = _ema(closes, 21)
        ema_50 = _ema(closes, 50)
        low_10d = _rolling_min(lows, 10)
        prev_close = [0.0] + closes[:-1]
        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range_20 = _rolling_mean(ranges, 20)
        vol_floats = [float(v) for v in volumes]
        avg_volume_50 = _rolling_mean(vol_floats, 50)

        sym_key = symbol[0] if isinstance(symbol, tuple) else str(symbol)
        symbol_data[sym_key] = {
            "dates": group_sorted["date"].to_list(),
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes,
            "date_to_idx": {d: i for i, d in enumerate(group_sorted["date"].to_list())},
            "sma_50": sma_50,
            "sma_200": sma_200,
            "ema_10": ema_10,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "low_10d": low_10d,
            "prev_close": prev_close,
            "avg_range_20": avg_range_20,
            "avg_volume_50": avg_volume_50,
        }
    return symbol_data, unique_dates


def _rolling_mean(values: list[float], window: int) -> list[float]:
    n = len(values)
    result = [0.0] * n
    running_sum = 0.0
    for i in range(n):
        running_sum += values[i]
        if i >= window:
            running_sum -= values[i - window]
        if i >= window - 1:
            result[i] = running_sum / window
    return result


def _rolling_min(values: list[float], window: int) -> list[float]:
    from collections import deque
    n = len(values)
    result = [0.0] * n
    dq: deque[int] = deque()
    for i in range(n):
        while dq and dq[0] <= i - window:
            dq.popleft()
        while dq and values[dq[-1]] >= values[i]:
            dq.pop()
        dq.append(i)
        result[i] = values[dq[0]]
    return result


def _ema(values: list[float], span: int) -> list[float]:
    n = len(values)
    result = [0.0] * n
    if n < span:
        return result
    seed = sum(values[:span]) / span
    result[span - 1] = seed
    multiplier = 2.0 / (span + 1.0)
    for i in range(span, n):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]
    return result

def _build_exit_config(exit_cfg: dict[str, Any]) -> ExitConfig:
    """Build an ExitConfig from the exit_cfg dict, using defaults for missing keys."""
    cfg = ExitConfig()
    field_map = {
        "breakeven_activation_r": "breakeven_activation_r",
        "partial_profit_r": "partial_profit_r",
        "partial_profit_pct": "partial_profit_pct",
        "trail_pct": "trail_pct",
        "trail_pct_bull": "trail_pct_bull",
        "trail_pct_bear": "trail_pct_bear",
        "time_stop_days": "time_stop_days",
        "time_stop_min_r": "time_stop_min_r",
        "max_hold_before_tighten": "max_hold_before_tighten",
        "soft_expiry_days": "soft_expiry_days",
        "soft_expiry_gain": "soft_expiry_gain",
    }
    for dict_key, attr in field_map.items():
        val = exit_cfg.get(dict_key)
        if val is not None:
            setattr(cfg, attr, type(getattr(cfg, attr))(val))
    return cfg


def simulate_portfolio_python(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    params: BacktestParams,
    portfolio: dict[str, Any],
    analysis_sector_map: dict[str, str],
    *,
    exit_config: dict[str, Any] | None = None,
    entry_gate: dict[date, bool] | None = None,
    entry_size_mult: dict[date, float] | None = None,
) -> dict[str, Any]:
    symbols = signals.to_dicts()
    symbol_data, unique_dates = build_symbol_data(ohlcv)
    
    # Build phased exit config using sensible defaults.
    # Settings may have aggressive values tuned for the old inline engine,
    # so we use ExitConfig defaults and only override compatible fields.
    phased_exit_config = ExitConfig()
    if exit_config:
        safe_overrides = {
            "breakeven_activation_r", "partial_profit_r", "partial_profit_pct",
            "trail_pct_bull", "trail_pct_bear", "trail_pct",
            "climax_min_hold_days", "climax_conditions_required",
            "time_stop_days", "time_stop_min_r",
            "soft_expiry_days", "soft_expiry_gain",
            "trail_widen_r", "max_hold_before_tighten",
            "max_loss_pct", "absolute_max_loss_pct",
        }
        for key, value in exit_config.items():
            if value is not None and key in safe_overrides and hasattr(phased_exit_config, key):
                setattr(phased_exit_config, key, type(getattr(phased_exit_config, key))(value))

    entry_gate = entry_gate or {}
    entry_size_mult = entry_size_mult or {}

    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for signal in symbols:
        signals_by_date[signal["signal_date"]].append(signal)

    friction_enabled = getattr(params, "friction_enabled", True)
    slippage_buy = 1.0 if not friction_enabled else 1.001
    slippage_sell = 1.0 if not friction_enabled else 0.999
    tax_buy = 1.0 if not friction_enabled else 1.00118658
    tax_sell = 1.0 if not friction_enabled else 0.99896342
    dp_charge = 0.0 if not friction_enabled else 16.0

    initial_capital = float(portfolio["initial_capital"])
    cash = initial_capital
    equity_peak = initial_capital
    open_positions: list[OpenPosition] = []
    pending_signals: list[PendingSignal] = []
    closed_trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous_equity = initial_capital

    for today in unique_dates:
        i = 0
        while i < len(open_positions):
            pos = open_positions[i]
            sym_prices = symbol_data.get(pos.symbol)
            if sym_prices is None:
                i += 1
                continue

            idx = sym_prices["date_to_idx"].get(today)
            if idx is not None:
                open_price = float(sym_prices["opens"][idx])
                high_price = float(sym_prices["highs"][idx])
                low_price = float(sym_prices["lows"][idx])
                close_price = float(sym_prices["closes"][idx])
                holding_days = (today - pos.entry_date).days

                pos.mae_price = min(pos.mae_price, low_price)
                pos.mfe_price = max(pos.mfe_price, high_price)

                # --- Gap-down stop: if open gaps below stop, exit at open ---
                if open_price <= pos.active_stop:
                    net_entry = pos.entry_price * slippage_buy * tax_buy
                    net_exit = open_price * slippage_sell * tax_sell
                    cash += pos.shares * net_exit - dp_charge
                    closed_trades.append(
                        close_trade_dict(
                            symbol=pos.symbol, signal_date=pos.signal_date,
                            status="stopped", entry_ref=pos.entry_ref,
                            stop=pos.stop, pivot=pos.pivot,
                            entry_date=pos.entry_date, entry_price=net_entry,
                            exit_date=today, exit_price=net_exit,
                            holding_days=holding_days,
                            mae_price=pos.mae_price, mfe_price=pos.mfe_price,
                            shares=pos.shares, sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                            original_shares=pos.original_shares, realized_pnl=pos.realized_pnl,
                        )
                    )
                    open_positions.pop(i)
                    continue

                # --- Build TradeState from OpenPosition ---
                trade_state = TradeState(
                    symbol=pos.symbol,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    initial_risk=pos.initial_risk,
                    initial_stop=pos.stop,
                    planned_shares=int(pos.original_shares),
                    phase=pos.phase,
                    current_shares=int(pos.shares),
                    active_stop=pos.active_stop,
                    highest_close=pos.mfe_price,
                    trail_mode=pos.trail_mode,
                    breakeven_activated=pos.breakeven_activated,
                    partial_taken=pos.partial_taken,
                    trail_activated=pos.trail_activated,
                    tighten_activated=pos.tighten_activated,
                    days_held=holding_days - 1,  # evaluate_exit increments by 1
                )

                # --- Build BarData from symbol_data ---
                bar = BarData(
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=int(sym_prices["volumes"][idx]),
                    sma_50=float(sym_prices["sma_50"][idx]),
                    sma_200=float(sym_prices["sma_200"][idx]),
                    ema_10=float(sym_prices["ema_10"][idx]),
                    ema_21=float(sym_prices["ema_21"][idx]),
                    ema_50=float(sym_prices["ema_50"][idx]),
                    low_10d=float(sym_prices["low_10d"][idx]),
                    prev_close=float(sym_prices["prev_close"][idx]),
                    avg_range_20=float(sym_prices["avg_range_20"][idx]),
                    avg_volume_50=float(sym_prices["avg_volume_50"][idx]),
                )

                # --- Route to correct exit engine ---
                _baseline_modes = {"baseline_hard", "baseline_ema10", "baseline_ema21"}
                if params.exit_mode.value in _baseline_modes:
                    actions = evaluate_exit_baseline(
                        trade_state,
                        bar=bar,
                        exit_mode=params.exit_mode.value,
                    )
                else:
                    actions = evaluate_exit(
                        trade_state,
                        bar=bar,
                        config=phased_exit_config,
                        market_regime="bull",
                        rs_rank=80.0,
                    )

                # --- Sync state back to OpenPosition ---
                pos.phase = trade_state.phase
                pos.active_stop = trade_state.active_stop
                pos.breakeven_activated = trade_state.breakeven_activated
                pos.partial_taken = trade_state.partial_taken
                pos.trail_activated = trade_state.trail_activated
                pos.tighten_activated = trade_state.tighten_activated
                pos.trail_mode = trade_state.trail_mode
                pos.mfe_price = max(pos.mfe_price, trade_state.highest_close)

                # --- Process actions ---
                exit_price: float | None = None
                status = ""
                exit_reason = ""
                for action in actions:
                    if action.action == "SELL_ALL":
                        exit_price = close_price
                        status = action.reason
                        exit_reason = action.reason
                        break
                    elif action.action == "SELL_PARTIAL":
                        sell_shares = min(pos.shares, float(action.shares))
                        net_entry = pos.entry_price * slippage_buy * tax_buy
                        sell_p_slippage = close_price * slippage_sell
                        net_sell = sell_p_slippage * tax_sell
                        net_sell_val = (sell_shares * net_sell) - dp_charge
                        pos.realized_pnl += net_sell_val - (sell_shares * net_entry)
                        cash += net_sell_val
                        pos.shares -= sell_shares
                        pos.partial_exit_price = net_sell
                        pos.partial_exit_date = today
                        net_stop = pos.stop * slippage_sell * tax_sell
                        net_risk = net_entry - net_stop
                        pos.partial_exit_r = (net_sell - net_entry) / net_risk if net_risk > 0 else 0.0

                # --- Hard max hold cap — unconditional capital release ---
                if exit_price is None and holding_days >= params.max_hold_days:
                    exit_price = close_price
                    status = "time_stop"
                    exit_reason = "time_stop"

                if exit_price is not None:
                    net_entry = pos.entry_price * slippage_buy * tax_buy
                    net_exit = exit_price * slippage_sell * tax_sell
                    cash += pos.shares * net_exit - dp_charge
                    td = close_trade_dict(
                        symbol=pos.symbol, signal_date=pos.signal_date,
                        status=status, entry_ref=pos.entry_ref,
                        stop=pos.stop, pivot=pos.pivot,
                        entry_date=pos.entry_date, entry_price=net_entry,
                        exit_date=today, exit_price=net_exit,
                        holding_days=holding_days,
                        mae_price=pos.mae_price, mfe_price=pos.mfe_price,
                        shares=pos.shares, sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                        original_shares=pos.original_shares, realized_pnl=pos.realized_pnl,
                    )
                    td["exit_phase"] = trade_state.phase.value
                    td["exit_reason"] = exit_reason
                    td["trail_mode_used"] = trade_state.trail_mode
                    if pos.partial_exit_price is not None:
                        td["partial_exit_price"] = pos.partial_exit_price
                        td["partial_exit_date"] = pos.partial_exit_date
                        td["partial_exit_r"] = pos.partial_exit_r
                    closed_trades.append(td)
                    open_positions.pop(i)
                    continue
            i += 1

        invested_value = 0.0
        for p in open_positions:
            idx = symbol_data[p.symbol]["date_to_idx"].get(today)
            if idx is not None:
                raw_price = float(symbol_data[p.symbol]["closes"][idx])
            else:
                raw_price = p.entry_price
            invested_value += p.shares * raw_price * slippage_sell * tax_sell
        current_equity = cash + invested_value
        equity_peak = max(equity_peak, current_equity)
        dd_pct = ((equity_peak - current_equity) / equity_peak) if equity_peak > 0.0 else 0.0

        if dd_pct >= portfolio["drawdown_close_pct"]:
            size_mult = 0.0
        elif dd_pct >= portfolio["drawdown_stop_pct"]:
            size_mult = 0.0
        elif dd_pct >= portfolio["drawdown_half_pct"]:
            size_mult = 0.5
        else:
            size_mult = 1.0
            
        day_entries_allowed = bool(entry_gate.get(today, True)) if entry_gate else True
        day_entry_size_mult = float(entry_size_mult.get(today, 1.0)) if entry_size_mult else 1.0

        if today in signals_by_date:
            for sig in signals_by_date[today]:
                pending_signals.append(
                    PendingSignal(
                        symbol=sig["symbol"],
                        signal_date=sig["signal_date"],
                        entry_ref=sig["entry_ref"],
                        stop=sig["stop"],
                        pivot=sig["pivot"],
                        rs_rank=sig["rs_rank"],
                        size_mult=float(sig.get("signal_size_mult", 1.0) or 1.0),
                        expiration_date=today + timedelta(days=params.pending_days),
                    )
                )

        j = 0
        while j < len(pending_signals):
            pending = pending_signals[j]
            if pending.expiration_date < today:
                closed_trades.append(empty_trade_dict(pending.symbol, pending.signal_date, "not_filled", pending.entry_ref, pending.stop))
                pending_signals.pop(j)
                continue

            if not day_entries_allowed or size_mult == 0.0 or len(open_positions) >= int(portfolio["max_positions"]) or current_equity <= 0.0:
                j += 1
                continue

            sym_prices = symbol_data.get(pending.symbol)
            if sym_prices is None or today not in sym_prices["date_to_idx"]:
                j += 1
                continue

            idx = sym_prices["date_to_idx"][today]
            open_p, high_p = float(sym_prices["opens"][idx]), float(sym_prices["highs"][idx])
            
            if open_p >= pending.entry_ref or high_p >= pending.entry_ref:
                fill_p = max(open_p, pending.entry_ref)
                risk_budget = current_equity * portfolio["risk_per_trade_pct"] * size_mult * day_entry_size_mult * pending.size_mult

                net_entry = fill_p * slippage_buy * tax_buy
                net_stop = pending.stop * slippage_sell * tax_sell
                net_risk = net_entry - net_stop

                if net_risk > 0:
                    shares = min(risk_budget / net_risk, cash / net_entry)
                    cost = shares * net_entry
                    if shares > 1e-5 and cost <= cash:
                        cash -= cost
                        open_positions.append(OpenPosition(
                            symbol=pending.symbol,
                            sector=analysis_sector_map.get(pending.symbol, "UNKNOWN"),
                            entry_date=today,
                            entry_price=fill_p,
                            shares=shares,
                            initial_risk=fill_p - pending.stop,
                            active_stop=pending.stop,
                            mae_price=fill_p,
                            mfe_price=fill_p,
                            signal_date=pending.signal_date,
                            entry_ref=pending.entry_ref,
                            stop=pending.stop,
                            pivot=pending.pivot,
                            original_shares=shares,
                            cost_basis=cost,
                            next_pyramid_r=float(portfolio.get("pyramid_trigger_r", 0.5))
                        ))
                        pending_signals.pop(j)
                        continue
            j += 1

        daily_rows.append({
            "date": today,
            "equity": current_equity,
            "cash": cash,
            "positions": len(open_positions),
            "daily_pnl": current_equity - previous_equity,
            "daily_return": (current_equity / previous_equity - 1.0) if previous_equity > 0 else 0.0
        })
        previous_equity = current_equity

    _bt_fields = {f.name for f in BacktestTrade.__dataclass_fields__.values()}
    return {
        "trades": [BacktestTrade(**{k: v for k, v in t.items() if k in _bt_fields}) for t in closed_trades],
        "daily_equity": pl.DataFrame(daily_rows, schema={
            "date": pl.Date, "equity": pl.Float64, "cash": pl.Float64, 
            "positions": pl.Int64, "daily_pnl": pl.Float64, "daily_return": pl.Float64
        })
    }

def build_entry_controls(settings: Any, regime_df: pl.DataFrame) -> tuple[dict[date, bool], dict[date, float]]:
    gate, size_mult = {}, {}
    for row in regime_df.sort("date").iter_rows(named=True):
        gate[row["date"]] = not bool(row.get("high_vol_market", False))
        size_mult[row["date"]] = 1.0
    return gate, size_mult

def compute_regime_inputs(settings: Any, start_date: date, end_date: date, benchmark_df: pl.DataFrame) -> pl.DataFrame:
    # Minimal implementation for UI compatibility
    return benchmark_df.with_columns(pl.lit(False).alias("high_vol_market"))

def filter_signals(
    signals: pl.DataFrame,
    *,
    settings: AppSettings,
    diagnostics: pl.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    rs_rank_min: float | None = None,
    rs_rank_max: float | None = None,
    ranking_mode: str = "default",
    max_signals_per_day: int | None = None,
) -> pl.DataFrame:
    df = signals.clone()
    
    # RS Rank filter
    if rs_rank_min is not None:
        df = df.filter(pl.col("rs_rank") >= float(rs_rank_min))
    if rs_rank_max is not None:
        df = df.filter(pl.col("rs_rank") <= float(rs_rank_max))

    # Add Sector RS if needed
    if settings.backtest.sector_alpha_enabled and sector_map:
        sector_df = pl.DataFrame([
            {"symbol": sym, "sector": sec} for sym, sec in sector_map.items()
        ])
        df = df.join(sector_df, on="symbol", how="left").fill_null("UNKNOWN")
        
        sector_rs_df = (
            df.group_by(["signal_date", "sector"])
            .agg(pl.col("rs_rank").median().alias("sector_rs"))
        )
        df = df.join(sector_rs_df, on=["signal_date", "sector"], how="left")
        
        s_cfg = settings.backtest
        df = df.with_columns(
            pl.when(pl.col("sector_rs") >= s_cfg.sector_alpha_threshold_high).then(pl.lit(s_cfg.sector_alpha_mult_high))
            .when(pl.col("sector_rs") <= s_cfg.sector_alpha_threshold_low).then(pl.lit(s_cfg.sector_alpha_mult_low))
            .otherwise(pl.lit(1.0))
            .alias("signal_size_mult")
        )

    # Daily Ranking
    df = select_top_signals_by_day(df, max_signals_per_day, ranking_mode=ranking_mode)
    return df

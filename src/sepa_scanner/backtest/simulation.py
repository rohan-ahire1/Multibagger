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
    pending_exit_reason: str | None = None
    bars_held: int = 0

@dataclass
class PendingSignal:
    symbol: str
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    rs_rank: float
    size_mult: float
    bars_checked: int = 0

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

# The duplicate portfolio simulator that used to live here was deleted.
#
# It was a stale fork of the canonical engine, now at
# sepa_scanner.lab.engines.portfolio_sim. The fork lacked the idle-cash sleeve,
# the ETF sector-cap exemption, pyramiding, and the entry-collision handling,
# defaulted friction_enabled the other way, and shipped a compute_regime_inputs
# stub that hardcoded high_vol_market=False. Anything importing it — including
# the /api/backtest routes — produced numbers that could not reproduce
# docs/BACKTEST_TRUTH.md.
#
# sepa_scanner.backtest re-exports the canonical implementations, so existing
# `from sepa_scanner.backtest import simulate_portfolio_python` imports keep
# working and now get the real engine.
#
# What remains in this module are the shared helpers (dataclasses, trade dict
# builders, rolling windows, exit-config construction) that the canonical
# engine imports.

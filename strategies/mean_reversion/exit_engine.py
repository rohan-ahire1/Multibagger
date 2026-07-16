from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(slots=True)
class MRExitConfig:
    exit_mode: str = "sma5"
    time_stop_days: int = 5
    stop_atr_mult: float = 2.0
    max_hold_days: int = 20


@dataclass(slots=True)
class MRExitAction:
    action: str
    reason: str


def _valid(value: float) -> bool:
    return not math.isnan(float(value))


def evaluate_mr_exit(
    *,
    entry_price: float,
    entry_date: date,
    active_stop: float,
    today: date,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    sma5: float,
    sma3: float,
    rsi2: float,
    cum_rsi2: float,
    config: MRExitConfig,
) -> MRExitAction:
    """Evaluate the simple mean-reversion exit stack."""
    _ = entry_price, high_price
    holding_days = (today - entry_date).days
    if open_price <= active_stop:
        return MRExitAction("SELL_ALL", "stopped")
    if low_price <= active_stop:
        return MRExitAction("SELL_ALL", "stopped")

    mode = config.exit_mode
    if mode == "sma5" and _valid(sma5) and close_price > sma5:
        return MRExitAction("SELL_ALL", "mean_revert_sma5")
    if mode == "sma3" and _valid(sma3) and close_price > sma3:
        return MRExitAction("SELL_ALL", "mean_revert_sma3")
    if mode == "rsi_70" and _valid(rsi2) and rsi2 > 70.0:
        return MRExitAction("SELL_ALL", "rsi_overbought")
    if mode == "rsi_50" and _valid(rsi2) and rsi2 > 50.0:
        return MRExitAction("SELL_ALL", "rsi_normalized")
    if mode == "cum_rsi_130" and _valid(cum_rsi2) and cum_rsi2 > 130.0:
        return MRExitAction("SELL_ALL", "cum_rsi_130")
    if mode == "cum_rsi_150" and _valid(cum_rsi2) and cum_rsi2 > 150.0:
        return MRExitAction("SELL_ALL", "cum_rsi_150")
    if mode == "time_stop" and holding_days >= config.time_stop_days:
        return MRExitAction("SELL_ALL", "time_stop")
    if mode == "combined":
        if _valid(sma5) and close_price > sma5:
            return MRExitAction("SELL_ALL", "mean_revert_sma5")
        if holding_days >= config.time_stop_days:
            return MRExitAction("SELL_ALL", "time_stop")
    if mode == "combined_sma5_10d":
        if _valid(sma5) and close_price > sma5:
            return MRExitAction("SELL_ALL", "mean_revert_sma5")
        if holding_days >= 10:
            return MRExitAction("SELL_ALL", "time_stop_10d")
    if holding_days >= config.max_hold_days:
        return MRExitAction("SELL_ALL", "max_hold")
    return MRExitAction("HOLD", "")

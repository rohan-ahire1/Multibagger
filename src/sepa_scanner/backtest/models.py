from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

import polars as pl


class ExitPhase(str, Enum):
    INITIAL_STOP = "initial_stop"
    BREAKEVEN = "breakeven"
    PARTIAL_PROFIT = "partial_profit"
    TRAILING = "trailing"
    TIGHTEN = "tighten"
    EXIT = "exit"


class ExitMode(str, Enum):
    V1_FIXED = "v1_fixed"
    V2_TRAIL_10D = "v2_trail_10d"
    V2_TRAIL_21EMA = "v2_trail_21ema"
    V3_TRAIL_PCT = "v3_trail_pct"
    MINERVINI_PHASED = "minervini_phased"


EXIT_MODE_LABELS: dict[ExitMode, str] = {
    ExitMode.V1_FIXED: "V1 Fixed Stop",
    ExitMode.V2_TRAIL_10D: "V2 Trail 10-Day Low",
    ExitMode.V2_TRAIL_21EMA: "V2 Trail 21-EMA",
    ExitMode.V3_TRAIL_PCT: "V3 Trail Percentage",
    ExitMode.MINERVINI_PHASED: "Minervini Phased",
}


@dataclass(slots=True)
class BacktestParams:
    pending_days: int = 5
    max_hold_days: int = 90
    target_r: float | None = None
    exit_mode: ExitMode = ExitMode.V2_TRAIL_10D
    trail_activation_r: float = 2.0
    trail_pct: float = 0.20
    dead_money_days: int | None = 15

    def __post_init__(self) -> None:
        if self.pending_days <= 0:
            raise ValueError("pending_days must be positive")
        if self.max_hold_days <= 0:
            raise ValueError("max_hold_days must be positive")
        if self.target_r is not None and self.target_r <= 0:
            raise ValueError("target_r must be positive when provided")
        if self.trail_activation_r <= 0:
            raise ValueError("trail_activation_r must be positive")
        if self.trail_pct <= 0 or self.trail_pct >= 1.0:
            raise ValueError("trail_pct must be between 0 and 1 (exclusive)")
        if self.dead_money_days is not None and self.dead_money_days <= 0:
            raise ValueError("dead_money_days must be positive when provided")


@dataclass(slots=True)
class BacktestTrade:
    symbol: str
    signal_date: date
    status: str
    entry_ref: float
    stop: float
    pivot: float | None = None
    entry_date: date | None = None
    entry_price: float | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    holding_days: int | None = None
    return_pct: float | None = None
    r_multiple: float | None = None
    mae_price: float | None = None
    mfe_price: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    giveback_pct: float | None = None
    exit_phase: str | None = None
    exit_reason: str | None = None
    partial_exit_price: float | None = None
    partial_exit_date: date | None = None
    partial_exit_r: float | None = None
    partial_exit_pct: float | None = None
    trail_mode_used: str | None = None


@dataclass(slots=True)
class BacktestResult:
    trades: list[BacktestTrade]


@dataclass(slots=True)
class BacktestSummary:
    total_signals: int
    filled_trades: int
    not_filled_signals: int
    stopped_trades: int
    target_exits: int
    time_stop_exits: int
    win_rate: float
    average_return_pct: float | None
    average_r: float | None
    median_r: float | None
    expectancy: float | None
    payoff_ratio: float | None
    cumulative_r: float
    max_drawdown: float
    longest_losing_streak: int
    avg_mfe_r: float | None = None
    avg_mae_r: float | None = None
    avg_giveback_pct: float | None = None
    dead_money_exits: int = 0
    exit_mode: str = ExitMode.V2_TRAIL_10D.value
    breakeven_exits: int = 0
    partial_profit_exits: int = 0
    trail_exits: int = 0
    tighten_exits: int = 0
    climax_sells: int = 0
    extension_sells: int = 0
    avg_partial_r: float | None = None
    pct_reaching_breakeven: float = 0.0
    pct_reaching_partial: float = 0.0
    pct_reaching_trail: float = 0.0
    exit_engine: str = "legacy"


@dataclass(slots=True)
class BacktestReport:
    params: BacktestParams
    summary: BacktestSummary
    trades: pl.DataFrame
    equity_curve: pl.DataFrame

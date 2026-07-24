"""Strategy lab: one contract, shared engines, comparable results."""

from sepa_scanner.lab.contracts import (
    ParamSpec,
    Strategy,
    StrategyContext,
    StrategyKind,
)
from sepa_scanner.lab.registry import get_strategy, list_strategies, register_strategy
from sepa_scanner.lab.result import BacktestRunResult, EquityPoint, RunMetrics, TradeRecord
from sepa_scanner.lab.runner import run_backtest

__all__ = [
    "BacktestRunResult",
    "EquityPoint",
    "ParamSpec",
    "RunMetrics",
    "Strategy",
    "StrategyContext",
    "StrategyKind",
    "TradeRecord",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "run_backtest",
]

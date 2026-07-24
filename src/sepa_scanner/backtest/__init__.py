from typing import Any

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams, BacktestResult, BacktestTrade, ExitMode

# The canonical portfolio engine lives in sepa_scanner.lab.engines.portfolio_sim.
# It is re-exported here so the historical import path keeps working, but it is
# resolved lazily: that module imports sepa_scanner.backtest submodules
# (queries, exit_engine, simulation), so importing it eagerly here would be a
# circular import. PEP 562 defers the cost to first attribute access.
_LAZY_EXPORTS = frozenset(
    {
        "build_entry_controls",
        "compute_regime_inputs",
        "filter_signals",
        "simulate_portfolio_python",
    }
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        from sepa_scanner.lab.engines import portfolio_sim

        return getattr(portfolio_sim, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_EXPORTS)


__all__ = [
    "BacktestParams",
    "BacktestResult",
    "BacktestTrade",
    "ExitMode",
    "build_entry_controls",
    "compute_regime_inputs",
    "filter_signals",
    "simulate_breakout_backtest",
    "simulate_portfolio_python",
]

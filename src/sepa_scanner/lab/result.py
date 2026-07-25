"""The single result shape both engines produce."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from sepa_scanner.lab.contracts import StrategyKind


@dataclass(slots=True)
class RunMetrics:
    """Comparable across strategies because it is derived from the equity curve.

    Trade-derived fields are ``None`` for REBALANCE runs, where a "trade" with a
    stop and an R-multiple does not exist.
    """

    cagr_pct: float
    total_return_pct: float
    terminal_equity: float
    max_drawdown_pct: float
    max_drawdown_days: int
    calmar: float | None
    sharpe: float | None
    sortino: float | None
    volatility_pct: float
    exposure_pct: float | None = None
    benchmark_cagr_pct: float | None = None
    excess_cagr_pct: float | None = None

    trades: int | None = None
    win_rate: float | None = None
    avg_r: float | None = None
    expectancy_r: float | None = None
    payoff_ratio: float | None = None
    avg_hold_days: float | None = None
    # Traded value as a percentage of starting capital. Meaningful for both
    # kinds, and the main cost signal for a rebalance strategy.
    turnover_pct: float | None = None


@dataclass(slots=True)
class EquityPoint:
    date: date
    equity: float
    drawdown_pct: float
    benchmark_equity: float | None = None


@dataclass(slots=True)
class TradeRecord:
    """Superset of a trade and a rebalance order; unused fields stay None."""

    symbol: str
    direction: str = "long"
    signal_date: date | None = None
    entry_date: date | None = None
    exit_date: date | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    shares: float | None = None
    stop: float | None = None
    r_multiple: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    hold_days: int | None = None
    exit_reason: str | None = None
    status: str = "closed"
    sector: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BacktestRunResult:
    run_id: str
    strategy_id: str
    kind: StrategyKind
    params: dict[str, Any]
    params_hash: str
    start_date: date
    end_date: date
    data_source: str
    metrics: RunMetrics
    equity_curve: list[EquityPoint] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return json.loads(json.dumps(payload, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, StrategyKind):
        return value.value
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")

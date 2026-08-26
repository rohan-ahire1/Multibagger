"""Run orchestration: resolve params, generate intent, simulate, summarise."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from sepa_scanner.lab.contracts import (
    DECISION_REQUIRED_COLUMNS,
    SIGNAL_REQUIRED_COLUMNS,
    DataRequirement,
    WEIGHT_REQUIRED_COLUMNS,
    Strategy,
    StrategyContext,
    StrategyKind,
)
from sepa_scanner.lab.engines.portfolio_order_engine import run_portfolio_order_engine
from sepa_scanner.lab.engines.reversion_engine import (
    reversion_daily_equity,
    reversion_trade_records,
    run_reversion_engine,
)
from sepa_scanner.lab.engines.rebalance_engine import (
    orders_to_trade_records,
    run_rebalance_engine,
    turnover_pct,
)
from sepa_scanner.lab.engines.signal_engine import (
    count_entries_in_window,
    run_signal_engine,
    to_trade_records,
)
from sepa_scanner.lab.metrics import build_equity_points, summarize
from sepa_scanner.lab.panel import (
    DataBundle,
    detect_price_discontinuities,
    load_bundle,
    load_panel_bundle,
    nifty500_tri,
)
from sepa_scanner.lab.registry import get_strategy, resolve_params
from sepa_scanner.lab.result import BacktestRunResult


def params_hash(strategy_id: str, params: dict[str, Any], data_source: str) -> str:
    payload = json.dumps(
        {"strategy": strategy_id, "params": params, "data_source": data_source},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def validate_intent(frame: pl.DataFrame, kind: StrategyKind, strategy_id: str) -> None:
    """Fail with a clear authoring error rather than deep inside the simulator."""
    signal_like = kind in (StrategyKind.SIGNAL, StrategyKind.REVERSION)
    portfolio_like = kind is StrategyKind.PORTFOLIO
    required = SIGNAL_REQUIRED_COLUMNS if signal_like else DECISION_REQUIRED_COLUMNS if portfolio_like else WEIGHT_REQUIRED_COLUMNS
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(
            f"strategy {strategy_id!r} produced a frame missing required "
            f"column(s) {missing}; got {list(frame.columns)}"
        )

    if frame.is_empty():
        raise ValueError(f"strategy {strategy_id!r} produced no rows for this window")

    if signal_like:
        duplicates = frame.group_by(["symbol", "signal_date"]).len().filter(pl.col("len") > 1)
        if not duplicates.is_empty():
            raise ValueError(
                f"strategy {strategy_id!r} produced {duplicates.height} duplicate "
                "(symbol, signal_date) rows"
            )
    elif portfolio_like:
        invalid = frame.filter(
            (pl.col("rank_pct") <= 0) | (pl.col("rank_pct") > 1) | (pl.col("atr20") <= 0)
        )
        if not invalid.is_empty():
            raise ValueError(f"strategy {strategy_id!r} produced invalid portfolio rank_pct or atr20 values")
        for _, decision in frame.group_by("decision_date", maintain_order=True):
            ranks = sorted(int(rank) for rank in decision["rank"].to_list())
            if ranks != list(range(1, len(ranks) + 1)):
                raise ValueError(f"strategy {strategy_id!r} ranks must be dense unique 1-based per decision_date")
    else:
        per_date = frame.group_by("rebalance_date").agg(pl.col("weight").sum().alias("total"))
        overweight = per_date.filter(pl.col("total") > 1.0 + 1e-6)
        if not overweight.is_empty():
            raise ValueError(
                f"strategy {strategy_id!r} allocates more than 100% on "
                f"{overweight.height} rebalance date(s)"
            )


def run_backtest(
    strategy_id: str,
    start_date: date,
    end_date: date,
    *,
    params: dict[str, Any] | None = None,
    bundle: DataBundle | None = None,
    run_id: str | None = None,
) -> BacktestRunResult:
    strategy: Strategy = get_strategy(strategy_id)

    if bundle is None:
        requirement = getattr(strategy, "data_requirement", DataRequirement.PANEL)
        loader = (
            load_bundle
            if requirement is DataRequirement.SIGNALS
            else load_panel_bundle
        )
        bundle = loader(start_date, end_date)

    resolved = resolve_params(strategy, bundle.settings, params)

    ctx = StrategyContext(
        settings=bundle.settings,
        start_date=start_date,
        end_date=end_date,
        data_dir=bundle.settings.runtime.data_dir,
        sector_map=bundle.sector_map,
        panel=bundle.ohlcv,
        benchmark=bundle.benchmark,
        extras={
            "signals": bundle.signals,
            "diagnostics": bundle.diagnostics,
            "params": bundle.params,
            "portfolio": bundle.portfolio,
        },
    )

    intent = strategy.generate(ctx, resolved)
    validate_intent(intent, strategy.kind, strategy_id)

    config = strategy.engine_config(ctx, resolved)

    exposure_pct: float | None = None
    turnover: float | None = None
    portfolio_metrics: dict[str, float | None] = {}

    if strategy.kind is StrategyKind.SIGNAL:
        raw = run_signal_engine(
            intent,
            bundle.ohlcv,
            config["params"],
            config["portfolio"],
            bundle.sector_map,
            exit_config=config.get("exit_config"),
            exit_kwargs=config["exit_kwargs"],
            entry_gate=config.get("entry_gate"),
            entry_size_mult=config.get("entry_size_mult"),
        )
        trades_df = raw["trades"]
        records = to_trade_records(trades_df)
        daily_equity = raw["daily_equity"]
        trade_count = count_entries_in_window(trades_df, start_date, end_date)
        include_trade_stats = True
    elif strategy.kind is StrategyKind.REVERSION:
        raw = run_reversion_engine(intent, bundle.ohlcv, config)
        records = reversion_trade_records(raw)
        daily_equity = reversion_daily_equity(raw)
        trade_count = len([r for r in records if r.entry_date is not None])
        include_trade_stats = True
    elif strategy.kind is StrategyKind.PORTFOLIO:
        raw = run_portfolio_order_engine(intent, bundle.ohlcv, config["config"])
        records = raw["trade_records"]
        daily_equity = raw["daily_equity"]
        trade_count = len(records)
        include_trade_stats = True
        orders = raw["orders"]
        initial = config["config"].initial_capital
        turnover = float(orders["notional"].sum()) / initial * 100.0 if orders.height else 0.0
        holdings = daily_equity["holdings_value"] / daily_equity["equity"]
        cash_pct = daily_equity["cash"] / daily_equity["equity"]
        position_counts = raw["positions"].group_by("date").len().rename({"len": "holdings"})
        portfolio_metrics = {
            "exposure_pct": float(holdings.mean()) * 100.0 if holdings.len() else None,
            "cash_pct": float(cash_pct.mean()) * 100.0 if cash_pct.len() else None,
            "avg_holdings": float(position_counts["holdings"].mean()) if position_counts.height else 0.0,
            "avg_holding_days": (sum(t.hold_days for t in records if t.hold_days is not None) / len(records)) if records else None,
            "cost_drag_pct": raw["cost_drag"] / initial * 100.0,
            "slippage_drag_pct": raw["slippage_drag"] / initial * 100.0,
        }
    else:
        # Target weights are executed at the next open after each rebalance
        # date; there are orders rather than positions with stops, so no
        # R-multiple statistics apply.
        raw = run_rebalance_engine(
            intent,
            bundle.ohlcv,
            config,
            name=config.get("name", strategy_id),
        )
        orders = raw["orders"]
        records = orders_to_trade_records(orders)
        daily_equity = raw["daily_equity"]
        trade_count = orders.height if orders is not None else 0
        turnover = turnover_pct(orders, config["initial_capital"])
        include_trade_stats = False

    # Refuse to publish benchmark-relative metrics from a series with an
    # unadjusted corporate action: a broken benchmark turns into fabricated
    # alpha, which is worse than reporting no alpha at all.
    benchmark_source = bundle.benchmark
    breaks = detect_price_discontinuities(benchmark_source, "tri")
    if strategy.kind is StrategyKind.PORTFOLIO:
        # The benchmark series is the Nifty 500 TRI; the price index matters only
        # for the regime filter. Gate them independently.
        tri = nifty500_tri(bundle.settings.runtime.data_dir, start_date, end_date)
        tri_complete = not tri.is_empty() and tri["date"].min() <= start_date and tri["date"].max() >= end_date
        benchmark_source = tri if tri_complete else pl.DataFrame()
        breaks = detect_price_discontinuities(benchmark_source, "tri")
    benchmark_for_metrics = None if breaks or benchmark_source.is_empty() else benchmark_source

    metrics = summarize(
        daily_equity,
        records,
        start_date,
        end_date,
        benchmark=benchmark_for_metrics,
        exposure_pct=portfolio_metrics.get("exposure_pct", exposure_pct),
        include_trade_stats=include_trade_stats,
    )
    # The signal simulator manages open positions past `end_date`, so the
    # headline count must be entries inside the window, matching the harness.
    metrics.trades = trade_count
    if turnover is not None:
        metrics.turnover_pct = turnover
    for name, value in portfolio_metrics.items():
        setattr(metrics, name, value)

    warnings: list[str] = []
    if breaks:
        warnings.append(
            f"benchmark {bundle.settings.market.benchmark_symbol} has "
            f"{len(breaks)} unadjusted price discontinuit(ies) "
            f"(first: {breaks[0]}); benchmark-relative metrics suppressed"
        )
    elif strategy.kind is StrategyKind.PORTFOLIO and benchmark_for_metrics is None:
        warnings.append("Nifty 500 TRI does not cover the run window; benchmark-relative metrics suppressed")
    if config.get("regime_source") == "nifty500_tri":
        warnings.append(
            "regime filter uses the Nifty 500 TRI, not the price index the spec "
            "requires; TRI drifts above price by reinvested dividends, so the "
            "SMA200 filter turns bullish marginally earlier"
        )
    if resolved.get("cash_yield_apy"):
        warnings.append(
            f"cash_yield_apy={resolved['cash_yield_apy']} is assumed income on idle "
            "cash, not strategy alpha; exclude it from headline figures"
        )

    return BacktestRunResult(
        run_id=run_id or uuid.uuid4().hex[:16],
        strategy_id=strategy_id,
        kind=strategy.kind,
        params=resolved,
        params_hash=params_hash(strategy_id, resolved, bundle.data_source),
        start_date=start_date,
        end_date=end_date,
        data_source=bundle.data_source,
        metrics=metrics,
        equity_curve=build_equity_points(
            daily_equity, start_date, end_date, benchmark_for_metrics
        ),
        trades=records,
        extras={
            "intent_rows": intent.height,
            "open_positions": len(raw.get("open_positions", []) or []),
            "rejections": raw.get("rejections", pl.DataFrame()).to_dicts() if strategy.kind is StrategyKind.PORTFOLIO else [],
            "delisted_assumed_terminal_count": len([r for r in raw.get("rejections", pl.DataFrame()).to_dicts() if r.get("reason") == "delisted_assumed_terminal"]) if strategy.kind is StrategyKind.PORTFOLIO else 0,
            "delisted_assumed_terminal_pnl_impact": raw.get("delisted_pnl_impact") if strategy.kind is StrategyKind.PORTFOLIO else None,
        },
        warnings=warnings,
        created_at=datetime.now(UTC),
    )

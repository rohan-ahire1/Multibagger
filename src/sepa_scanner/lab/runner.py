"""Run orchestration: resolve params, generate intent, simulate, summarise."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from sepa_scanner.lab.contracts import (
    SIGNAL_REQUIRED_COLUMNS,
    DataRequirement,
    WEIGHT_REQUIRED_COLUMNS,
    Strategy,
    StrategyContext,
    StrategyKind,
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
    required = (
        SIGNAL_REQUIRED_COLUMNS if kind is StrategyKind.SIGNAL else WEIGHT_REQUIRED_COLUMNS
    )
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(
            f"strategy {strategy_id!r} produced a frame missing required "
            f"column(s) {missing}; got {list(frame.columns)}"
        )

    if frame.is_empty():
        raise ValueError(f"strategy {strategy_id!r} produced no rows for this window")

    if kind is StrategyKind.SIGNAL:
        duplicates = frame.group_by(["symbol", "signal_date"]).len().filter(pl.col("len") > 1)
        if not duplicates.is_empty():
            raise ValueError(
                f"strategy {strategy_id!r} produced {duplicates.height} duplicate "
                "(symbol, signal_date) rows"
            )
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

    if strategy.kind is not StrategyKind.SIGNAL:
        raise NotImplementedError(
            "the rebalance engine is not wired yet; only SIGNAL strategies run today"
        )

    config = strategy.engine_config(ctx, resolved)
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

    # Refuse to publish benchmark-relative metrics from a series with an
    # unadjusted corporate action: a broken benchmark turns into fabricated
    # alpha, which is worse than reporting no alpha at all.
    breaks = detect_price_discontinuities(bundle.benchmark, "tri")
    benchmark_for_metrics = None if breaks else bundle.benchmark

    metrics = summarize(
        daily_equity,
        records,
        start_date,
        end_date,
        benchmark=benchmark_for_metrics,
    )
    # The simulator manages open positions past `end_date`, so the headline
    # trade count must be entries inside the window, matching the harness.
    metrics.trades = count_entries_in_window(trades_df, start_date, end_date)

    warnings: list[str] = []
    if breaks:
        warnings.append(
            f"benchmark {bundle.settings.market.benchmark_symbol} has "
            f"{len(breaks)} unadjusted price discontinuit(ies) "
            f"(first: {breaks[0]}); benchmark-relative metrics suppressed"
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
            "signals_after_filter": intent.height,
            "open_positions": len(raw.get("open_positions", [])),
        },
        warnings=warnings,
        created_at=datetime.now(UTC),
    )

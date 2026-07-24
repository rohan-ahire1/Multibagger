"""Adaptive OHLCV momentum strategy backed by the Rust portfolio engine.

This is an experimental v2 companion to ``calmar_momentum_strategy.py``.  It
keeps the same causal signal timing, adjusted daily OHLCV source, friction
model, and Rust execution engine, but adds two portfolio-quality controls:

* only leaders at least ``min_distance_from_low`` above their trailing
  252-session low are eligible;
* open risk is split into three rolling-beta buckets (low, medium, high) and
  the Rust sector-cap machinery limits concentration in any one bucket.

The buckets are derived from price history, not imported sector labels.  A
63-session rolling beta is calculated only from information available at the
signal close, using the cleaned NIFTYBEES return as the market proxy.  This is
an exposure-control heuristic, not a claim that beta buckets are industries.

The file deliberately does not change the production configuration.  It
reuses the audited data loading, feature, signal, Rust adapter, and equity
reconstruction helpers from the baseline experimental file so the two runs
remain directly comparable.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

import multibagger_core

try:  # Supports both ``python rust_core/...py`` and package imports.
    from . import calmar_momentum_strategy as core
except ImportError:  # pragma: no cover - exercised by the direct CLI path
    import calmar_momentum_strategy as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_START = core.DEFAULT_START
INITIAL_CAPITAL = core.INITIAL_CAPITAL


@dataclass(frozen=True)
class AdaptiveConfig:
    """Causal v2 controls layered over the baseline strategy."""

    min_distance_from_low: float = 0.75
    beta_lookback: int = 63
    beta_low: float = 0.70
    beta_high: float = 1.30
    max_bucket_positions: int = 4
    max_bucket_pct: float = 0.40


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _beta_frame(
    ohlcv: pl.DataFrame,
    symbols: set[str],
    lookback: int,
) -> pl.DataFrame:
    """Return causal rolling beta values for the requested symbols."""

    benchmark, _ = core._clean_benchmark_close(
        ohlcv.filter(pl.col("symbol") == "NIFTYBEES").select("date", "close")
    )
    benchmark_returns = benchmark.with_columns(
        (pl.col("clean_close") / pl.col("clean_close").shift(1) - 1.0).alias(
            "benchmark_return"
        )
    ).select("date", "benchmark_return")

    panel = (
        ohlcv.filter(pl.col("symbol").is_in(sorted(symbols)))
        .select("date", "symbol", "close")
        .sort(["symbol", "date"])
        .with_columns(
            (
                pl.col("close")
                / pl.col("close").shift(1).over("symbol")
                - 1.0
            ).alias("stock_return")
        )
        .join(benchmark_returns, on="date", how="left")
        .with_columns(
            [
                pl.col("stock_return")
                .rolling_mean(lookback)
                .over("symbol")
                .alias("stock_mean"),
                pl.col("benchmark_return")
                .rolling_mean(lookback)
                .over("symbol")
                .alias("benchmark_mean"),
                (pl.col("stock_return") * pl.col("benchmark_return"))
                .rolling_mean(lookback)
                .over("symbol")
                .alias("cross_mean"),
                (pl.col("benchmark_return") * pl.col("benchmark_return"))
                .rolling_mean(lookback)
                .over("symbol")
                .alias("benchmark_square_mean"),
            ]
        )
        .with_columns(
            [
                (
                    pl.col("cross_mean")
                    - pl.col("stock_mean") * pl.col("benchmark_mean")
                ).alias("covariance"),
                (
                    pl.col("benchmark_square_mean")
                    - pl.col("benchmark_mean") * pl.col("benchmark_mean")
                ).alias("benchmark_variance"),
            ]
        )
        .with_columns(
            (pl.col("covariance") / pl.col("benchmark_variance")).alias(
                "beta"
            )
        )
    )
    return panel.select("date", "symbol", "beta")


def _bucket(value: Any, cfg: AdaptiveConfig) -> str:
    """Map a rolling beta to a Rust sector-cap bucket."""

    try:
        beta = float(value)
    except (TypeError, ValueError):
        beta = 1.0
    if not math.isfinite(beta):
        beta = 1.0
    if beta < cfg.beta_low:
        return "LOW_BETA"
    if beta < cfg.beta_high:
        return "MID_BETA"
    return "HIGH_BETA"


def _build_adaptive_signals(
    ohlcv: pl.DataFrame,
    features: pl.DataFrame,
    raw_signals: list[dict[str, Any]],
    source_dates: dict[str, str],
    cfg: AdaptiveConfig,
) -> list[dict[str, Any]]:
    """Apply the quality screen and attach causal beta bucket labels."""

    quality = {
        (row["date"], str(row["symbol"])): row["distance_from_low"]
        for row in features.select(
            "date", "symbol", "distance_from_low"
        ).iter_rows(named=True)
    }
    signal_symbols = {str(signal["symbol"]) for signal in raw_signals}
    beta = _beta_frame(ohlcv, signal_symbols | {"NIFTYBEES"}, cfg.beta_lookback)
    beta_lookup = {
        (row["date"], str(row["symbol"])): row["beta"]
        for row in beta.iter_rows(named=True)
    }

    selected: list[dict[str, Any]] = []
    for signal in raw_signals:
        symbol = str(signal["symbol"])
        key = f"{symbol}|{signal['signal_date']}"
        source_date = date.fromisoformat(source_dates[key])
        distance = quality.get((source_date, symbol))
        if distance is None:
            continue
        try:
            distance_value = float(distance)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(distance_value) or distance_value < cfg.min_distance_from_low:
            continue

        adapted = dict(signal)
        adapted["sector"] = _bucket(
            beta_lookup.get((source_date, symbol)), cfg
        )
        selected.append(adapted)
    return selected


def run(
    *,
    start: date = DEFAULT_START,
    end: date | None = None,
    strategy_cfg: core.StrategyConfig = core.StrategyConfig(),
    adaptive_cfg: AdaptiveConfig = AdaptiveConfig(),
    write_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the adaptive strategy and return an auditable summary."""

    ohlcv, invalid_rows = core.load_ohlcv()
    latest = ohlcv["date"].max()
    requested_end = end or latest
    requested_end = min(requested_end, latest)
    if requested_end <= start:
        raise ValueError("end must be after start")

    features = core.build_features(ohlcv, strategy_cfg)
    raw_signals, source_dates = core.generate_signals(
        features, strategy_cfg, start, requested_end
    )
    signals = _build_adaptive_signals(
        ohlcv, features, raw_signals, source_dates, adaptive_cfg
    )
    if not signals:
        raise RuntimeError("No signals survived the adaptive quality filters")

    symbols = {str(signal["symbol"]) for signal in signals} | {"NIFTYBEES"}
    (
        engine_dates,
        opens,
        highs,
        lows,
        closes,
        symbol_indices,
        engine_frame,
    ) = core._engine_arrays(ohlcv, symbols, start, requested_end)
    params, portfolio = core._rust_params(strategy_cfg)
    portfolio["max_sector_positions"] = adaptive_cfg.max_bucket_positions
    portfolio["max_sector_pct"] = adaptive_cfg.max_bucket_pct

    result = json.loads(
        multibagger_core.run_portfolio_backtest_rust(
            json.dumps(signals),
            json.dumps(params),
            json.dumps(portfolio),
            engine_dates,
            opens,
            highs,
            lows,
            closes,
            symbol_indices,
        )
    )
    curve, inferred_trades = core.reconstruct_equity(
        result["trades"], engine_frame, start, requested_end
    )
    metrics = core.equity_metrics(curve)
    benchmark = core.benchmark_metrics(engine_frame, start, requested_end)
    filled = [trade for trade in result["trades"] if trade.get("entry_date")]
    status_counts: dict[str, int] = {}
    for trade in result["trades"]:
        status = str(trade["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    summary: dict[str, Any] = {
        "strategy": "calmar_adaptive_beta_bucket_v2",
        "rust_core": multibagger_core.health_check(),
        "data": {
            "path": str(core.OHLCV_PATH),
            "rows": ohlcv.height,
            "invalid_rows_excluded": invalid_rows,
            "symbols": ohlcv["symbol"].n_unique(),
            "available_start": ohlcv["date"].min().isoformat(),
            "available_end": latest.isoformat(),
        },
        "execution": {
            "signal_is_after_close": True,
            "execution": "next_global_session_open",
            "friction": "Rust fixed slippage/statutory charge model",
            "rust_final_equity": float(result["final_equity"]),
            "reconstructed_final_equity": metrics["final_equity"],
            "terminal_difference_pct": (
                metrics["final_equity"] / float(result["final_equity"]) - 1.0
            )
            * 100.0,
        },
        "metrics": metrics,
        "benchmark": benchmark,
        "raw_signals": len(raw_signals),
        "signals": len(signals),
        "filled_trades": len(filled),
        "inferred_trades": inferred_trades,
        "status_counts": status_counts,
        "baseline_config": asdict(strategy_cfg),
        "adaptive_config": asdict(adaptive_cfg),
    }

    if write_dir is not None:
        write_dir.mkdir(parents=True, exist_ok=True)
        (write_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        curve.write_csv(write_dir / "daily_equity.csv")
        (write_dir / "trades.json").write_text(
            json.dumps(result["trades"], indent=2, default=str), encoding="utf-8"
        )
        pl.DataFrame(signals).write_csv(write_dir / "signals.csv")

    print(json.dumps(summary, indent=2, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--risk", type=float, default=None)
    parser.add_argument("--min-distance-from-low", type=float, default=0.75)
    parser.add_argument("--beta-low", type=float, default=0.70)
    parser.add_argument("--beta-high", type=float, default=1.30)
    parser.add_argument("--max-bucket-positions", type=int, default=4)
    parser.add_argument("--max-bucket-pct", type=float, default=0.40)
    parser.add_argument("--write-dir", type=Path, default=None)
    args = parser.parse_args()

    strategy_cfg = core.StrategyConfig(
        risk_per_trade_pct=(
            args.risk
            if args.risk is not None
            else core.StrategyConfig().risk_per_trade_pct
        )
    )
    adaptive_cfg = AdaptiveConfig(
        min_distance_from_low=args.min_distance_from_low,
        beta_low=args.beta_low,
        beta_high=args.beta_high,
        max_bucket_positions=args.max_bucket_positions,
        max_bucket_pct=args.max_bucket_pct,
    )
    run(
        start=args.start,
        end=args.end,
        strategy_cfg=strategy_cfg,
        adaptive_cfg=adaptive_cfg,
        write_dir=args.write_dir,
    )


if __name__ == "__main__":
    main()

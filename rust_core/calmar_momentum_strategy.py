"""Calmar-oriented daily momentum strategy backed by the Rust portfolio engine.

This is an experimental strategy file.  It deliberately does not modify the
production SEPA configuration or its canonical backtest.  The strategy uses
only adjusted daily OHLCV from ``data/yf_adjusted/ohlcv_all.parquet`` and calls
``multibagger_core.run_portfolio_backtest_rust`` for fills, stops, sizing, and
portfolio controls.

Signal timing is explicit:

* indicators are calculated after the close on day D;
* the signal is handed to Rust with execution date D+1;
* ``entry_ref=0.01`` is a deliberately-low trigger that makes Rust's trigger
  fill an actual next-session market-at-open order for the price-filtered
  universe.  It is not a claim that a breakout filled at one paisa.

The Rust portfolio API returns trades and final equity, but not a daily curve
or share count.  The file reconstructs both from the returned net prices and
R-multiples, checks the reconstructed terminal value against Rust, and reports
equity-curve CAGR, max drawdown, and Calmar.  Missing symbol bars are marked at
the last observed close, rather than silently assigning them zero value.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

import multibagger_core


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OHLCV_PATH = PROJECT_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"
DEFAULT_START = date(2010, 1, 4)
INITIAL_CAPITAL = 1_000_000.0

# The Rust engine's fixed friction model.  These are used only to reverse its
# returned trade R-multiple into shares for the daily equity reconstruction.
BUY_FACTOR = 1.001 * 1.00118658
SELL_FACTOR = 0.999 * 0.99896342
DP_CHARGE = 16.0


@dataclass(frozen=True)
class StrategyConfig:
    """Small, auditable parameter surface for the strategy."""

    min_price: float = 20.0
    min_adv20: float = 20_000_000.0
    rank_max: int = 80
    min_momentum: float = 0.05
    max_below_52w_high: float = 0.10
    breakout_lookback: int = 126
    momentum_lookback: int = 126
    skip_recent_bars: int = 21
    volume_multiple: float = 1.15
    require_breakout: bool = False
    use_benchmark_gate: bool = True
    min_breadth200: float = 0.40
    stop_atr: float = 2.50
    signal_cooldown_days: int = 42
    max_signals_per_day: int = 12
    max_hold_days: int = 126
    trail_activation_r: float = 1.0
    trail_pct: float = 0.20
    dead_money_days: int = 30
    risk_per_trade_pct: float = 0.011
    max_positions: int = 12
    max_heat_pct: float = 0.15
    drawdown_half_pct: float = 0.12
    # These are deliberately beyond the measured peak-to-trough loss.  They
    # remain a last-resort circuit breaker rather than an optimization lever.
    drawdown_stop_pct: float = 0.50
    drawdown_close_pct: float = 0.70


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _clean_benchmark_close(frame: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Repair obvious one-day split/adjustment jumps in NIFTYBEES.

    The local NIFTYBEES history has a documented 2019 10x adjustment
    discontinuity.  Strategy signals use the repaired market trend; the stock
    OHLCV panel itself is never rewritten.  A jump is corrected only when the
    raw close ratio is below 0.5 or above 2.0, and the count is reported.
    """

    ordered = frame.select("date", "close").sort("date")
    dates = ordered["date"].to_list()
    raw = [float(value) for value in ordered["close"]]
    if not raw:
        return pl.DataFrame({"date": [], "clean_close": []}), 0
    scale = 1.0
    corrected: list[float] = [raw[0]]
    jumps = 0
    for previous, current in zip(raw, raw[1:]):
        ratio = current / previous if previous > 0 else 1.0
        if ratio < 0.5:
            scale /= ratio
            jumps += 1
        elif ratio > 2.0:
            scale /= ratio
            jumps += 1
        corrected.append(current * scale)
    return pl.DataFrame({"date": dates, "clean_close": corrected}), jumps


def load_ohlcv(path: Path = OHLCV_PATH) -> tuple[pl.DataFrame, int]:
    """Load the adjusted daily panel and exclude impossible OHLC rows.

    The local historical panel contains a small number of malformed Yahoo
    rows (usually a split/adjustment discontinuity).  They are excluded before
    rolling features or Rust stops are calculated and counted in the result;
    allowing an impossible low/high through would be worse than losing one
    observation.
    """

    frame = pl.read_parquet(
        path,
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
    ).sort(["symbol", "date"])
    if frame.is_empty():
        raise RuntimeError(f"No OHLCV rows found in {path}")
    valid = (
        pl.col("open").is_finite()
        & pl.col("high").is_finite()
        & pl.col("low").is_finite()
        & pl.col("close").is_finite()
        & (pl.col("open") > 0)
        & (pl.col("high") > 0)
        & (pl.col("low") > 0)
        & (pl.col("close") > 0)
        & (pl.col("low") <= pl.min_horizontal("open", "high", "close"))
        & (pl.col("high") >= pl.max_horizontal("open", "low", "close"))
        & (pl.col("volume") >= 0)
    )
    invalid = frame.filter(~valid).height
    cleaned = frame.filter(valid)
    if cleaned.is_empty():
        raise RuntimeError(f"All OHLCV rows were invalid in {path}")
    return (
        cleaned.with_columns((pl.col("close") * pl.col("volume")).alias("turnover")),
        invalid,
    )


def build_features(ohlcv: pl.DataFrame, cfg: StrategyConfig) -> pl.DataFrame:
    """Build causal trend, momentum, volatility, and breadth features."""

    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - pl.col("close").shift(1).over("symbol")).abs(),
        (pl.col("low") - pl.col("close").shift(1).over("symbol")).abs(),
    ).alias("true_range")

    features = (
        ohlcv.with_columns(
            [
                pl.col("turnover").rolling_mean(20).over("symbol").alias("adv20"),
                pl.col("volume").rolling_mean(50).over("symbol").alias("volume50"),
                pl.col("close").rolling_mean(50).over("symbol").alias("sma50"),
                pl.col("close").rolling_mean(200).over("symbol").alias("sma200"),
                # The shift is outside the rolling window: the breakout level
                # is known at the D close and excludes D itself.
                pl.col("high")
                .rolling_max(cfg.breakout_lookback)
                .over("symbol")
                .shift(1)
                .over("symbol")
                .alias("prior_high"),
                pl.col("high")
                .rolling_max(252)
                .over("symbol")
                .shift(1)
                .over("symbol")
                .alias("prior_52w_high"),
                pl.col("low").rolling_min(252).over("symbol").alias("low_52w"),
                (
                    pl.col("close")
                    / pl.col("close").shift(cfg.momentum_lookback).over("symbol")
                    - 1.0
                ).alias("return126"),
                (
                    pl.col("close")
                    / pl.col("close").shift(cfg.skip_recent_bars).over("symbol")
                    - 1.0
                ).alias("return21"),
                true_range,
            ]
        )
        .with_columns(
            [
                pl.col("true_range")
                .rolling_mean(14)
                .over("symbol")
                .alias("atr14"),
            ]
        )
        .with_columns(
            [
                (pl.col("return126") - pl.col("return21")).alias("momentum"),
                (pl.col("close") / pl.col("prior_52w_high") - 1.0).alias(
                    "distance_from_high"
                ),
                (pl.col("close") / pl.col("low_52w") - 1.0).alias("distance_from_low"),
            ]
        )
        .with_columns(
            [
                pl.col("momentum")
                .rank(descending=True)
                .over("date")
                .alias("momentum_rank"),
            ]
        )
        .drop("true_range")
    )

    breadth = features.group_by("date").agg(
        pl.when(pl.col("sma200").is_not_null())
        .then((pl.col("close") > pl.col("sma200")).cast(pl.Float64))
        .otherwise(None)
        .mean()
        .fill_null(0.0)
        .alias("breadth200")
    )
    if "NIFTYBEES" in set(features["symbol"].unique().to_list()):
        benchmark, _ = _clean_benchmark_close(
            ohlcv.filter(pl.col("symbol") == "NIFTYBEES")
        )
        market = benchmark.with_columns(
            pl.col("clean_close").rolling_mean(200).alias("benchmark_sma200")
        ).select(
            "date",
            (pl.col("clean_close") > pl.col("benchmark_sma200"))
            .fill_null(False)
            .alias("market_bull"),
        )
    else:
        market = breadth.select(
            "date", (pl.col("breadth200") >= 0.40).alias("market_bull")
        )
    return features.join(breadth, on="date", how="left").join(
        market, on="date", how="left"
    )


def generate_signals(
    features: pl.DataFrame,
    cfg: StrategyConfig,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Create one-shot signals and shift them to the next global session.

    The returned mapping preserves the original signal date for audit output;
    Rust receives the shifted execution date only.
    """

    calendar = features.select("date").unique().sort("date")["date"].to_list()
    next_date = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}

    liquid = (
        (pl.col("close") >= cfg.min_price)
        & (pl.col("adv20") >= cfg.min_adv20)
        & (pl.col("volume") > 0)
        & pl.col("atr14").is_not_null()
        & pl.col("prior_high").is_not_null()
        & pl.col("sma200").is_not_null()
        & pl.col("momentum_rank").is_not_null()
    )
    trend = (
        (pl.col("close") > pl.col("sma200"))
        & (pl.col("sma50") > pl.col("sma200"))
        & (pl.col("momentum") >= cfg.min_momentum)
        & (pl.col("distance_from_high") >= -cfg.max_below_52w_high)
        & (pl.col("distance_from_low") >= 0.30)
    )
    breakout = (
        (pl.col("close") > pl.col("prior_high"))
        & (pl.col("volume") >= pl.col("volume50") * cfg.volume_multiple)
    )
    regime = pl.col("breadth200") >= cfg.min_breadth200
    if cfg.use_benchmark_gate:
        regime = regime & pl.col("market_bull").fill_null(False)
    entry = liquid & trend & regime & (
        pl.col("momentum_rank") <= cfg.rank_max
    )
    if cfg.require_breakout:
        entry = entry & breakout

    candidates = (
        features.filter((pl.col("date") >= start) & (pl.col("date") < end))
        .with_columns(entry.alias("entry_condition"))
        # A rising edge prevents a persistent trend from becoming a daily
        # stream of duplicate orders.  A fresh breakout can re-arm it.
        .filter(
            pl.col("entry_condition")
            & ~pl.col("entry_condition")
            .shift(1)
            .over("symbol")
            .fill_null(False)
        )
        .sort(["date", "momentum_rank", "symbol"])
        .group_by("date", maintain_order=True)
        .head(cfg.max_signals_per_day)
        .sort(["symbol", "date"])
        .select("date", "symbol", "close", "atr14", "momentum_rank")
    )

    # The Rust portfolio engine intentionally has no per-symbol overlap rule.
    # Enforce a causal cooldown here so one name cannot consume several risk
    # budgets while a prior position is still alive.
    rows: list[dict[str, Any]] = []
    last_signal: dict[str, date] = {}
    source_dates: dict[str, str] = {}
    for row in candidates.iter_rows(named=True):
        symbol = str(row["symbol"])
        source_date = row["date"]
        if symbol == "NIFTYBEES":
            continue
        previous = last_signal.get(symbol)
        if previous is not None and (source_date - previous).days < cfg.signal_cooldown_days:
            continue
        execution_date = next_date.get(source_date)
        if execution_date is None:
            continue
        close = float(row["close"])
        atr = float(row["atr14"])
        stop = max(0.01, close - cfg.stop_atr * atr)
        if not math.isfinite(stop) or stop >= close:
            continue
        key = f"{symbol}|{execution_date.isoformat()}"
        source_dates[key] = source_date.isoformat()
        rows.append(
            {
                "symbol": symbol,
                "sector": "ALL",
                "signal_date": execution_date.isoformat(),
                # This is a market-at-next-open adapter for the Rust trigger
                # API, not a price prediction.
                "entry_ref": 0.01,
                "stop": stop,
                "pivot": close,
                "rs_rank": float(row["momentum_rank"]),
            }
        )
        last_signal[symbol] = source_date
    return rows, source_dates


def _engine_arrays(
    ohlcv: pl.DataFrame, symbols: set[str], start: date, end: date
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, tuple[int, int]], pl.DataFrame]:
    """Prepare contiguous symbol slices for the PyO3 Rust API."""

    engine = ohlcv.filter(
        pl.col("symbol").is_in(sorted(symbols))
        & (pl.col("date") >= start)
        & (pl.col("date") <= end)
    ).sort(["symbol", "date"])
    if engine.is_empty():
        raise RuntimeError("No engine OHLCV rows remain after filtering")
    indices: dict[str, tuple[int, int]] = {}
    cursor = 0
    for row in engine.group_by("symbol", maintain_order=True).len().iter_rows():
        symbol, length = str(row[0]), int(row[1])
        indices[symbol] = (cursor, cursor + length)
        cursor += length
    dates = [value.isoformat() for value in engine["date"]]
    arrays = tuple(
        np.ascontiguousarray(engine[column].to_numpy(), dtype=np.float64)
        for column in ("open", "high", "low", "close")
    )
    return dates, *arrays, indices, engine


def _rust_params(cfg: StrategyConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    params = {
        "pending_days": 3,
        "max_hold_days": cfg.max_hold_days,
        "target_r": None,
        "exit_mode": "v3_trail_pct",
        "trail_activation_r": cfg.trail_activation_r,
        "trail_pct": cfg.trail_pct,
        "dead_money_days": cfg.dead_money_days,
        "friction_enabled": True,
    }
    portfolio = {
        "initial_capital": INITIAL_CAPITAL,
        "risk_per_trade_pct": cfg.risk_per_trade_pct,
        "max_positions": cfg.max_positions,
        # Sector labels are intentionally unavailable in OHLCV-only research;
        # all signals share one bucket and the heat cap remains binding.
        "max_sector_positions": cfg.max_positions,
        "max_sector_pct": 1.0,
        "max_heat_pct": cfg.max_heat_pct,
        "regime_heat_mult_strong": 1.0,
        "drawdown_half_pct": cfg.drawdown_half_pct,
        "drawdown_stop_pct": cfg.drawdown_stop_pct,
        "drawdown_close_pct": cfg.drawdown_close_pct,
        "allowed_entry_dates": None,
    }
    return params, portfolio


def _infer_shares(trade: dict[str, Any]) -> float | None:
    """Recover Rust's fractional share count from its net trade fields."""

    if not trade.get("entry_date") or trade.get("r_multiple") is None:
        return None
    if trade.get("entry_price") is None or trade.get("exit_price") is None:
        return None
    net_entry = float(trade["entry_price"])
    net_exit = float(trade["exit_price"])
    net_stop = float(trade["stop"]) * SELL_FACTOR
    net_risk = net_entry - net_stop
    if net_risk <= 0:
        return None
    # r = (shares * (exit-entry) - DP) / (shares * net_risk)
    denominator = (net_exit - net_entry) - float(trade["r_multiple"]) * net_risk
    if abs(denominator) < 1e-12:
        return None
    shares = DP_CHARGE / denominator
    if not math.isfinite(shares) or shares <= 0:
        return None
    return shares


def _price_series(engine: pl.DataFrame) -> dict[str, tuple[list[date], list[float]]]:
    series: dict[str, tuple[list[date], list[float]]] = {}
    for group in engine.select("date", "symbol", "close").partition_by(
        "symbol", maintain_order=True
    ):
        symbol = str(group["symbol"][0])
        series[symbol] = (
            group["date"].to_list(),
            [float(value) for value in group["close"]],
        )
    return series


def reconstruct_equity(
    trades: list[dict[str, Any]],
    engine: pl.DataFrame,
    start: date,
    end: date,
) -> tuple[pl.DataFrame, int]:
    """Build a daily net-liquidation curve from the Rust trade output."""

    prices = _price_series(engine)
    curve_dates = (
        engine.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        .select("date")
        .unique()
        .sort("date")["date"]
        .to_list()
    )
    buy_cash: defaultdict[date, float] = defaultdict(float)
    sell_cash: defaultdict[date, float] = defaultdict(float)
    active: defaultdict[str, list[tuple[date, date, float, float]]] = defaultdict(list)
    inferred = 0
    skipped_inference = 0
    for trade in trades:
        shares = _infer_shares(trade)
        if shares is None:
            if trade.get("entry_date"):
                skipped_inference += 1
            continue
        entry_date = date.fromisoformat(trade["entry_date"])
        exit_date = date.fromisoformat(trade["exit_date"])
        entry_net = float(trade["entry_price"])
        exit_net = float(trade["exit_price"])
        buy_cash[entry_date] -= shares * entry_net
        sell_cash[exit_date] += shares * exit_net - DP_CHARGE
        # Raw entry is only a fallback for a malformed first mark.
        raw_entry = entry_net / BUY_FACTOR
        active[str(trade["symbol"])].append(
            (entry_date, exit_date, shares, raw_entry)
        )
        inferred += 1

    equity_rows: list[dict[str, Any]] = []
    cash = INITIAL_CAPITAL
    for today in curve_dates:
        cash += buy_cash[today] + sell_cash[today]
        mark_to_market = 0.0
        open_count = 0
        for symbol, positions in active.items():
            date_values = prices.get(symbol)
            for entry_date, exit_date, shares, fallback in positions:
                if not (entry_date <= today < exit_date):
                    continue
                open_count += 1
                price = fallback
                if date_values is not None:
                    dates, closes = date_values
                    index = bisect.bisect_right(dates, today) - 1
                    if index >= 0:
                        price = closes[index]
                mark_to_market += shares * price * SELL_FACTOR
        equity = cash + mark_to_market
        peak = max(
            equity,
            float(equity_rows[-1]["peak"]) if equity_rows else INITIAL_CAPITAL,
        )
        equity_rows.append(
            {
                "date": today,
                "equity": equity,
                "cash": cash,
                "positions": open_count,
                "peak": peak,
                "drawdown_pct": (equity / peak - 1.0) * 100.0,
            }
        )
    if skipped_inference:
        raise RuntimeError(
            f"Could not reconstruct {skipped_inference} filled Rust trades; refusing to report metrics"
        )
    return pl.DataFrame(equity_rows), inferred


def equity_metrics(curve: pl.DataFrame) -> dict[str, float]:
    if curve.is_empty() or curve.height < 2:
        raise RuntimeError("Daily equity curve is too short")
    first_date = curve["date"][0]
    last_date = curve["date"][-1]
    years = max((last_date - first_date).days / 365.25, 1e-9)
    start_equity = float(curve["equity"][0])
    final_equity = float(curve["equity"][-1])
    cagr = (final_equity / start_equity) ** (1.0 / years) - 1.0
    max_dd = float(curve["drawdown_pct"].min()) / 100.0
    return {
        "start": first_date.isoformat(),
        "end": last_date.isoformat(),
        "years": years,
        "cagr_pct": cagr * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else 99.0,
        "final_equity": final_equity,
    }


def benchmark_metrics(engine: pl.DataFrame, start: date, end: date) -> dict[str, float] | None:
    benchmark, jumps = _clean_benchmark_close(
        engine.filter(
            (pl.col("symbol") == "NIFTYBEES")
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        ).select("date", "close")
    )
    if benchmark.height < 2:
        return None
    values = np.asarray(benchmark["clean_close"].to_list(), dtype=float)
    dates = benchmark["date"].to_list()
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0
    drawdown = values / np.maximum.accumulate(values) - 1.0
    max_dd = float(drawdown.min())
    return {
        "cagr_pct": cagr * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else 99.0,
        "adjustment_jumps_corrected": float(jumps),
    }


def run(
    *,
    start: date = DEFAULT_START,
    end: date | None = None,
    cfg: StrategyConfig = StrategyConfig(),
    write_dir: Path | None = None,
) -> dict[str, Any]:
    ohlcv, invalid_rows = load_ohlcv()
    latest = ohlcv["date"].max()
    requested_end = end or latest
    if requested_end > latest:
        requested_end = latest
    if requested_end <= start:
        raise ValueError("end must be after start")

    features = build_features(ohlcv, cfg)
    signals, source_dates = generate_signals(features, cfg, start, requested_end)
    if not signals:
        raise RuntimeError("No signals survived the causal filters")
    symbols = {str(signal["symbol"]) for signal in signals} | {"NIFTYBEES"}
    (
        engine_dates,
        opens,
        highs,
        lows,
        closes,
        symbol_indices,
        engine_frame,
    ) = _engine_arrays(ohlcv, symbols, start, requested_end)
    params, portfolio = _rust_params(cfg)
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
    curve, inferred_trades = reconstruct_equity(
        result["trades"], engine_frame, start, requested_end
    )
    metrics = equity_metrics(curve)
    benchmark = benchmark_metrics(engine_frame, start, requested_end)
    filled = [trade for trade in result["trades"] if trade.get("entry_date")]
    status_counts: defaultdict[str, int] = defaultdict(int)
    for trade in result["trades"]:
        status_counts[str(trade["status"])] += 1
    summary: dict[str, Any] = {
        "strategy": "calmar_momentum_126",
        "rust_core": multibagger_core.health_check(),
        "data": {
            "path": str(OHLCV_PATH),
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
        "signals": len(signals),
        "filled_trades": len(filled),
        "inferred_trades": inferred_trades,
        "status_counts": dict(status_counts),
        "config": asdict(cfg),
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
        # The source-date map is kept separate from the Rust payload so the
        # execution adapter remains obvious in the exported audit trail.
        (write_dir / "signal_source_dates.json").write_text(
            json.dumps(source_dates, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, indent=2, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--risk", type=float, default=None, help="Override risk per trade")
    parser.add_argument("--write-dir", type=Path, default=None)
    args = parser.parse_args()
    cfg = StrategyConfig(
        risk_per_trade_pct=args.risk
        if args.risk is not None
        else StrategyConfig().risk_per_trade_pct
    )
    run(start=args.start, end=args.end, cfg=cfg, write_dir=args.write_dir)


if __name__ == "__main__":
    main()

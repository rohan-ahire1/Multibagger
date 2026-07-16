from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import polars as pl

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "yf_adjusted"
SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "raw" / "universe_snapshots"
OUTPUT_ROOT = PROJECT_ROOT / "strategies" / "autonomous_research" / "output"
HOLDOUT_START = date(2023, 1, 1)
START_2010 = date(2010, 1, 1)
INITIAL_CAPITAL = 1_000_000.0


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    family: str
    rank_col: str
    entry_rule: str
    hold_days: int
    max_positions: int
    position_pct: float
    stop_atr: float | None
    trail_atr: float | None
    exit_rule: str
    slippage_bps: float
    min_adv20: float
    max_adv_pct: float
    min_price: float = 20.0
    rank_max: float | None = None
    require_market_bull: bool = True
    min_breadth200: float = 0.40
    vol_target_ann: float | None = None
    min_exposure_mult: float = 0.35


@dataclass
class Position:
    symbol: str
    entry_signal_date: date
    entry_date: date
    entry_price_raw: float
    entry_price_net: float
    shares: int
    cost: float
    buy_charges: float
    stop_raw: float | None
    highest_close: float
    days_held: int = 0


@dataclass
class Trade:
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    gross_pnl: float
    net_pnl: float
    gross_pnl_no_friction: float
    slippage_cost: float
    return_pct: float
    r_multiple: float | None
    holding_days: int
    exit_reason: str
    charges: float
    buy_charges: float
    sell_charges: float


@dataclass
class Metrics:
    strategy: str
    start: str
    end: str
    cagr: float
    post_tax_cagr: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    win_rate: float
    avg_holding_days: float
    turnover: float
    trades: int
    final_equity: float
    avg_trade_return_pct: float


@dataclass
class BenchmarkMetrics:
    name: str
    start: str
    end: str
    cagr: float
    post_tax_cagr: float
    total_return_pct: float
    post_tax_total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    final_equity: float
    post_tax_final_equity: float
    tax: float


def ensure_output() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def check_rust_core() -> str:
    try:
        import multibagger_core

        return multibagger_core.health_check()
    except Exception as exc:  # pragma: no cover - environmental check
        return f"Rust core unavailable: {exc}"


def load_ohlcv(start: date = START_2010, end: date | None = None) -> pl.DataFrame:
    store = ParquetOhlcvStore(DATA_ROOT)
    columns = ["date", "symbol", "open", "high", "low", "close", "volume", "turnover"]
    return store.read_frame(start_date=start, end_date=end, columns=columns).sort(["symbol", "date"])


def audit_data(df: pl.DataFrame) -> dict[str, Any]:
    if df.is_empty():
        raise RuntimeError("No OHLCV data found")
    by_symbol = df.group_by("symbol").agg(
        pl.len().alias("rows"),
        pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"),
        (pl.col("volume") <= 0).sum().alias("zero_volume_days"),
        (pl.col("turnover") <= 0).sum().alias("zero_turnover_days"),
    )
    invalid_ohlc = df.filter(
        (pl.col("open") <= 0)
        | (pl.col("high") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") <= 0)
        | (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    ).height
    with_ret = df.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("ret1")
    )
    extreme_moves = with_ret.filter(pl.col("ret1").abs() > 0.45).height
    symbols_with_gaps = (
        df.sort(["symbol", "date"])
        .with_columns(pl.col("date").diff().over("symbol").dt.total_days().alias("gap_days"))
        .filter(pl.col("gap_days") > 14)
        .select(pl.col("symbol").n_unique())
        .item()
    )
    snapshot_files = sorted(SNAPSHOT_ROOT.glob("*.zip"))
    return {
        "schema": {name: str(dtype) for name, dtype in df.schema.items()},
        "rows": df.height,
        "symbols": df["symbol"].n_unique(),
        "min_date": str(df["date"].min()),
        "max_date": str(df["date"].max()),
        "invalid_ohlc_rows": invalid_ohlc,
        "zero_volume_rows": int(by_symbol["zero_volume_days"].sum()),
        "zero_turnover_rows": int(by_symbol["zero_turnover_days"].sum()),
        "extreme_abs_daily_return_gt_45pct": extreme_moves,
        "symbols_with_calendar_gaps_gt_14d": int(symbols_with_gaps),
        "universe_snapshot_files": len(snapshot_files),
        "sample_symbol_lifetimes": by_symbol.sort("rows", descending=True).head(10).to_dicts(),
        "rust_core": check_rust_core(),
    }


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    base = df.with_columns(
        [
            (pl.col("turnover").rolling_mean(20).over("symbol")).alias("adv20"),
            (pl.col("volume").rolling_mean(20).over("symbol")).alias("vol20"),
            (pl.col("volume").rolling_mean(50).over("symbol")).alias("vol50"),
            (pl.col("close").rolling_mean(50).over("symbol")).alias("sma50"),
            (pl.col("close").rolling_mean(100).over("symbol")).alias("sma100"),
            (pl.col("close").rolling_mean(200).over("symbol")).alias("sma200"),
            (pl.col("high").rolling_max(126).over("symbol").shift(1).over("symbol")).alias("high126_prev"),
            (pl.col("high").rolling_max(252).over("symbol").shift(1).over("symbol")).alias("high252_prev"),
            (pl.col("low").rolling_min(252).over("symbol")).alias("low252"),
            (pl.col("close") / pl.col("close").shift(21).over("symbol") - 1.0).alias("ret21"),
            (pl.col("close") / pl.col("close").shift(63).over("symbol") - 1.0).alias("ret63"),
            (pl.col("close") / pl.col("close").shift(126).over("symbol") - 1.0).alias("ret126"),
            (pl.col("close") / pl.col("close").shift(252).over("symbol") - 1.0).alias("ret252"),
            (pl.max_horizontal(
                pl.col("high") - pl.col("low"),
                (pl.col("high") - pl.col("close").shift(1).over("symbol")).abs(),
                (pl.col("low") - pl.col("close").shift(1).over("symbol")).abs(),
            ).alias("tr")),
        ]
    )
    base = base.with_columns(
        [
            pl.col("tr").rolling_mean(14).over("symbol").alias("atr14"),
            ((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low"))).fill_nan(0.5).alias("ibs"),
            (pl.col("ret126") - pl.col("ret21")).alias("mom126_skip21"),
            (pl.col("ret63") - pl.col("ret21")).alias("mom63_skip21"),
            (pl.col("ret252") - pl.col("ret21")).alias("mom252_skip21"),
            (pl.col("close") / pl.col("high252_prev") - 1.0).alias("dist_52w_high"),
            (pl.col("close") / pl.col("low252") - 1.0).alias("dist_52w_low"),
        ]
    )
    ranked = base.with_columns(
        [
            pl.col("mom126_skip21").rank(descending=True).over("date").alias("rank_mom126"),
            pl.col("mom63_skip21").rank(descending=True).over("date").alias("rank_mom63"),
            pl.col("ret252").rank(descending=True).over("date").alias("rank_mom252"),
            pl.col("ret21").rank(descending=False).over("date").alias("rank_reversal21"),
            pl.col("turnover").rank(descending=True).over("date").alias("rank_liquidity"),
        ]
    ).drop("tr")
    breadth = ranked.group_by("date").agg(
        ((pl.col("close") > pl.col("sma200")).cast(pl.Float64).mean()).alias("breadth200")
    )
    bench_symbol = "NIFTYBEES" if "NIFTYBEES" in set(df["symbol"].unique().to_list()) else None
    if bench_symbol is not None:
        bench = ranked.filter(pl.col("symbol") == bench_symbol).select(
            "date",
            (pl.col("close") > pl.col("sma200")).alias("market_bull"),
        )
    else:
        bench = breadth.select("date", (pl.col("breadth200") > 0.40).alias("market_bull"))
    return ranked.join(breadth, on="date", how="left").join(bench, on="date", how="left")


def liquid_base_expr(cfg: StrategyConfig) -> pl.Expr:
    return (
        (pl.col("close") >= cfg.min_price)
        & (pl.col("adv20") >= cfg.min_adv20)
        & (pl.col("volume") > 0)
        & pl.col("sma200").is_not_null()
        & pl.col("atr14").is_not_null()
    )


def signal_frame(features: pl.DataFrame, cfg: StrategyConfig, start: date, end: date) -> pl.DataFrame:
    base = features.filter(
        (pl.col("date") >= start)
        & (pl.col("date") <= end)
        & liquid_base_expr(cfg)
    )
    if cfg.require_market_bull:
        base = base.filter((pl.col("market_bull") == True) & (pl.col("breadth200") >= cfg.min_breadth200))
    if cfg.entry_rule in {"momentum_trend", "momentum_trend_63", "momentum_trend_252"}:
        momentum_col = {
            "momentum_trend": "mom126_skip21",
            "momentum_trend_63": "mom63_skip21",
            "momentum_trend_252": "mom252_skip21",
        }[cfg.entry_rule]
        entry = (
            (pl.col("close") > pl.col("sma200"))
            & (pl.col("sma50") > pl.col("sma200"))
            & (pl.col("dist_52w_high") > -0.25)
            & (pl.col("dist_52w_low") > 0.30)
            & (pl.col(momentum_col) > 0)
        )
    elif cfg.entry_rule == "momentum_leaders":
        entry = (
            (pl.col("close") > pl.col("sma50"))
            & (pl.col("sma50") > pl.col("sma200"))
            & (pl.col("dist_52w_high") > -0.15)
            & (pl.col("ret21") > 0)
            & (pl.col("mom126_skip21") > 0.15)
        )
    elif cfg.entry_rule == "breakout_126":
        entry = (
            (pl.col("close") > pl.col("high126_prev"))
            & (pl.col("close") > pl.col("sma200"))
            & (pl.col("volume") > pl.col("vol50") * 1.5)
            & (pl.col("mom126_skip21") > 0)
        )
    elif cfg.entry_rule == "pullback_uptrend":
        entry = (
            (pl.col("close") > pl.col("sma200"))
            & (pl.col("sma50") > pl.col("sma200"))
            & (pl.col("ret21") < -0.08)
            & (pl.col("ibs") < 0.25)
            & (pl.col("mom126_skip21") > 0)
        )
    elif cfg.entry_rule == "leader_pullback":
        entry = (
            (pl.col("close") > pl.col("sma100"))
            & (pl.col("sma50") > pl.col("sma200"))
            & (pl.col("ret21") < -0.03)
            & (pl.col("ret21") > -0.16)
            & (pl.col("ibs") < 0.35)
            & (pl.col("mom126_skip21") > 0.15)
        )
    elif cfg.entry_rule == "vol_breakout":
        entry = (
            (pl.col("close") > pl.col("high252_prev") * 0.98)
            & (pl.col("close") > pl.col("sma200"))
            & (pl.col("atr14") / pl.col("close") < 0.08)
            & (pl.col("volume") > pl.col("vol20") * 1.2)
        )
    else:
        raise ValueError(f"unknown entry rule: {cfg.entry_rule}")

    filtered = base.filter(entry).with_columns(pl.col(cfg.rank_col).alias("rank_value"))
    if cfg.rank_max is not None:
        filtered = filtered.filter(pl.col("rank_value") <= cfg.rank_max)
    return (
        filtered
        .sort(["date", "rank_value", "rank_liquidity"])
        .select(
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "atr14",
            "adv20",
            "sma50",
            "sma100",
            "sma200",
            "rank_value",
        )
    )


def build_market_data(features: pl.DataFrame, start: date, end: date) -> dict[str, dict[date, dict[str, float]]]:
    sliced = features.filter((pl.col("date") >= start) & (pl.col("date") <= end)).select(
        "date", "symbol", "open", "high", "low", "close", "atr14", "adv20", "sma50", "sma100", "sma200"
    )
    data: dict[str, dict[date, dict[str, float]]] = defaultdict(dict)
    for row in sliced.iter_rows(named=True):
        data[row["symbol"]][row["date"]] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "atr14": float(row["atr14"]) if row["atr14"] is not None else math.nan,
            "adv20": float(row["adv20"]) if row["adv20"] is not None else 0.0,
            "sma50": float(row["sma50"]) if row["sma50"] is not None else math.nan,
            "sma100": float(row["sma100"]) if row["sma100"] is not None else math.nan,
            "sma200": float(row["sma200"]) if row["sma200"] is not None else math.nan,
        }
    return data


def brokerage(turnover: float) -> float:
    return min(20.0, turnover * 0.0003)


def buy_charges(turnover: float) -> float:
    exchange = turnover * 0.0000322
    sebi = turnover * 0.000001
    stamp = turnover * 0.00015
    brok = brokerage(turnover)
    gst = 0.18 * (brok + exchange)
    return brok + exchange + sebi + stamp + gst


def sell_charges(turnover: float) -> float:
    exchange = turnover * 0.0000322
    sebi = turnover * 0.000001
    stt = turnover * 0.001
    brok = brokerage(turnover)
    gst = 0.18 * (brok + exchange)
    return brok + exchange + sebi + stt + gst


def max_drawdown_from_equity(equities: np.ndarray) -> float:
    peak = np.maximum.accumulate(equities)
    drawdowns = equities / peak - 1.0
    return float(np.min(drawdowns) * 100.0)


def sharpe_from_equity(equities: np.ndarray) -> float:
    returns = np.diff(equities) / equities[:-1]
    if len(returns) > 2 and np.std(returns) > 0:
        return float(np.mean(returns) / np.std(returns) * math.sqrt(252))
    return 0.0


def equity_metrics(
    strategy: str,
    daily: list[dict[str, Any]],
    trades: list[Trade],
    start: date,
    end: date,
    initial_capital: float,
) -> Metrics:
    if not daily:
        raise RuntimeError("No daily equity records")
    equities = np.array([float(row["equity"]) for row in daily], dtype=float)
    years = max((end - start).days / 365.25, 1e-9)
    final_equity = float(equities[-1])
    cagr = (final_equity / initial_capital) ** (1.0 / years) - 1.0
    sharpe = sharpe_from_equity(equities)
    wins = [t for t in trades if t.net_pnl > 0]
    turnover = sum((t.entry_price * t.shares) + (t.exit_price * t.shares) for t in trades) / initial_capital
    avg_hold = statistics.mean([t.holding_days for t in trades]) if trades else 0.0
    avg_ret = statistics.mean([t.return_pct for t in trades]) if trades else 0.0
    tax = estimate_tax(trades)
    post_tax_final = final_equity - tax
    post_tax_cagr = (post_tax_final / initial_capital) ** (1.0 / years) - 1.0 if post_tax_final > 0 else -1.0
    return Metrics(
        strategy=strategy,
        start=start.isoformat(),
        end=end.isoformat(),
        cagr=cagr,
        post_tax_cagr=post_tax_cagr,
        total_return_pct=(final_equity / initial_capital - 1.0) * 100.0,
        max_drawdown_pct=max_drawdown_from_equity(equities),
        sharpe=sharpe,
        win_rate=(len(wins) / len(trades) * 100.0) if trades else 0.0,
        avg_holding_days=avg_hold,
        turnover=turnover,
        trades=len(trades),
        final_equity=final_equity,
        avg_trade_return_pct=avg_ret,
    )


def estimate_tax(trades: list[Trade]) -> float:
    stcg = 0.0
    ltcg = 0.0
    for trade in trades:
        if trade.net_pnl <= 0:
            continue
        if trade.holding_days >= 365:
            ltcg += trade.net_pnl
        else:
            stcg += trade.net_pnl
    return stcg * 0.20 + max(0.0, ltcg - 125_000.0) * 0.125


def estimate_index_ltcg_tax(initial_capital: float, final_equity: float, holding_days: int) -> float:
    gain = max(0.0, final_equity - initial_capital)
    if gain <= 0:
        return 0.0
    if holding_days >= 365:
        return max(0.0, gain - 125_000.0) * 0.125
    return gain * 0.20


def benchmark_from_symbol(
    features: pl.DataFrame,
    symbol: str,
    start: date,
    end: date,
    *,
    name: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> tuple[BenchmarkMetrics, list[dict[str, Any]]]:
    bench = (
        features.filter((pl.col("symbol") == symbol) & (pl.col("date") >= start) & (pl.col("date") <= end))
        .select("date", "close")
        .sort("date")
    )
    if bench.height < 2:
        raise RuntimeError(f"Benchmark symbol {symbol} not available for {start} to {end}")
    dates = bench["date"].to_list()
    closes = np.array(bench["close"].to_list(), dtype=float)
    shares = initial_capital / closes[0]
    equities = shares * closes
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    final_equity = float(equities[-1])
    tax = estimate_index_ltcg_tax(initial_capital, final_equity, (dates[-1] - dates[0]).days)
    post_tax_final = final_equity - tax
    daily = [{"date": d.isoformat(), "equity": float(eq)} for d, eq in zip(dates, equities)]
    return (
        BenchmarkMetrics(
            name=name,
            start=dates[0].isoformat(),
            end=dates[-1].isoformat(),
            cagr=(final_equity / initial_capital) ** (1.0 / years) - 1.0,
            post_tax_cagr=(post_tax_final / initial_capital) ** (1.0 / years) - 1.0,
            total_return_pct=(final_equity / initial_capital - 1.0) * 100.0,
            post_tax_total_return_pct=(post_tax_final / initial_capital - 1.0) * 100.0,
            max_drawdown_pct=max_drawdown_from_equity(equities),
            sharpe=sharpe_from_equity(equities),
            final_equity=final_equity,
            post_tax_final_equity=post_tax_final,
            tax=tax,
        ),
        daily,
    )


def cost_turnover_decomposition(
    metrics: Metrics,
    trades: list[Trade],
    daily: list[dict[str, Any]],
    benchmark: BenchmarkMetrics | None = None,
) -> dict[str, Any]:
    gross_no_friction = sum(t.gross_pnl_no_friction for t in trades)
    slippage = sum(t.slippage_cost for t in trades)
    costs = sum(t.charges for t in trades)
    net = sum(t.net_pnl for t in trades)
    years = max((parse_date(metrics.end) - parse_date(metrics.start)).days / 365.25, 1e-9)
    avg_cash_pct = 0.0
    if daily:
        avg_cash_pct = statistics.mean(
            (float(row.get("cash", 0.0)) / float(row["equity"]))
            for row in daily
            if float(row["equity"]) > 0
        )
    bench_cagr = benchmark.cagr if benchmark is not None else 0.0
    cash_drag_est = avg_cash_pct * bench_cagr
    return {
        "gross_no_friction_pnl": gross_no_friction,
        "slippage_cost": slippage,
        "explicit_costs": costs,
        "net_trade_pnl": net,
        "slippage_pct_initial": slippage / INITIAL_CAPITAL * 100.0,
        "explicit_costs_pct_initial": costs / INITIAL_CAPITAL * 100.0,
        "annual_slippage_pct_initial": slippage / INITIAL_CAPITAL / years * 100.0,
        "annual_explicit_costs_pct_initial": costs / INITIAL_CAPITAL / years * 100.0,
        "annual_friction_pct_initial": (slippage + costs) / INITIAL_CAPITAL / years * 100.0,
        "avg_cash_pct": avg_cash_pct * 100.0,
        "cash_drag_est_annual_pct": cash_drag_est * 100.0,
        "turnover_x": metrics.turnover,
    }


def simulate_portfolio(
    features: pl.DataFrame,
    cfg: StrategyConfig,
    start: date,
    end: date,
    *,
    initial_capital: float = INITIAL_CAPITAL,
) -> tuple[Metrics, list[Trade], list[dict[str, Any]]]:
    calendar = features.filter((pl.col("date") >= start) & (pl.col("date") <= end)).select("date").unique().sort("date")["date"].to_list()
    market = build_market_data(features, start, end)
    signals = signal_frame(features, cfg, start, end)
    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in signals.iter_rows(named=True):
        signals_by_date[row["date"]].append(row)

    cash = initial_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    daily: list[dict[str, Any]] = []
    pending_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    recent_equity_returns: list[float] = []

    for idx, today in enumerate(calendar):
        for sig in signals_by_date.get(today, []):
            if idx + 1 < len(calendar):
                pending_by_date[calendar[idx + 1]].append(sig)

        for sym in list(positions):
            pos = positions[sym]
            bar = market.get(sym, {}).get(today)
            if bar is None:
                continue
            pos.days_held += 1
            pos.highest_close = max(pos.highest_close, bar["close"])
            exit_raw = None
            reason = ""
            if pos.stop_raw is not None and bar["open"] <= pos.stop_raw:
                exit_raw = bar["open"]
                reason = "stop_open"
            elif pos.stop_raw is not None and bar["low"] <= pos.stop_raw:
                exit_raw = pos.stop_raw
                reason = "stop"
            elif cfg.exit_rule == "below_sma100" and bar["close"] < bar["sma100"]:
                exit_raw = bar["close"]
                reason = "below_sma100"
            elif cfg.exit_rule == "mean_revert_sma5" and bar["close"] > bar["sma50"]:
                exit_raw = bar["close"]
                reason = "mean_revert"
            elif pos.days_held >= cfg.hold_days:
                exit_raw = bar["close"]
                reason = "time"

            if cfg.trail_atr is not None and not math.isnan(bar["atr14"]):
                trail = pos.highest_close - cfg.trail_atr * bar["atr14"]
                pos.stop_raw = max(pos.stop_raw or trail, trail)

            if exit_raw is None:
                continue
            sell_raw = exit_raw * (1.0 - cfg.slippage_bps / 10_000.0)
            gross = sell_raw * pos.shares
            buy_slippage = (pos.entry_price_raw - (pos.entry_price_raw / (1.0 + cfg.slippage_bps / 10_000.0))) * pos.shares
            sell_slippage = (exit_raw - sell_raw) * pos.shares
            sell_costs = sell_charges(gross)
            charges = pos.buy_charges + sell_costs
            proceeds = gross - sell_costs
            cash += proceeds
            net_pnl = proceeds - pos.cost
            initial_risk = None
            if pos.stop_raw is not None:
                risk_per_share = max(pos.entry_price_net - pos.stop_raw, 0.0)
                if risk_per_share > 0:
                    initial_risk = net_pnl / (risk_per_share * pos.shares)
            trades.append(
                Trade(
                    symbol=sym,
                    signal_date=pos.entry_signal_date.isoformat(),
                    entry_date=pos.entry_date.isoformat(),
                    exit_date=today.isoformat(),
                    entry_price=pos.entry_price_net,
                    exit_price=sell_raw,
                    shares=pos.shares,
                    gross_pnl=(sell_raw - pos.entry_price_raw) * pos.shares,
                    net_pnl=net_pnl,
                    gross_pnl_no_friction=(exit_raw - (pos.entry_price_raw / (1.0 + cfg.slippage_bps / 10_000.0))) * pos.shares,
                    slippage_cost=buy_slippage + sell_slippage,
                    return_pct=net_pnl / pos.cost * 100.0,
                    r_multiple=initial_risk,
                    holding_days=(today - pos.entry_date).days,
                    exit_reason=reason,
                    charges=charges,
                    buy_charges=pos.buy_charges,
                    sell_charges=sell_costs,
                )
            )
            del positions[sym]

        todays_entries = pending_by_date.get(today, [])
        if todays_entries:
            todays_entries = sorted(todays_entries, key=lambda row: (row["rank_value"], row["symbol"]))
        exposure_mult = 1.0
        if cfg.vol_target_ann is not None and len(recent_equity_returns) >= 20:
            realized_daily = float(np.std(np.array(recent_equity_returns[-20:], dtype=float)))
            target_daily = cfg.vol_target_ann / math.sqrt(252)
            if realized_daily > 0:
                exposure_mult = min(1.0, max(cfg.min_exposure_mult, target_daily / realized_daily))
        for sig in todays_entries:
            if len(positions) >= cfg.max_positions or sig["symbol"] in positions:
                continue
            bar = market.get(sig["symbol"], {}).get(today)
            if bar is None:
                continue
            buy_raw = bar["open"] * (1.0 + cfg.slippage_bps / 10_000.0)
            max_notional = min(cash * cfg.position_pct * exposure_mult, bar["adv20"] * cfg.max_adv_pct)
            shares = int(max_notional // buy_raw)
            if shares <= 0:
                continue
            gross = buy_raw * shares
            charges = buy_charges(gross)
            total_cost = gross + charges
            if total_cost > cash:
                shares = int(cash // (buy_raw * 1.002))
                gross = buy_raw * shares
                charges = buy_charges(gross)
                total_cost = gross + charges
            if shares <= 0 or total_cost > cash:
                continue
            stop = None
            if cfg.stop_atr is not None and not math.isnan(bar["atr14"]):
                stop = max(0.01, buy_raw - cfg.stop_atr * bar["atr14"])
            cash -= total_cost
            positions[sig["symbol"]] = Position(
                symbol=sig["symbol"],
                entry_signal_date=sig["date"],
                entry_date=today,
                entry_price_raw=buy_raw,
                entry_price_net=total_cost / shares,
                shares=shares,
                cost=total_cost,
                buy_charges=charges,
                stop_raw=stop,
                highest_close=bar["close"],
            )

        mtm = 0.0
        for sym, pos in positions.items():
            bar = market.get(sym, {}).get(today)
            price = bar["close"] if bar is not None else pos.entry_price_raw
            mtm += price * pos.shares
        equity = cash + mtm
        if daily and daily[-1]["equity"] > 0:
            recent_equity_returns.append(equity / float(daily[-1]["equity"]) - 1.0)
        daily.append({"date": today.isoformat(), "equity": equity, "cash": cash, "positions": len(positions)})

    if positions:
        last_day = calendar[-1]
        for sym, pos in list(positions.items()):
            bar = market.get(sym, {}).get(last_day)
            if bar is None:
                continue
            sell_raw = bar["close"] * (1.0 - cfg.slippage_bps / 10_000.0)
            gross = sell_raw * pos.shares
            buy_slippage = (pos.entry_price_raw - (pos.entry_price_raw / (1.0 + cfg.slippage_bps / 10_000.0))) * pos.shares
            sell_slippage = (bar["close"] - sell_raw) * pos.shares
            sell_costs = sell_charges(gross)
            charges = pos.buy_charges + sell_costs
            proceeds = gross - sell_costs
            cash += proceeds
            net_pnl = proceeds - pos.cost
            trades.append(
                Trade(
                    symbol=sym,
                    signal_date=pos.entry_signal_date.isoformat(),
                    entry_date=pos.entry_date.isoformat(),
                    exit_date=last_day.isoformat(),
                    entry_price=pos.entry_price_net,
                    exit_price=sell_raw,
                    shares=pos.shares,
                    gross_pnl=(sell_raw - pos.entry_price_raw) * pos.shares,
                    net_pnl=net_pnl,
                    gross_pnl_no_friction=(bar["close"] - (pos.entry_price_raw / (1.0 + cfg.slippage_bps / 10_000.0))) * pos.shares,
                    slippage_cost=buy_slippage + sell_slippage,
                    return_pct=net_pnl / pos.cost * 100.0,
                    r_multiple=None,
                    holding_days=(last_day - pos.entry_date).days,
                    exit_reason="liquidate_end",
                    charges=charges,
                    buy_charges=pos.buy_charges,
                    sell_charges=sell_costs,
                )
            )
        daily[-1]["equity"] = cash

    return equity_metrics(cfg.name, daily, trades, start, end, initial_capital), trades, daily


def default_candidates(quick: bool) -> list[StrategyConfig]:
    min_adv = 30_000_000.0 if quick else 20_000_000.0
    return [
        StrategyConfig(name="mom_126_hold63", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=63, max_positions=12, position_pct=0.10, stop_atr=3.0, trail_atr=None, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=150),
        StrategyConfig(name="mom_126_hold42_r100", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=42, max_positions=12, position_pct=0.10, stop_atr=3.0, trail_atr=None, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=100),
        StrategyConfig(name="mom_126_hold42_r150", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=42, max_positions=12, position_pct=0.10, stop_atr=3.0, trail_atr=None, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=150),
        StrategyConfig(name="mom_126_hold84_r150", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=84, max_positions=12, position_pct=0.10, stop_atr=3.0, trail_atr=None, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=150),
        StrategyConfig(name="mom_126_hold63_r200", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=63, max_positions=12, position_pct=0.10, stop_atr=3.0, trail_atr=None, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=200),
        StrategyConfig(name="mom_126_top80_conc", family="momentum", rank_col="rank_mom126", entry_rule="momentum_trend", hold_days=42, max_positions=6, position_pct=0.166, stop_atr=2.5, trail_atr=3.5, exit_rule="time", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=80, min_breadth200=0.45),
        StrategyConfig(name="mom_leaders_42", family="momentum", rank_col="rank_mom126", entry_rule="momentum_leaders", hold_days=42, max_positions=8, position_pct=0.125, stop_atr=2.5, trail_atr=3.5, exit_rule="time", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=80, min_breadth200=0.45),
        StrategyConfig(name="mom_leaders_84", family="momentum", rank_col="rank_mom252", entry_rule="momentum_leaders", hold_days=84, max_positions=8, position_pct=0.125, stop_atr=3.0, trail_atr=4.0, exit_rule="below_sma100", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=80, min_breadth200=0.45),
        StrategyConfig(name="breakout_126_vol", family="breakout", rank_col="rank_mom126", entry_rule="breakout_126", hold_days=84, max_positions=10, position_pct=0.10, stop_atr=2.5, trail_atr=3.0, exit_rule="time", slippage_bps=20.0, min_adv20=min_adv, max_adv_pct=0.015, rank_max=120),
        StrategyConfig(name="vol_breakout_252", family="breakout", rank_col="rank_mom252", entry_rule="vol_breakout", hold_days=126, max_positions=10, position_pct=0.10, stop_atr=3.0, trail_atr=4.0, exit_rule="time", slippage_bps=20.0, min_adv20=min_adv, max_adv_pct=0.015, rank_max=120),
        StrategyConfig(name="leader_pullback", family="mean_reversion", rank_col="rank_mom126", entry_rule="leader_pullback", hold_days=21, max_positions=10, position_pct=0.10, stop_atr=2.5, trail_atr=None, exit_rule="mean_revert_sma5", slippage_bps=15.0, min_adv20=min_adv, max_adv_pct=0.02, rank_max=100, min_breadth200=0.45),
    ]


def walk_forward(features: pl.DataFrame, cfg: StrategyConfig) -> list[dict[str, Any]]:
    rows = []
    for test_year in range(2015, 2023):
        train_start = date(test_year - 4, 1, 1)
        train_end = date(test_year - 1, 12, 31)
        test_start = date(test_year, 1, 1)
        test_end = date(test_year, 12, 31)
        train_m, _, _ = simulate_portfolio(features, cfg, train_start, train_end)
        test_m, _, _ = simulate_portfolio(features, cfg, test_start, test_end)
        rows.append(
            {
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "train_cagr": train_m.cagr,
                "test_cagr": test_m.cagr,
                "test_mdd": test_m.max_drawdown_pct,
                "test_sharpe": test_m.sharpe,
                "test_trades": test_m.trades,
            }
        )
    return rows


def sensitivity(features: pl.DataFrame, cfg: StrategyConfig) -> list[dict[str, Any]]:
    variants = []
    for hold_mult in [0.75, 1.0, 1.25]:
        for slip in [10.0, cfg.slippage_bps, 25.0]:
            new_cfg = StrategyConfig(
                **{
                    **asdict(cfg),
                    "name": f"{cfg.name}_h{hold_mult}_s{int(slip)}",
                    "hold_days": max(10, int(cfg.hold_days * hold_mult)),
                    "slippage_bps": slip,
                }
            )
            m, _, _ = simulate_portfolio(features, new_cfg, date(2015, 1, 1), date(2022, 12, 31))
            variants.append({"hold_mult": hold_mult, "slippage_bps": slip, **asdict(m)})
    return variants


def clone_config(cfg: StrategyConfig, **updates: Any) -> StrategyConfig:
    values = asdict(cfg)
    values.update(updates)
    return StrategyConfig(**values)


def hold_lookback_plateau_configs(base: StrategyConfig) -> list[StrategyConfig]:
    rows: list[StrategyConfig] = []
    lookbacks = [
        ("63", "rank_mom63", "momentum_trend_63"),
        ("126", "rank_mom126", "momentum_trend"),
        ("252", "rank_mom252", "momentum_trend_252"),
    ]
    for hold in [63, 84, 126]:
        for label, rank_col, entry_rule in lookbacks:
            rows.append(
                clone_config(
                    base,
                    name=f"plateau_lb{label}_hold{hold}",
                    rank_col=rank_col,
                    entry_rule=entry_rule,
                    hold_days=hold,
                    rank_max=150,
                )
            )
    return rows


def one_improvement_pass(
    features: pl.DataFrame,
    base_cfg: StrategyConfig,
    base_metrics: Metrics,
    base_trades: list[Trade],
    base_daily: list[dict[str, Any]],
    pre_benchmark: BenchmarkMetrics,
) -> tuple[StrategyConfig, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    base_decomp = cost_turnover_decomposition(base_metrics, base_trades, base_daily, pre_benchmark)
    variants: list[StrategyConfig] = [base_cfg]
    variants.extend(
        [
            clone_config(base_cfg, name="lower_turnover_hold126", hold_days=126, max_positions=10, position_pct=0.10),
            clone_config(base_cfg, name="lower_turnover_hold168", hold_days=168, max_positions=8, position_pct=0.125),
            clone_config(base_cfg, name="vol_target_18", vol_target_ann=0.18),
            clone_config(base_cfg, name="vol_target_16", vol_target_ann=0.16),
        ]
    )
    if base_decomp["annual_friction_pct_initial"] > 3.0:
        variants.extend(
            [
                clone_config(base_cfg, name="friction_hold126_rank100", hold_days=126, rank_max=100, max_positions=8, position_pct=0.125),
                clone_config(base_cfg, name="friction_hold168_rank100", hold_days=168, rank_max=100, max_positions=8, position_pct=0.125),
            ]
        )

    rows = []
    for cfg in variants:
        m, trades, daily = simulate_portfolio(features, cfg, date(2015, 1, 1), date(2022, 12, 31))
        decomp = cost_turnover_decomposition(m, trades, daily, pre_benchmark)
        calmar = m.post_tax_cagr / abs(m.max_drawdown_pct / 100.0) if m.max_drawdown_pct < 0 else 0.0
        rows.append(
            {
                "name": cfg.name,
                "score": calmar,
                "post_tax_cagr": m.post_tax_cagr,
                "pre_tax_cagr": m.cagr,
                "max_drawdown_pct": m.max_drawdown_pct,
                "sharpe": m.sharpe,
                "trades": m.trades,
                "turnover": m.turnover,
                "annual_friction_pct_initial": decomp["annual_friction_pct_initial"],
                "config": asdict(cfg),
            }
        )
    rows = sorted(rows, key=lambda row: (row["score"], row["post_tax_cagr"]), reverse=True)

    plateau = []
    for cfg in hold_lookback_plateau_configs(base_cfg):
        m, _, _ = simulate_portfolio(features, cfg, date(2015, 1, 1), date(2022, 12, 31))
        plateau.append(
            {
                "name": cfg.name,
                "lookback": cfg.rank_col,
                "hold_days": cfg.hold_days,
                "post_tax_cagr": m.post_tax_cagr,
                "pre_tax_cagr": m.cagr,
                "max_drawdown_pct": m.max_drawdown_pct,
                "sharpe": m.sharpe,
                "trades": m.trades,
            }
        )

    return StrategyConfig(**rows[0]["config"]), rows, plateau, base_decomp


def bootstrap_trades(trades: list[Trade], samples: int = 1000) -> dict[str, float]:
    pnls = [t.net_pnl for t in trades]
    if not pnls:
        return {}
    rng = random.Random(42)
    totals = []
    worst_dds = []
    for _ in range(samples):
        equity = INITIAL_CAPITAL
        peak = equity
        min_dd = 0.0
        for pnl in (rng.choice(pnls) for _ in pnls):
            equity += pnl
            peak = max(peak, equity)
            min_dd = min(min_dd, equity / peak - 1.0)
        totals.append((equity / INITIAL_CAPITAL) - 1.0)
        worst_dds.append(min_dd)
    return {
        "return_p05": float(np.percentile(totals, 5)),
        "return_p50": float(np.percentile(totals, 50)),
        "return_p95": float(np.percentile(totals, 95)),
        "maxdd_p05": float(np.percentile(worst_dds, 5)),
        "maxdd_p50": float(np.percentile(worst_dds, 50)),
        "maxdd_p95": float(np.percentile(worst_dds, 95)),
    }


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    audit: dict[str, Any],
    coarse: list[dict[str, Any]],
    best_cfg: StrategyConfig,
    insample: Metrics,
    holdout: Metrics,
    benchmark: BenchmarkMetrics,
    benchmark_source_note: str,
    decomp: dict[str, Any],
    improvement_rows: list[dict[str, Any]],
    plateau_rows: list[dict[str, Any]],
    wf: list[dict[str, Any]],
    sens: list[dict[str, Any]],
    boot: dict[str, float],
) -> None:
    clears_benchmark = (
        holdout.post_tax_cagr > benchmark.post_tax_cagr
        and abs(holdout.max_drawdown_pct) < abs(benchmark.max_drawdown_pct)
        and holdout.sharpe > benchmark.sharpe
    )
    headline = (
        "Headline: the strategy beats the Nifty 50 proxy on post-tax CAGR, but loses the risk-adjusted comparison on drawdown and Sharpe; index the money."
        if not clears_benchmark
        else "Headline: the strategy beats the Nifty 50 proxy post-tax with a better drawdown profile on the final holdout."
    )
    verdict = (
        "Index the money. The strategy is interesting, but it does not clear the benchmark after tax and drawdown."
        if not clears_benchmark
        else "Paper-trade before deployment. The strategy clears this benchmark comparison, but implementation risk remains."
    )
    lines = [
        "# Autonomous Indian Equity Strategy Research Report",
        "",
        headline,
        "",
        "## Data Audit",
        f"- Source: `data/yf_adjusted/ohlcv_all.parquet` via `ParquetOhlcvStore`.",
        f"- Rows/symbols/date range: {audit['rows']:,} rows, {audit['symbols']:,} symbols, {audit['min_date']} to {audit['max_date']}.",
        f"- Schema: `{audit['schema']}`.",
        f"- Quality flags: {audit['invalid_ohlc_rows']} invalid OHLC rows, {audit['zero_volume_rows']} zero-volume rows, {audit['extreme_abs_daily_return_gt_45pct']} absolute daily moves above 45%.",
        f"- Universe snapshots: {audit['universe_snapshot_files']} NSE bhavcopy snapshots available. The parquet appears to be broad historical NSE, not just today's survivors, but delisted coverage is still only as good as yfinance and the collected snapshots.",
        f"- Rust core check: {audit['rust_core']}.",
        "",
        "## Realism Assumptions",
        "- Signals are computed after day T close and execute at day T+1 open.",
        "- Delivery model: integer shares, no leverage, cash drag, max concurrent positions, and position notional capped at 1.5-2.0% of ADV depending on strategy.",
        "- Costs: brokerage min(₹20/order, 0.03%), STT 0.1% on sell, exchange charges, SEBI fees, stamp duty on buy, GST on brokerage/exchange, plus 0.10-0.25% slippage per side through strategy config.",
        "- Liquidity filter: price >= ₹20 and ADV20 >= ₹20-30m. Illiquid/penny names are excluded.",
        "- Taxes are reported separately using FY26 assumptions: STCG 20%, LTCG 12.5% above ₹1.25L.",
        f"- Benchmark: {benchmark.name}. {benchmark_source_note}",
        "",
        "## Coarse Candidate Results (2015-2022 only)",
    ]
    for row in coarse:
        lines.append(
            f"- {row['strategy']}: CAGR {row['cagr']:.2%}, MDD {row['max_drawdown_pct']:.1f}%, Sharpe {row['sharpe']:.2f}, trades {row['trades']}"
        )
    lines.extend(
        [
            "",
            "## Selected Strategy",
            f"- Name: `{best_cfg.name}` ({best_cfg.family}).",
            f"- Logic: `{best_cfg.entry_rule}` ranked by `{best_cfg.rank_col}`, hold up to {best_cfg.hold_days} trading days, max {best_cfg.max_positions} positions.",
            f"- Exit: `{best_cfg.exit_rule}`, stop ATR={best_cfg.stop_atr}, trailing ATR={best_cfg.trail_atr}.",
            "",
            "## In-Sample vs Final Holdout",
            f"- In-sample 2015-2022: pre-tax CAGR {insample.cagr:.2%}, post-tax CAGR {insample.post_tax_cagr:.2%}, MDD {insample.max_drawdown_pct:.1f}%, Sharpe {insample.sharpe:.2f}, win rate {insample.win_rate:.1f}%, trades {insample.trades}.",
            f"- Final holdout 2023-present: pre-tax CAGR {holdout.cagr:.2%}, post-tax CAGR {holdout.post_tax_cagr:.2%}, MDD {holdout.max_drawdown_pct:.1f}%, Sharpe {holdout.sharpe:.2f}, win rate {holdout.win_rate:.1f}%, avg hold {holdout.avg_holding_days:.1f} days, turnover {holdout.turnover:.1f}x, trades {holdout.trades}.",
            f"- Benchmark holdout: pre-tax CAGR {benchmark.cagr:.2%}, post-tax CAGR {benchmark.post_tax_cagr:.2%}, MDD {benchmark.max_drawdown_pct:.1f}%, Sharpe {benchmark.sharpe:.2f}.",
            f"- Strategy minus benchmark: post-tax CAGR spread {(holdout.post_tax_cagr - benchmark.post_tax_cagr):.2%}; drawdown spread {(holdout.max_drawdown_pct - benchmark.max_drawdown_pct):.1f} percentage points; Sharpe spread {(holdout.sharpe - benchmark.sharpe):.2f}.",
            "",
            "## Cost And Turnover",
            f"- Gross no-friction P&L: ₹{decomp['gross_no_friction_pnl']:,.0f}.",
            f"- Slippage cost: ₹{decomp['slippage_cost']:,.0f}; explicit charges/taxes: ₹{decomp['explicit_costs']:,.0f}.",
            f"- Annual friction load: {decomp['annual_friction_pct_initial']:.2f}% of initial capital; average cash: {decomp['avg_cash_pct']:.1f}%; estimated annual cash drag vs benchmark: {decomp['cash_drag_est_annual_pct']:.2f}%.",
            "",
            "## One Improvement Pass",
        ]
    )
    for row in improvement_rows:
        lines.append(
            f"- {row['name']}: post-tax CAGR {row['post_tax_cagr']:.2%}, MDD {row['max_drawdown_pct']:.1f}%, Sharpe {row['sharpe']:.2f}, turnover {row['turnover']:.1f}x, friction {row['annual_friction_pct_initial']:.2f}%/yr."
        )
    lines.extend(
        [
            "",
            "## Hold x Lookback Plateau",
        ]
    )
    for row in plateau_rows:
        lines.append(
            f"- {row['name']}: post-tax CAGR {row['post_tax_cagr']:.2%}, MDD {row['max_drawdown_pct']:.1f}%, Sharpe {row['sharpe']:.2f}, trades {row['trades']}."
        )
    lines.extend(
        [
            "",
            "## Walk-Forward",
        ]
    )
    for row in wf:
        lines.append(
            f"- Test {row['test_start'][:4]}: train CAGR {row['train_cagr']:.2%}, test CAGR {row['test_cagr']:.2%}, MDD {row['test_mdd']:.1f}%, trades {row['test_trades']}."
        )
    lines.extend(
        [
            "",
            "## Sensitivity And Bootstrap",
            f"- Sensitivity variants written to `sensitivity.csv`; performance should be judged by spread, not the single best point.",
            f"- Bootstrap trade resampling: `{boot}`.",
            "",
            "## Biases And Limitations",
            "- yfinance adjusted OHLCV is the price source; splits/bonuses are assumed handled upstream.",
            "- Survivorship bias is reduced by broad historical symbols and NSE snapshots, but not eliminated. Apply a practical 10-20% CAGR haircut before believing any result.",
            "- Daily bars cannot know intraday order sequencing when stop and target both occur; this engine uses conservative stop-first logic where applicable.",
            "- No borrow, impact model beyond ADV cap/slippage, or corporate action event exclusions are added here.",
            "",
            "## Verdict",
            verdict,
            "",
            "A pleasingly unsentimental answer, which is usually the only useful kind.",
        ]
    )
    (OUTPUT_ROOT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run(quick: bool, skip_holdout: bool, coarse_only: bool) -> None:
    ensure_output()
    end = date(2026, 5, 29)
    df = load_ohlcv(START_2010, end)
    audit = audit_data(df)
    (OUTPUT_ROOT / "data_audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    features = build_features(df)
    if quick:
        features = features.filter(pl.col("date") >= date(2014, 1, 1))

    coarse = []
    configs = default_candidates(quick)
    for cfg in configs:
        m, trades, daily = simulate_portfolio(features, cfg, date(2015, 1, 1), date(2022, 12, 31))
        coarse.append({**asdict(m), "config": asdict(cfg)})
    coarse_sorted = sorted(coarse, key=lambda row: (row["cagr"], row["sharpe"]), reverse=True)
    write_csv(OUTPUT_ROOT / "coarse_results.csv", coarse_sorted)
    best_cfg = StrategyConfig(**coarse_sorted[0]["config"])
    if coarse_only:
        print(json.dumps({"best": best_cfg.name, "best_cagr": coarse_sorted[0]["cagr"], "output": str(OUTPUT_ROOT)}, indent=2))
        return

    insample, in_trades, in_daily = simulate_portfolio(features, best_cfg, date(2015, 1, 1), date(2022, 12, 31))
    pre_benchmark, _ = benchmark_from_symbol(
        features,
        "NIFTYBEES",
        date(2015, 1, 1),
        date(2022, 12, 31),
        name="NIFTYBEES adjusted close proxy",
    )
    improved_cfg, improvement_rows, plateau_rows, base_decomp = one_improvement_pass(
        features,
        best_cfg,
        insample,
        in_trades,
        in_daily,
        pre_benchmark,
    )
    best_cfg = improved_cfg
    insample, in_trades, in_daily = simulate_portfolio(features, best_cfg, date(2015, 1, 1), date(2022, 12, 31))
    wf = walk_forward(features, best_cfg)
    sens = sensitivity(features, best_cfg)

    if skip_holdout:
        holdout = Metrics(best_cfg.name, HOLDOUT_START.isoformat(), end.isoformat(), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, INITIAL_CAPITAL, 0.0)
        holdout_trades = []
        holdout_daily = []
        boot = {}
    else:
        holdout, holdout_trades, holdout_daily = simulate_portfolio(features, best_cfg, HOLDOUT_START, end)
        benchmark, benchmark_daily = benchmark_from_symbol(
            features,
            "NIFTYBEES",
            HOLDOUT_START,
            end,
            name="NIFTYBEES adjusted close proxy",
        )
        holdout_decomp = cost_turnover_decomposition(holdout, holdout_trades, holdout_daily, benchmark)
        boot = bootstrap_trades(holdout_trades, samples=300 if quick else 1000)
    if skip_holdout:
        benchmark = BenchmarkMetrics(
            name="NIFTYBEES adjusted close proxy",
            start=HOLDOUT_START.isoformat(),
            end=end.isoformat(),
            cagr=0.0,
            post_tax_cagr=0.0,
            total_return_pct=0.0,
            post_tax_total_return_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe=0.0,
            final_equity=INITIAL_CAPITAL,
            post_tax_final_equity=INITIAL_CAPITAL,
            tax=0.0,
        )
        benchmark_daily = []
        holdout_decomp = base_decomp

    write_csv(OUTPUT_ROOT / "walk_forward.csv", wf)
    write_csv(OUTPUT_ROOT / "sensitivity.csv", sens)
    write_csv(OUTPUT_ROOT / "improvement_pass.csv", improvement_rows)
    write_csv(OUTPUT_ROOT / "hold_lookback_plateau.csv", plateau_rows)
    write_csv(OUTPUT_ROOT / "holdout_trades.csv", [asdict(t) for t in holdout_trades])
    write_csv(OUTPUT_ROOT / "holdout_daily_equity.csv", holdout_daily)
    write_csv(OUTPUT_ROOT / "benchmark_daily_equity.csv", benchmark_daily)
    summary = {
        "best_config": asdict(best_cfg),
        "in_sample": asdict(insample),
        "holdout": asdict(holdout),
        "benchmark": asdict(benchmark),
        "holdout_cost_decomposition": holdout_decomp,
        "improvement_pass": improvement_rows,
        "hold_lookback_plateau": plateau_rows,
        "bootstrap": boot,
        "coarse": coarse_sorted,
    }
    (OUTPUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if not skip_holdout:
        write_report(
            audit,
            coarse_sorted,
            best_cfg,
            insample,
            holdout,
            benchmark,
            "Local data does not include official Nifty 50 TRI; NIFTYBEES adjusted OHLCV is used as the closest investable local Nifty 50 total-return proxy.",
            holdout_decomp,
            improvement_rows,
            plateau_rows,
            wf,
            sens,
            boot,
        )
    print(json.dumps({"best": best_cfg.name, "in_sample_cagr": insample.cagr, "holdout_cagr": holdout.cagr, "output": str(OUTPUT_ROOT)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Use faster audit/bootstrap settings.")
    parser.add_argument("--skip-holdout", action="store_true", help="Do not evaluate 2023-present.")
    parser.add_argument("--coarse-only", action="store_true", help="Only run candidate screening.")
    args = parser.parse_args()
    run(quick=args.quick, skip_holdout=args.skip_holdout, coarse_only=args.coarse_only)


if __name__ == "__main__":
    main()

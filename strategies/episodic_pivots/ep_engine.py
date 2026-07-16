from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import polars as pl

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "yf_adjusted"
OUTPUT_ROOT = PROJECT_ROOT / "strategies" / "episodic_pivots" / "output"
TRI_PATH = PROJECT_ROOT / "data" / "reference" / "nifty500_tri_2010-01-04_2026-03-17.csv"
INITIAL_CAPITAL = 1_000_000.0
START_2010 = date(2010, 1, 1)
TRAIN_START = date(2015, 1, 1)
TRAIN_END = date(2022, 12, 31)
HOLDOUT_START = date(2023, 1, 1)
FINAL_END = date(2026, 3, 17)


@dataclass(frozen=True)
class EPConfig:
    name: str
    min_gap_pct: float
    min_day_ret_pct: float
    min_vol_ratio: float
    min_close_pos: float
    min_adv20: float
    min_price: float
    max_positions: int
    position_pct: float
    max_adv_pct: float
    initial_stop_pct: float
    trail_pct: float
    max_hold_days: int
    slippage_bps: float
    require_above_sma200: bool = True


@dataclass
class Position:
    symbol: str
    signal_date: date
    entry_date: date
    entry_raw: float
    entry_net: float
    shares: int
    cost: float
    buy_charges: float
    stop_raw: float
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
    net_pnl: float
    gross_pnl_no_friction: float
    slippage_cost: float
    charges: float
    return_pct: float
    holding_days: int
    exit_reason: str
    gap_pct: float
    day_ret_pct: float
    vol_ratio: float


@dataclass
class Metrics:
    name: str
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


def ensure_output() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def load_ohlcv(start: date = START_2010, end: date = FINAL_END) -> pl.DataFrame:
    return ParquetOhlcvStore(DATA_ROOT).read_frame(
        start_date=start,
        end_date=end,
        columns=["date", "symbol", "open", "high", "low", "close", "volume", "turnover"],
    ).sort(["symbol", "date"])


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


def estimate_strategy_tax(trades: list[Trade]) -> float:
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


def estimate_index_tax(final_equity: float, start: date, end: date) -> float:
    gain = max(0.0, final_equity - INITIAL_CAPITAL)
    if (end - start).days >= 365:
        return max(0.0, gain - 125_000.0) * 0.125
    return gain * 0.20


def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0) * 100.0)


def sharpe(equity: np.ndarray) -> float:
    rets = np.diff(equity) / equity[:-1]
    if len(rets) > 2 and np.std(rets) > 0:
        return float(np.mean(rets) / np.std(rets) * math.sqrt(252))
    return 0.0


def build_features(df: pl.DataFrame) -> pl.DataFrame:
    base = df.with_columns(
        [
            pl.col("close").shift(1).over("symbol").alias("prev_close"),
            pl.col("volume").rolling_mean(20).over("symbol").alias("vol20"),
            pl.col("turnover").rolling_mean(20).over("symbol").alias("adv20"),
            pl.col("close").rolling_mean(50).over("symbol").alias("sma50"),
            pl.col("close").rolling_mean(200).over("symbol").alias("sma200"),
        ]
    )
    return base.with_columns(
        [
            ((pl.col("open") / pl.col("prev_close") - 1.0) * 100.0).alias("gap_pct"),
            ((pl.col("close") / pl.col("prev_close") - 1.0) * 100.0).alias("day_ret_pct"),
            (pl.col("volume") / pl.col("vol20")).alias("vol_ratio"),
            ((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low"))).fill_nan(0.5).alias("close_pos"),
            (pl.col("turnover").rank(descending=True).over("date")).alias("liquidity_rank"),
        ]
    )


def make_signals(features: pl.DataFrame, cfg: EPConfig, start: date, end: date) -> pl.DataFrame:
    expr = (
        (pl.col("date") >= start)
        & (pl.col("date") <= end)
        & (pl.col("close") >= cfg.min_price)
        & (pl.col("adv20") >= cfg.min_adv20)
        & (pl.col("volume") > 0)
        & (pl.col("vol_ratio") >= cfg.min_vol_ratio)
        & (pl.col("close_pos") >= cfg.min_close_pos)
        & ((pl.col("gap_pct") >= cfg.min_gap_pct) | (pl.col("day_ret_pct") >= cfg.min_day_ret_pct))
        & (pl.col("sma200").is_not_null())
    )
    if cfg.require_above_sma200:
        expr = expr & (pl.col("close") > pl.col("sma200"))
    return (
        features.filter(expr)
        .with_columns(
            (
                pl.col("gap_pct") * 2.0
                + pl.col("day_ret_pct")
                + pl.col("vol_ratio") * 3.0
                + pl.col("close_pos") * 5.0
            ).rank(descending=True).over("date").alias("event_rank")
        )
        .sort(["date", "event_rank", "liquidity_rank"])
        .select("date", "symbol", "gap_pct", "day_ret_pct", "vol_ratio", "event_rank")
    )


def market_dict(features: pl.DataFrame, start: date, end: date) -> dict[str, dict[date, dict[str, float]]]:
    sliced = features.filter((pl.col("date") >= start) & (pl.col("date") <= end)).select(
        "date", "symbol", "open", "high", "low", "close", "adv20", "sma50", "sma200"
    )
    data: dict[str, dict[date, dict[str, float]]] = defaultdict(dict)
    for row in sliced.iter_rows(named=True):
        data[row["symbol"]][row["date"]] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "adv20": float(row["adv20"]) if row["adv20"] is not None else 0.0,
            "sma50": float(row["sma50"]) if row["sma50"] is not None else math.nan,
            "sma200": float(row["sma200"]) if row["sma200"] is not None else math.nan,
        }
    return data


def simulate(features: pl.DataFrame, cfg: EPConfig, start: date, end: date) -> tuple[Metrics, list[Trade], list[dict[str, Any]]]:
    calendar = features.filter((pl.col("date") >= start) & (pl.col("date") <= end)).select("date").unique().sort("date")["date"].to_list()
    signals = make_signals(features, cfg, start, end)
    mkt = market_dict(features, start, end)
    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in signals.iter_rows(named=True):
        signals_by_date[row["date"]].append(row)

    cash = INITIAL_CAPITAL
    positions: dict[str, Position] = {}
    pending: dict[date, list[dict[str, Any]]] = defaultdict(list)
    trades: list[Trade] = []
    daily: list[dict[str, Any]] = []

    for idx, today in enumerate(calendar):
        for sig in signals_by_date.get(today, []):
            if idx + 1 < len(calendar):
                pending[calendar[idx + 1]].append(sig)

        for sym in list(positions):
            pos = positions[sym]
            bar = mkt.get(sym, {}).get(today)
            if bar is None:
                continue
            pos.days_held += 1
            pos.highest_close = max(pos.highest_close, bar["close"])
            pos.stop_raw = max(pos.stop_raw, pos.highest_close * (1.0 - cfg.trail_pct))
            exit_raw = None
            reason = ""
            if bar["open"] <= pos.stop_raw:
                exit_raw = bar["open"]
                reason = "stop_open"
            elif bar["low"] <= pos.stop_raw:
                exit_raw = pos.stop_raw
                reason = "stop"
            elif bar["close"] < bar["sma50"]:
                exit_raw = bar["close"]
                reason = "below_sma50"
            elif pos.days_held >= cfg.max_hold_days:
                exit_raw = bar["close"]
                reason = "time"
            if exit_raw is None:
                continue
            sell_raw = exit_raw * (1.0 - cfg.slippage_bps / 10_000.0)
            gross = sell_raw * pos.shares
            sell_cost = sell_charges(gross)
            proceeds = gross - sell_cost
            cash += proceeds
            buy_raw_no_slip = pos.entry_raw / (1.0 + cfg.slippage_bps / 10_000.0)
            buy_slip = (pos.entry_raw - buy_raw_no_slip) * pos.shares
            sell_slip = (exit_raw - sell_raw) * pos.shares
            net_pnl = proceeds - pos.cost
            meta = getattr(pos, "meta", {})
            trades.append(
                Trade(
                    symbol=sym,
                    signal_date=pos.signal_date.isoformat(),
                    entry_date=pos.entry_date.isoformat(),
                    exit_date=today.isoformat(),
                    entry_price=pos.entry_net,
                    exit_price=sell_raw,
                    shares=pos.shares,
                    net_pnl=net_pnl,
                    gross_pnl_no_friction=(exit_raw - buy_raw_no_slip) * pos.shares,
                    slippage_cost=buy_slip + sell_slip,
                    charges=pos.buy_charges + sell_cost,
                    return_pct=net_pnl / pos.cost * 100.0,
                    holding_days=(today - pos.entry_date).days,
                    exit_reason=reason,
                    gap_pct=float(meta.get("gap_pct", 0.0)),
                    day_ret_pct=float(meta.get("day_ret_pct", 0.0)),
                    vol_ratio=float(meta.get("vol_ratio", 0.0)),
                )
            )
            del positions[sym]

        entries = sorted(pending.get(today, []), key=lambda row: (row["event_rank"], row["symbol"]))
        for sig in entries:
            if len(positions) >= cfg.max_positions or sig["symbol"] in positions:
                continue
            bar = mkt.get(sig["symbol"], {}).get(today)
            if bar is None:
                continue
            buy_raw = bar["open"] * (1.0 + cfg.slippage_bps / 10_000.0)
            max_notional = min(cash * cfg.position_pct, bar["adv20"] * cfg.max_adv_pct)
            shares = int(max_notional // buy_raw)
            if shares <= 0:
                continue
            gross = buy_raw * shares
            bchg = buy_charges(gross)
            total = gross + bchg
            if total > cash:
                continue
            cash -= total
            pos = Position(
                symbol=sig["symbol"],
                signal_date=sig["date"],
                entry_date=today,
                entry_raw=buy_raw,
                entry_net=total / shares,
                shares=shares,
                cost=total,
                buy_charges=bchg,
                stop_raw=buy_raw * (1.0 - cfg.initial_stop_pct),
                highest_close=bar["close"],
            )
            setattr(pos, "meta", {"gap_pct": sig["gap_pct"], "day_ret_pct": sig["day_ret_pct"], "vol_ratio": sig["vol_ratio"]})
            positions[sig["symbol"]] = pos

        mtm = 0.0
        for sym, pos in positions.items():
            bar = mkt.get(sym, {}).get(today)
            mtm += (bar["close"] if bar is not None else pos.entry_raw) * pos.shares
        daily.append({"date": today.isoformat(), "equity": cash + mtm, "cash": cash, "positions": len(positions)})

    if positions and daily:
        last = calendar[-1]
        for sym, pos in list(positions.items()):
            bar = mkt.get(sym, {}).get(last)
            if bar is None:
                continue
            sell_raw = bar["close"] * (1.0 - cfg.slippage_bps / 10_000.0)
            gross = sell_raw * pos.shares
            sell_cost = sell_charges(gross)
            proceeds = gross - sell_cost
            cash += proceeds
            net_pnl = proceeds - pos.cost
            meta = getattr(pos, "meta", {})
            trades.append(
                Trade(
                    symbol=sym,
                    signal_date=pos.signal_date.isoformat(),
                    entry_date=pos.entry_date.isoformat(),
                    exit_date=last.isoformat(),
                    entry_price=pos.entry_net,
                    exit_price=sell_raw,
                    shares=pos.shares,
                    net_pnl=net_pnl,
                    gross_pnl_no_friction=(bar["close"] - pos.entry_raw / (1.0 + cfg.slippage_bps / 10_000.0)) * pos.shares,
                    slippage_cost=(pos.entry_raw - pos.entry_raw / (1.0 + cfg.slippage_bps / 10_000.0)) * pos.shares + (bar["close"] - sell_raw) * pos.shares,
                    charges=pos.buy_charges + sell_cost,
                    return_pct=net_pnl / pos.cost * 100.0,
                    holding_days=(last - pos.entry_date).days,
                    exit_reason="liquidate_end",
                    gap_pct=float(meta.get("gap_pct", 0.0)),
                    day_ret_pct=float(meta.get("day_ret_pct", 0.0)),
                    vol_ratio=float(meta.get("vol_ratio", 0.0)),
                )
            )
        daily[-1]["equity"] = cash

    return metrics(cfg.name, daily, trades, start, end), trades, daily


def metrics(name: str, daily: list[dict[str, Any]], trades: list[Trade], start: date, end: date) -> Metrics:
    eq = np.array([float(row["equity"]) for row in daily], dtype=float)
    years = (end - start).days / 365.25
    final = float(eq[-1]) if len(eq) else INITIAL_CAPITAL
    tax = estimate_strategy_tax(trades)
    post_tax_final = final - tax
    wins = [t for t in trades if t.net_pnl > 0]
    turnover = sum((t.entry_price + t.exit_price) * t.shares for t in trades) / INITIAL_CAPITAL
    return Metrics(
        name=name,
        start=start.isoformat(),
        end=end.isoformat(),
        cagr=(final / INITIAL_CAPITAL) ** (1 / years) - 1,
        post_tax_cagr=(post_tax_final / INITIAL_CAPITAL) ** (1 / years) - 1 if post_tax_final > 0 else -1.0,
        total_return_pct=(final / INITIAL_CAPITAL - 1) * 100,
        max_drawdown_pct=max_drawdown(eq),
        sharpe=sharpe(eq),
        win_rate=len(wins) / len(trades) * 100 if trades else 0.0,
        avg_holding_days=statistics.mean([t.holding_days for t in trades]) if trades else 0.0,
        turnover=turnover,
        trades=len(trades),
        final_equity=final,
    )


def benchmark_tri(start: date, end: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tri = pl.read_csv(TRI_PATH, try_parse_dates=True).filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
    dates = tri["date"].to_list()
    vals = np.array(tri["tri"].to_list(), dtype=float)
    eq = INITIAL_CAPITAL * vals / vals[0]
    years = (dates[-1] - dates[0]).days / 365.25
    final = float(eq[-1])
    tax = estimate_index_tax(final, dates[0], dates[-1])
    post_tax_final = final - tax
    return (
        {
            "name": "Nifty 500 TRI local reference",
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "cagr": (final / INITIAL_CAPITAL) ** (1 / years) - 1,
            "post_tax_cagr": (post_tax_final / INITIAL_CAPITAL) ** (1 / years) - 1,
            "total_return_pct": (final / INITIAL_CAPITAL - 1) * 100,
            "post_tax_total_return_pct": (post_tax_final / INITIAL_CAPITAL - 1) * 100,
            "max_drawdown_pct": max_drawdown(eq),
            "sharpe": sharpe(eq),
            "final_equity": final,
            "post_tax_final_equity": post_tax_final,
            "tax": tax,
        },
        [{"date": d.isoformat(), "equity": float(e)} for d, e in zip(dates, eq)],
    )


def configs() -> list[EPConfig]:
    return [
        EPConfig("ep_gap6_vol3_hold60", 6.0, 8.0, 3.0, 0.65, 30_000_000, 20.0, 10, 0.10, 0.015, 0.10, 0.15, 60, 20.0),
        EPConfig("ep_gap8_vol4_hold60", 8.0, 10.0, 4.0, 0.70, 30_000_000, 20.0, 10, 0.10, 0.015, 0.10, 0.15, 60, 20.0),
        EPConfig("ep_gap10_vol5_hold90", 10.0, 12.0, 5.0, 0.70, 25_000_000, 20.0, 8, 0.125, 0.015, 0.12, 0.18, 90, 25.0),
        EPConfig("ep_day12_vol4_hold45", 99.0, 12.0, 4.0, 0.75, 30_000_000, 20.0, 10, 0.10, 0.015, 0.10, 0.12, 45, 20.0),
        EPConfig("ep_liquid_gap5_vol25", 5.0, 7.0, 2.5, 0.65, 50_000_000, 30.0, 10, 0.10, 0.01, 0.10, 0.15, 60, 15.0),
    ]


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(summary: dict[str, Any]) -> None:
    strat = summary["holdout"]["strategy"]
    bench = summary["holdout"]["benchmark"]
    spread = summary["holdout"]["spread"]
    verdict = (
        "Index the money."
        if strat["post_tax_cagr"] <= bench["post_tax_cagr"] or strat["sharpe"] <= bench["sharpe"]
        else "Promising, but paper-trade first."
    )
    lines = [
        "# Episodic Pivot Research",
        "",
        f"Headline: episodic pivots {('do not beat' if verdict.startswith('Index') else 'beat')} the local TRI benchmark after tax and risk adjustment; {verdict}",
        "",
        "## Definition",
        "- Large positive gap or large day return.",
        "- Exceptional volume relative to 20-day average.",
        "- Strong close in the event-day range.",
        "- Entry at next trading day open; no same-day fills.",
        "",
        "## Selected Config",
        f"- `{summary['selected_config']['name']}`",
        f"- gap >= {summary['selected_config']['min_gap_pct']}%, day return >= {summary['selected_config']['min_day_ret_pct']}%, volume ratio >= {summary['selected_config']['min_vol_ratio']}x.",
        f"- Max hold {summary['selected_config']['max_hold_days']} days, initial stop {summary['selected_config']['initial_stop_pct']:.0%}, trail {summary['selected_config']['trail_pct']:.0%}.",
        "",
        "## Holdout 2023-2026-03-17",
        f"- Strategy: post-tax CAGR {strat['post_tax_cagr']:.2%}, pre-tax CAGR {strat['cagr']:.2%}, MDD {strat['max_drawdown_pct']:.1f}%, Sharpe {strat['sharpe']:.2f}, trades {strat['trades']}.",
        f"- Benchmark: post-tax CAGR {bench['post_tax_cagr']:.2%}, pre-tax CAGR {bench['cagr']:.2%}, MDD {bench['max_drawdown_pct']:.1f}%, Sharpe {bench['sharpe']:.2f}.",
        f"- Spread: post-tax CAGR {spread['post_tax_cagr']:.2%}, MDD {spread['max_drawdown_pct_points']:.1f} pct-pts, Sharpe {spread['sharpe']:.2f}.",
        "",
        "## Train Ranking 2015-2022",
    ]
    for row in summary["train_results"]:
        lines.append(f"- {row['name']}: post-tax CAGR {row['post_tax_cagr']:.2%}, MDD {row['max_drawdown_pct']:.1f}%, Sharpe {row['sharpe']:.2f}, trades {row['trades']}.")
    lines.extend(
        [
            "",
            "## Verdict",
            verdict,
            "",
            "A clean no is still cleaner than an overfit yes.",
        ]
    )
    (OUTPUT_ROOT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run() -> None:
    ensure_output()
    df = load_ohlcv()
    features = build_features(df)
    train_rows = []
    for cfg in configs():
        m, _, _ = simulate(features, cfg, TRAIN_START, TRAIN_END)
        train_rows.append({**asdict(m), "config": asdict(cfg)})
    train_rows = sorted(train_rows, key=lambda row: (row["post_tax_cagr"], row["sharpe"]), reverse=True)
    selected = EPConfig(**train_rows[0]["config"])

    train_metrics, train_trades, train_daily = simulate(features, selected, TRAIN_START, TRAIN_END)
    hold_metrics, hold_trades, hold_daily = simulate(features, selected, HOLDOUT_START, FINAL_END)
    bench, bench_daily = benchmark_tri(HOLDOUT_START, FINAL_END)
    spread = {
        "post_tax_cagr": hold_metrics.post_tax_cagr - bench["post_tax_cagr"],
        "pre_tax_cagr": hold_metrics.cagr - bench["cagr"],
        "max_drawdown_pct_points": hold_metrics.max_drawdown_pct - bench["max_drawdown_pct"],
        "sharpe": hold_metrics.sharpe - bench["sharpe"],
    }
    summary = {
        "selected_config": asdict(selected),
        "train": asdict(train_metrics),
        "holdout": {"strategy": asdict(hold_metrics), "benchmark": bench, "spread": spread},
        "train_results": train_rows,
    }
    (OUTPUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(OUTPUT_ROOT / "train_results.csv", train_rows)
    write_csv(OUTPUT_ROOT / "train_trades.csv", [asdict(t) for t in train_trades])
    write_csv(OUTPUT_ROOT / "holdout_trades.csv", [asdict(t) for t in hold_trades])
    write_csv(OUTPUT_ROOT / "holdout_daily_equity.csv", hold_daily)
    write_csv(OUTPUT_ROOT / "benchmark_daily_equity.csv", bench_daily)
    write_report(summary)
    print(json.dumps(summary["holdout"], indent=2))


if __name__ == "__main__":
    run()

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.ranking import load_sector_map
from strategies.mean_reversion.exit_engine import MRExitConfig
from strategies.mean_reversion.signal_engine import scan_mean_reversion, scan_mr_v2, scan_short_mean_reversion
from strategies.mean_reversion.simulation import simulate_mr_long_short_portfolio, simulate_mr_portfolio
from strategies.mean_reversion.universe import (
    filter_signals_to_historical_membership,
    historical_liquidity_membership,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connors RSI2 mean reversion diagnostics.")
    parser.add_argument("--start", default="2010-01-04")
    parser.add_argument("--end", default="2026-03-17")
    parser.add_argument("--output-dir", default="reports/mean_reversion")
    parser.add_argument("--config", default="strategies/mean_reversion/config.yaml")
    parser.add_argument("--sweep", action="store_true", help="Run staged parameter sweep")
    parser.add_argument("--sweep-v2", action="store_true", help="Run research-driven v2 comparison sweep")
    parser.add_argument("--short-side", action="store_true", help="Run mirrored short-side long/short study")
    parser.add_argument("--regime-study", action="store_true", help="Run bull/bear regime comparison using default config")
    parser.add_argument("--cost", type=float, default=0.0, help="Round-trip transaction cost as decimal (e.g. 0.002 = 0.2%%)")
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_mr_config(path: Path = Path("strategies/mean_reversion/config.yaml")) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _safe_div(num: float, denom: float) -> float | None:
    if denom == 0.0:
        return None
    return num / denom


def _max_streak(values: list[float], *, wins: bool) -> int:
    best = 0
    current = 0
    for value in values:
        passed = value > 0 if wins else value < 0
        if passed:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def compute_metrics(daily_equity: pl.DataFrame, trades: pl.DataFrame) -> dict[str, Any]:
    if daily_equity.is_empty():
        return {}

    equities = [float(value) for value in daily_equity["equity"].to_list()]
    start_equity = equities[0]
    end_equity = equities[-1]
    trading_days = len(equities)
    years = max(trading_days / 252.0, 1 / 252.0)
    total_return_pct = ((end_equity / start_equity) - 1.0) * 100.0 if start_equity > 0 else 0.0
    cagr_pct = ((end_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0 if start_equity > 0 else 0.0
    max_drawdown_pct = min(float(value) for value in daily_equity["drawdown_pct"].to_list())
    calmar = _safe_div(cagr_pct, abs(max_drawdown_pct)) if max_drawdown_pct < 0 else None

    daily_returns = [(equities[idx] / equities[idx - 1]) - 1.0 for idx in range(1, len(equities)) if equities[idx - 1] > 0]
    avg_daily = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    variance = sum((value - avg_daily) ** 2 for value in daily_returns) / (len(daily_returns) - 1) if len(daily_returns) > 1 else 0.0
    downside = [value for value in daily_returns if value < 0]
    downside_var = sum(value**2 for value in downside) / (len(downside) - 1) if len(downside) > 1 else 0.0
    sharpe = (avg_daily / math.sqrt(variance) * math.sqrt(252.0)) if variance > 0 else None
    sortino = (avg_daily / math.sqrt(downside_var) * math.sqrt(252.0)) if downside_var > 0 else None

    if trades.is_empty():
        return {
            "start_equity": start_equity,
            "final_equity": end_equity,
            "total_return_pct": total_return_pct,
            "cagr_pct": cagr_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "calmar": calmar,
            "sharpe": sharpe,
            "sortino": sortino,
            "trading_days": trading_days,
            "fills": 0,
        }

    r_values = [float(value) for value in trades["r_multiple"].to_list()]
    winners = [value for value in r_values if value > 0]
    losers = [value for value in r_values if value < 0]
    pnl_values = [float(value) for value in trades["pnl"].to_list()]
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    avg_win = sum(winners) / len(winners) if winners else 0.0
    avg_loss = abs(sum(losers) / len(losers)) if losers else 0.0
    win_rate = len(winners) / len(r_values) if r_values else 0.0
    holding_days = [int(value) for value in trades["holding_days"].to_list()]
    winner_holds = [int(row["holding_days"]) for row in trades.filter(pl.col("r_multiple") > 0).to_dicts()]
    loser_holds = [int(row["holding_days"]) for row in trades.filter(pl.col("r_multiple") < 0).to_dicts()]

    return {
        "start_equity": start_equity,
        "final_equity": end_equity,
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "calmar": calmar,
        "sharpe": sharpe,
        "sortino": sortino,
        "trading_days": trading_days,
        "fills": len(r_values),
        "win_rate": win_rate,
        "average_r": sum(r_values) / len(r_values) if r_values else None,
        "median_r": median(r_values) if r_values else None,
        "payoff_ratio": _safe_div(avg_win, avg_loss),
        "expectancy": (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss),
        "max_consecutive_wins": _max_streak(r_values, wins=True),
        "max_consecutive_losses": _max_streak(r_values, wins=False),
        "avg_holding_days": sum(holding_days) / len(holding_days) if holding_days else None,
        "avg_holding_days_winners": sum(winner_holds) / len(winner_holds) if winner_holds else None,
        "avg_holding_days_losers": sum(loser_holds) / len(loser_holds) if loser_holds else None,
        "profit_factor": _safe_div(gross_profit, gross_loss),
    }


def yearly_returns(daily_equity: pl.DataFrame, trades: pl.DataFrame) -> pl.DataFrame:
    if daily_equity.is_empty():
        return pl.DataFrame()

    rows: list[dict[str, Any]] = []
    for year, group in daily_equity.with_columns(pl.col("date").dt.year().alias("year")).group_by("year", maintain_order=True):
        year_int = int(year[0] if isinstance(year, tuple) else year)
        start_equity = float(group["equity"][0])
        end_equity = float(group["equity"][-1])
        year_trades = trades.filter(pl.col("exit_date").dt.year() == year_int) if not trades.is_empty() else trades
        wins = year_trades.filter(pl.col("r_multiple") > 0).height if not year_trades.is_empty() else 0
        fills = year_trades.height if not year_trades.is_empty() else 0
        rows.append(
            {
                "year": year_int,
                "start_equity": start_equity,
                "end_equity": end_equity,
                "return_pct": ((end_equity / start_equity) - 1.0) * 100.0 if start_equity > 0 else 0.0,
                "max_drawdown_pct": min(float(value) for value in group["drawdown_pct"].to_list()),
                "fills": fills,
                "win_rate": wins / fills if fills else 0.0,
            }
        )
    return pl.DataFrame(rows).sort("year")


def monthly_returns(daily_equity: pl.DataFrame) -> pl.DataFrame:
    if daily_equity.is_empty():
        return pl.DataFrame()
    by_year: dict[int, dict[str, Any]] = {}
    monthly = daily_equity.with_columns(
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    )
    for keys, group in monthly.group_by(["year", "month"], maintain_order=True):
        year, month = keys
        start_equity = float(group["equity"][0])
        end_equity = float(group["equity"][-1])
        year_int = int(year)
        by_year.setdefault(year_int, {"year": year_int})
        by_year[year_int][f"m{int(month):02d}"] = ((end_equity / start_equity) - 1.0) * 100.0
    rows = []
    for year in sorted(by_year):
        row = {"year": year}
        row.update({f"m{month:02d}": by_year[year].get(f"m{month:02d}") for month in range(1, 13)})
        rows.append(row)
    return pl.DataFrame(rows)


def r_distribution(trades: pl.DataFrame) -> pl.DataFrame:
    bins = [
        ("<-2R", lambda value: value < -2.0),
        ("-2R_to_-1R", lambda value: -2.0 <= value < -1.0),
        ("-1R_to_0R", lambda value: -1.0 <= value < 0.0),
        ("0R_to_1R", lambda value: 0.0 <= value < 1.0),
        ("1R_to_2R", lambda value: 1.0 <= value < 2.0),
        (">2R", lambda value: value >= 2.0),
    ]
    values = [float(value) for value in trades["r_multiple"].to_list()] if not trades.is_empty() else []
    return pl.DataFrame(
        [{"bucket": label, "count": sum(1 for value in values if predicate(value))} for label, predicate in bins]
    )


def streak_distribution(trades: pl.DataFrame) -> pl.DataFrame:
    if trades.is_empty():
        return pl.DataFrame({"type": [], "length": [], "count": []})

    rows: list[dict[str, Any]] = []
    current_type: str | None = None
    current_len = 0
    values = [float(value) for value in trades.sort(["exit_date", "symbol"])["r_multiple"].to_list()]
    for value in values:
        streak_type = "win" if value > 0 else "loss" if value < 0 else "flat"
        if streak_type == current_type:
            current_len += 1
        else:
            if current_type is not None:
                rows.append({"type": current_type, "length": current_len})
            current_type = streak_type
            current_len = 1
    if current_type is not None:
        rows.append({"type": current_type, "length": current_len})
    if not rows:
        return pl.DataFrame({"type": [], "length": [], "count": []})
    return pl.DataFrame(rows).group_by(["type", "length"]).len().rename({"len": "count"}).sort(["type", "length"])


def load_ohlcv(
    config: dict[str, Any],
    start_date: date,
    end_date: date,
    universe: set[str] | None = None,
) -> pl.DataFrame:
    ohlcv_path = Path(config["data"]["ohlcv_path"])
    store = ParquetOhlcvStore(ohlcv_path.parent)
    frame = store.read_frame(start_date=None, end_date=end_date, columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    benchmark_symbols = {
        str(config.get("data", {}).get("benchmark_symbol", "")),
        str(config.get("market", {}).get("benchmark_symbol", "")),
        "^NSEI",
        "NIFTY 50",
        "NIFTYBEES",
    }
    # A static symbol list is suitable only for a live diagnostic.  Historical
    # callers pass None and apply dated point-in-time membership after loading.
    if universe is not None:
        keep_symbols = set(universe) | {symbol for symbol in benchmark_symbols if symbol}
        frame = frame.filter(pl.col("symbol").is_in(sorted(keep_symbols)))
    if frame.is_empty():
        return frame
    # Keep pre-start history for indicators, but all output signals are filtered later.
    earliest_needed = min(start_date, frame["date"].min())
    return frame.filter(pl.col("date") >= earliest_needed).sort(["symbol", "date"])


def _filter_to_pit_liquidity_universe(signals: pl.DataFrame, ohlcv: pl.DataFrame) -> pl.DataFrame:
    """Apply dated liquidity membership after calculating signal-date features."""
    membership = historical_liquidity_membership(
        ohlcv,
        size=100,
        lookback_sessions=50,
        min_history_sessions=200,
        reconstitute="semiannual",
    )
    return filter_signals_to_historical_membership(signals, membership)


def run_variant(
    *,
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    rsi_threshold: float,
    exit_mode: str,
    time_stop_days: int,
    stop_atr_mult: float,
    risk_per_trade_pct: float,
    round_trip_cost_pct: float = 0.0,
    regime: str = "all",
) -> dict[str, Any]:
    signals = scan_mean_reversion(
        ohlcv,
        rsi_period=int(config["entry"]["rsi_period"]),
        rsi_threshold=rsi_threshold,
        sma_trend_period=int(config["entry"]["sma_trend_period"]),
        min_adv_inr=float(config["entry"]["min_adv_inr"]),
        min_price=float(config["entry"]["min_price_inr"]),
        stop_atr_mult=stop_atr_mult,
    )
    signals = _filter_to_pit_liquidity_universe(signals, ohlcv).filter(
        (pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= end_date)
    )

    if regime != "all":
        from strategies.mean_reversion.simulation import build_benchmark_regime

        regime_map = build_benchmark_regime(ohlcv)
        if regime == "bull":
            allowed = {d for d, above in regime_map.items() if above}
        else:
            allowed = {d for d, above in regime_map.items() if not above}
        signals = signals.filter(pl.col("signal_date").is_in(sorted(allowed)))

    exit_config = MRExitConfig(
        exit_mode=exit_mode,
        time_stop_days=time_stop_days,
        stop_atr_mult=stop_atr_mult,
        max_hold_days=int(config["exit"].get("max_hold_days", 20)),
    )
    result = simulate_mr_portfolio(
        signals,
        ohlcv.filter(pl.col("date") <= end_date),
        start_date=start_date,
        end_date=end_date,
        max_positions=int(config["portfolio"]["max_positions"]),
        max_sector_positions=int(config["portfolio"]["max_sector_positions"]),
        initial_capital=float(config["portfolio"]["initial_capital"]),
        risk_per_trade_pct=risk_per_trade_pct,
        exit_config=exit_config,
        sector_map=sector_map,
        round_trip_cost_pct=round_trip_cost_pct,
    )
    metrics = compute_metrics(result["daily_equity"], result["trades"])
    metrics.update(
        {
            "signals": int(signals.height),
            "filled_signals": result["filled_signals"],
            "fill_rate": result["filled_signals"] / signals.height if signals.height else 0.0,
            "expired_signals": result["expired_signals"],
            "skipped_signals": result["skipped_signals"],
            "rsi_threshold": rsi_threshold,
            "exit_mode": exit_mode,
            "time_stop_days": time_stop_days,
            "stop_atr_mult": stop_atr_mult,
            "risk_per_trade_pct": risk_per_trade_pct,
        }
    )
    return {"signals": signals, **result, "metrics": metrics}


def run_variant_v2(
    *,
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    label: str,
    entry_indicator: str,
    entry_threshold: float,
    exit_mode: str,
    stop_atr_mult: float,
    risk_per_trade_pct: float,
    round_trip_cost_pct: float,
    min_days_down: int | None = None,
    days_down_lookback: int = 5,
    max_ibs: float | None = None,
    max_adx: float | None = None,
    max_vol_rank: float | None = None,
    vol_rank_period: int = 20,
    max_signals_per_day: int | None = None,
    cooldown_days: int = 0,
    sector_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    signals = scan_mr_v2(
        ohlcv,
        entry_indicator=entry_indicator,
        entry_threshold=entry_threshold,
        sma_trend_period=int(config["entry"]["sma_trend_period"]),
        min_days_down=min_days_down,
        days_down_lookback=days_down_lookback,
        max_ibs=max_ibs,
        max_adx=max_adx,
        max_vol_rank=max_vol_rank,
        vol_rank_period=vol_rank_period,
        min_adv_inr=float(config["entry"]["min_adv_inr"]),
        min_price=float(config["entry"]["min_price_inr"]),
        stop_atr_mult=stop_atr_mult,
        max_signals_per_day=max_signals_per_day,
        cooldown_days=cooldown_days,
    )
    signals = _filter_to_pit_liquidity_universe(signals, ohlcv).filter(
        (pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= end_date)
    )

    exit_config = MRExitConfig(
        exit_mode=exit_mode,
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=stop_atr_mult,
        max_hold_days=int(config["exit"].get("max_hold_days", 20)),
    )
    result = simulate_mr_portfolio(
        signals,
        ohlcv.filter(pl.col("date") <= end_date),
        start_date=start_date,
        end_date=end_date,
        max_positions=int(config["portfolio"]["max_positions"]),
        max_sector_positions=int(config["portfolio"]["max_sector_positions"]),
        initial_capital=float(config["portfolio"]["initial_capital"]),
        risk_per_trade_pct=risk_per_trade_pct,
        exit_config=exit_config,
        sector_map=sector_map,
        round_trip_cost_pct=round_trip_cost_pct,
        cooldown_days=cooldown_days,
        sector_weights=sector_weights,
    )
    metrics = compute_metrics(result["daily_equity"], result["trades"])
    metrics.update(
        {
            "variant": label,
            "signals": int(signals.height),
            "filled_signals": result["filled_signals"],
            "fill_rate": result["filled_signals"] / signals.height if signals.height else 0.0,
            "expired_signals": result["expired_signals"],
            "skipped_signals": result["skipped_signals"],
            "entry_indicator": entry_indicator,
            "entry_threshold": entry_threshold,
            "exit_mode": exit_mode,
            "stop_atr_mult": stop_atr_mult,
            "risk_per_trade_pct": risk_per_trade_pct,
            "min_days_down": min_days_down,
            "days_down_lookback": days_down_lookback,
            "max_ibs": max_ibs,
            "max_adx": max_adx,
            "max_vol_rank": max_vol_rank,
            "vol_rank_period": vol_rank_period,
            "max_signals_per_day": max_signals_per_day,
            "cooldown_days": cooldown_days,
            "round_trip_cost_pct": round_trip_cost_pct,
        }
    )
    return {"signals": signals, **result, "metrics": metrics}


def write_outputs(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_equity: pl.DataFrame = result["daily_equity"]
    trades: pl.DataFrame = result["trades"]
    daily_equity.write_csv(output_dir / "daily_equity.csv")
    trades.sort(["exit_date", "symbol"]).write_csv(output_dir / "trade_pnl.csv")
    yearly_returns(daily_equity, trades).write_csv(output_dir / "yearly_returns.csv")
    monthly_returns(daily_equity).write_csv(output_dir / "monthly_returns.csv")
    r_distribution(trades).write_csv(output_dir / "r_distribution.csv")
    streak_distribution(trades).write_csv(output_dir / "streak_distribution.csv")
    (output_dir / "summary.json").write_text(json.dumps(result["metrics"], indent=2, default=str), encoding="utf-8")

    if not trades.is_empty():
        trades.sort("r_multiple", descending=True).head(10).write_csv(output_dir / "best_10_trades.csv")
        trades.sort("r_multiple").head(10).write_csv(output_dir / "worst_10_trades.csv")
    signals: pl.DataFrame = result["signals"]
    if signals.is_empty():
        pl.DataFrame({"year": [], "len": []}).write_csv(output_dir / "signals_per_year.csv")
    else:
        signals.with_columns(pl.col("signal_date").dt.year().alias("year")).group_by("year").len().sort("year").write_csv(
            output_dir / "signals_per_year.csv"
        )


def print_summary(metrics: dict[str, Any]) -> None:
    print(json.dumps(metrics, indent=2, default=str))
    avg_hold = metrics.get("avg_holding_days")
    fill_rate = metrics.get("fill_rate", 0.0)
    win_rate = metrics.get("win_rate", 0.0)
    payoff = metrics.get("payoff_ratio")
    print("\nVerification checks:")
    print(f"- Avg holding days 2-7 target: {avg_hold}")
    print(f"- Fill rate not 0/100% target: {fill_rate:.2%}")
    print(f"- Win rate >55% target: {win_rate:.2%}")
    print(f"- Payoff ratio typically <2: {payoff}")
    print("- Historical liquidity membership is point-in-time and based on prior observations only.")


def sweep_row(result: dict[str, Any], label: str) -> dict[str, Any]:
    metrics = result["metrics"]
    return {
        "variant": label,
        "signals": metrics.get("signals", 0),
        "fills": metrics.get("fills", 0),
        "cagr_pct": metrics.get("cagr_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "calmar": metrics.get("calmar"),
        "win_rate": metrics.get("win_rate"),
        "average_r": metrics.get("average_r"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "avg_holding_days": metrics.get("avg_holding_days"),
        "rsi_threshold": metrics.get("rsi_threshold"),
        "exit_mode": metrics.get("exit_mode"),
        "time_stop_days": metrics.get("time_stop_days"),
        "stop_atr_mult": metrics.get("stop_atr_mult"),
        "risk_per_trade_pct": metrics.get("risk_per_trade_pct"),
    }


def best_by_calmar(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: float(row["calmar"] if row["calmar"] is not None else -9999.0))


def run_sweep(config: dict[str, Any], ohlcv: pl.DataFrame, universe: set[str], sector_map: dict[str, str], start_date: date, end_date: date, output_dir: Path, round_trip_cost_pct: float = 0.0) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []

    phase1_rows = []
    exit_specs = [("sma5", 5), ("sma3", 5), ("rsi_70", 5), ("rsi_50", 5), ("time_stop", 3), ("time_stop", 5), ("time_stop", 7), ("combined", 5)]
    print("=== PHASE 1: EXIT MODE SWEEP (rsi_thresh=10, atr_mult=2.0, risk=2%) ===")
    for exit_mode, time_stop_days in exit_specs:
        label = f"{exit_mode}_{time_stop_days}" if exit_mode == "time_stop" else exit_mode
        result = run_variant(config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map, start_date=start_date, end_date=end_date, rsi_threshold=10, exit_mode=exit_mode, time_stop_days=time_stop_days, stop_atr_mult=2.0, risk_per_trade_pct=0.02, round_trip_cost_pct=round_trip_cost_pct)
        row = sweep_row(result, label)
        phase1_rows.append(row)
        all_rows.append(row)
        print(row)
    pl.DataFrame(phase1_rows).write_csv(output_dir / "phase1_exit_mode.csv")
    best_exit = best_by_calmar(phase1_rows)

    phase2_rows = []
    print("\n=== PHASE 2: RSI THRESHOLD SWEEP ===")
    for threshold in (5, 10, 15, 20, 25):
        result = run_variant(config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map, start_date=start_date, end_date=end_date, rsi_threshold=threshold, exit_mode=str(best_exit["exit_mode"]), time_stop_days=int(best_exit["time_stop_days"]), stop_atr_mult=2.0, risk_per_trade_pct=0.02, round_trip_cost_pct=round_trip_cost_pct)
        row = sweep_row(result, f"rsi_{threshold}")
        phase2_rows.append(row)
        all_rows.append(row)
        print(row)
    pl.DataFrame(phase2_rows).write_csv(output_dir / "phase2_rsi_threshold.csv")
    best_rsi = best_by_calmar(phase2_rows)

    phase3_rows = []
    print("\n=== PHASE 3: STOP DISTANCE SWEEP ===")
    for atr_mult in (1.5, 2.0, 2.5, 3.0):
        result = run_variant(config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map, start_date=start_date, end_date=end_date, rsi_threshold=float(best_rsi["rsi_threshold"]), exit_mode=str(best_rsi["exit_mode"]), time_stop_days=int(best_rsi["time_stop_days"]), stop_atr_mult=atr_mult, risk_per_trade_pct=0.02, round_trip_cost_pct=round_trip_cost_pct)
        row = sweep_row(result, f"atr_{atr_mult}")
        phase3_rows.append(row)
        all_rows.append(row)
        print(row)
    pl.DataFrame(phase3_rows).write_csv(output_dir / "phase3_stop_distance.csv")
    best_stop = best_by_calmar(phase3_rows)

    phase4_rows = []
    print("\n=== PHASE 4: POSITION SIZING SWEEP ===")
    for risk in (0.01, 0.02, 0.03, 0.05):
        result = run_variant(config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map, start_date=start_date, end_date=end_date, rsi_threshold=float(best_stop["rsi_threshold"]), exit_mode=str(best_stop["exit_mode"]), time_stop_days=int(best_stop["time_stop_days"]), stop_atr_mult=float(best_stop["stop_atr_mult"]), risk_per_trade_pct=risk, round_trip_cost_pct=round_trip_cost_pct)
        row = sweep_row(result, f"risk_{risk}")
        phase4_rows.append(row)
        all_rows.append(row)
        print(row)
    pl.DataFrame(phase4_rows).write_csv(output_dir / "phase4_position_sizing.csv")
    best_final = best_by_calmar(phase4_rows)
    pl.DataFrame(all_rows).write_csv(output_dir / "sweep_summary.csv")

    final_result = run_variant(config=config, ohlcv=ohlcv, universe=universe, sector_map=sector_map, start_date=start_date, end_date=end_date, rsi_threshold=float(best_final["rsi_threshold"]), exit_mode=str(best_final["exit_mode"]), time_stop_days=int(best_final["time_stop_days"]), stop_atr_mult=float(best_final["stop_atr_mult"]), risk_per_trade_pct=float(best_final["risk_per_trade_pct"]), round_trip_cost_pct=round_trip_cost_pct)
    write_outputs(output_dir / "final_best_combo", final_result)
    print("\nFinal best combination:")
    print_summary(final_result["metrics"])


def cost_label(cost: float) -> str:
    return f"{cost * 100:.1f}pct"


def comparison_row(result: dict[str, Any], label: str, cost: float) -> dict[str, Any]:
    metrics = result["metrics"]
    return {
        "version": label,
        "cost_pct": cost * 100.0,
        "signals": metrics.get("signals", 0),
        "fills": metrics.get("fills", 0),
        "cagr_pct": metrics.get("cagr_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "calmar": metrics.get("calmar"),
        "win_rate": metrics.get("win_rate"),
        "average_r": metrics.get("average_r"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "avg_holding_days": metrics.get("avg_holding_days"),
        "entry_indicator": metrics.get("entry_indicator", "rsi2"),
        "entry_threshold": metrics.get("entry_threshold", metrics.get("rsi_threshold")),
        "exit_mode": metrics.get("exit_mode"),
        "stop_atr_mult": metrics.get("stop_atr_mult"),
        "risk_per_trade_pct": metrics.get("risk_per_trade_pct"),
        "max_signals_per_day": metrics.get("max_signals_per_day"),
        "cooldown_days": metrics.get("cooldown_days", 0),
    }


def print_comparison_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'Version':<18} {'Fills':>7} {'CAGR':>9} {'MaxDD':>9} {'Calmar':>8} {'WR':>8} {'AvgR':>9} {'Payoff':>8} {'AvgHold':>9} {'Signals':>9}")
    for row in rows:
        cagr = float(row["cagr_pct"] or 0.0)
        maxdd = float(row["max_drawdown_pct"] or 0.0)
        calmar = float(row["calmar"] or 0.0)
        win_rate = float(row["win_rate"] or 0.0)
        avg_r = float(row["average_r"] or 0.0)
        payoff = float(row["payoff_ratio"] or 0.0)
        avg_hold = float(row["avg_holding_days"] or 0.0)
        print(
            f"{row['version']:<18} "
            f"{int(row['fills'] or 0):>7} "
            f"{cagr:>8.1f}% "
            f"{maxdd:>8.1f}% "
            f"{calmar:>8.2f} "
            f"{win_rate * 100.0:>7.1f}% "
            f"{avg_r:>8.3f}R "
            f"{payoff:>8.2f} "
            f"{avg_hold:>8.1f}d "
            f"{int(row['signals'] or 0):>9}"
        )


def print_component_table(rows: list[dict[str, Any]]) -> None:
    print("\n=== COMPONENT ISOLATION (at 0.2% cost) ===")
    print(f"{'Variant':<18} {'Fills':>7} {'CAGR':>9} {'MaxDD':>9} {'Calmar':>8} {'Diff_vs_A':>10}")
    base = next((row for row in rows if row["version"] == "version_a"), None)
    base_calmar = float(base["calmar"] or 0.0) if base else 0.0
    for row in rows:
        calmar = row["calmar"]
        diff = "baseline" if row["version"] == "version_a" else f"{(float(calmar or 0.0) - base_calmar):+.2f}"
        print(
            f"{row['version']:<18} "
            f"{int(row['fills'] or 0):>7} "
            f"{float(row['cagr_pct'] or 0.0):>8.1f}% "
            f"{float(row['max_drawdown_pct'] or 0.0):>8.1f}% "
            f"{float(calmar or 0.0):>8.2f} "
            f"{diff:>10}"
        )


def run_v2_sweep(
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    output_dir: Path,
    round_trip_cost_pct: float = 0.0,
) -> None:
    _ = round_trip_cost_pct
    output_dir.mkdir(parents=True, exist_ok=True)
    costs = (0.0, 0.002, 0.005)
    version_specs: list[dict[str, Any]] = [
        {
            "label": "version_a",
            "entry_indicator": "crsi",
            "entry_threshold": 10.0,
            "min_days_down": 4,
            "days_down_lookback": 5,
            "exit_mode": "sma5",
            "stop_atr_mult": 3.0,
            "risk_per_trade_pct": 0.05,
            "max_signals_per_day": 3,
            "cooldown_days": 5,
        },
        {
            "label": "version_b",
            "entry_indicator": "cum_rsi",
            "entry_threshold": 5.0,
            "min_days_down": 4,
            "days_down_lookback": 5,
            "max_vol_rank": 20.0,
            "vol_rank_period": 20,
            "exit_mode": "sma5",
            "stop_atr_mult": 3.0,
            "risk_per_trade_pct": 0.05,
            "max_signals_per_day": 2,
            "cooldown_days": 5,
        },
        {
            "label": "version_c",
            "entry_indicator": "crsi",
            "entry_threshold": 10.0,
            "max_ibs": 0.2,
            "max_vol_rank": 20.0,
            "min_days_down": 4,
            "days_down_lookback": 5,
            "exit_mode": "combined_sma5_10d",
            "stop_atr_mult": 3.0,
            "risk_per_trade_pct": 0.05,
            "max_signals_per_day": 2,
            "cooldown_days": 5,
        },
        {
            "label": "version_d",
            "entry_indicator": "crsi",
            "entry_threshold": 10.0,
            "min_days_down": 4,
            "days_down_lookback": 5,
            "max_adx": 20.0,
            "exit_mode": "sma5",
            "stop_atr_mult": 3.0,
            "risk_per_trade_pct": 0.05,
            "max_signals_per_day": 3,
            "cooldown_days": 5,
        },
        {
            "label": "version_e",
            "entry_indicator": "crsi",
            "entry_threshold": 10.0,
            "min_days_down": 4,
            "days_down_lookback": 5,
            "exit_mode": "sma5",
            "stop_atr_mult": 3.0,
            "risk_per_trade_pct": 0.05,
            "max_signals_per_day": 3,
            "cooldown_days": 5,
            "sector_weights": {
                "Financial Services": 1.2,
                "Capital Goods": 1.0,
                "Information Technology": 0.5,
            },
        },
    ]

    all_results: dict[tuple[str, float], dict[str, Any]] = {}
    best_cost_rows: list[dict[str, Any]] = []

    for cost in costs:
        rows: list[dict[str, Any]] = []
        baseline = run_variant(
            config=config,
            ohlcv=ohlcv,
            universe=universe,
            sector_map=sector_map,
            start_date=start_date,
            end_date=end_date,
            rsi_threshold=25.0,
            exit_mode="sma3",
            time_stop_days=int(config["exit"]["time_stop_days"]),
            stop_atr_mult=3.0,
            risk_per_trade_pct=0.05,
            round_trip_cost_pct=cost,
        )
        all_results[("v1_base", cost)] = baseline
        rows.append(comparison_row(baseline, "v1_base", cost))

        for spec in version_specs:
            label = str(spec["label"])
            kwargs = {key: value for key, value in spec.items() if key != "label"}
            result = run_variant_v2(
                config=config,
                ohlcv=ohlcv,
                universe=universe,
                sector_map=sector_map,
                start_date=start_date,
                end_date=end_date,
                label=label,
                round_trip_cost_pct=cost,
                **kwargs,
            )
            all_results[(label, cost)] = result
            rows.append(comparison_row(result, label, cost))

        print_comparison_table(f"COST: {cost * 100:.1f}%", rows)
        pl.DataFrame(rows).write_csv(output_dir / f"comparison_{cost_label(cost)}.csv")
        if cost == 0.002:
            best_cost_rows = rows

    version_a = next(spec for spec in version_specs if spec["label"] == "version_a")
    isolation_specs = [
        version_a,
        {**version_a, "label": "A_no_cooldown", "cooldown_days": 0},
        {**version_a, "label": "A_no_ranking", "max_signals_per_day": None},
        {**version_a, "label": "A_no_days_down", "min_days_down": None},
        {**version_a, "label": "A_strict_crsi", "entry_threshold": 5.0},
        {**version_a, "label": "A_exit_sma3", "exit_mode": "sma3"},
        {**version_a, "label": "A_exit_rsi50", "exit_mode": "rsi_50"},
        {**version_a, "label": "A_exit_cum_rsi", "exit_mode": "cum_rsi_130"},
        {**version_a, "label": "A_tighter_stop", "stop_atr_mult": 1.5},
    ]
    isolation_rows: list[dict[str, Any]] = []
    for spec in isolation_specs:
        label = str(spec["label"])
        kwargs = {key: value for key, value in spec.items() if key != "label"}
        result = run_variant_v2(
            config=config,
            ohlcv=ohlcv,
            universe=universe,
            sector_map=sector_map,
            start_date=start_date,
            end_date=end_date,
            label=label,
            round_trip_cost_pct=0.002,
            **kwargs,
        )
        row = comparison_row(result, label, 0.002)
        isolation_rows.append(row)
        all_results[(label, 0.002)] = result
    pl.DataFrame(isolation_rows).write_csv(output_dir / "component_isolation.csv")
    print_component_table(isolation_rows)

    comparable_rows = [row for row in best_cost_rows if row["version"] != "v1_base"]
    best_row = best_by_calmar(comparable_rows)
    best_label = str(best_row["version"])
    best_result = all_results[(best_label, 0.002)]
    write_outputs(output_dir / "best_version", best_result)
    print(f"\nBest 0.2% cost Calmar version: {best_label}")
    print_summary(best_result["metrics"])


def direction_metrics(trades: pl.DataFrame, direction: str) -> dict[str, Any]:
    subset = trades.filter(pl.col("direction") == direction) if not trades.is_empty() and "direction" in trades.columns else pl.DataFrame()
    if subset.is_empty():
        return {"fills": 0, "win_rate": 0.0, "average_r": None, "avg_holding_days": None, "pnl": 0.0}
    r_values = [float(value) for value in subset["r_multiple"].to_list()]
    wins = [value for value in subset["pnl"].to_list() if float(value) > 0.0]
    holding_days = [int(value) for value in subset["holding_days"].to_list()]
    return {
        "fills": subset.height,
        "win_rate": len(wins) / subset.height if subset.height else 0.0,
        "average_r": sum(r_values) / len(r_values) if r_values else None,
        "avg_holding_days": sum(holding_days) / len(holding_days) if holding_days else None,
        "pnl": sum(float(value) for value in subset["pnl"].to_list()),
    }


def run_short_side_study(
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    output_dir: Path,
    round_trip_cost_pct: float = 0.002,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    locked = {
        "rsi_threshold": 35.0,
        "short_rsi_threshold": 65.0,
        "stop_atr_mult": 3.5,
        "exit_mode": "sma3",
        "risk_per_trade_pct": 0.04,
        "max_positions": 5,
        "max_sector_positions": 3,
        "initial_capital": 500_000.0,
    }

    long_signals = scan_mean_reversion(
        ohlcv,
        rsi_period=2,
        rsi_threshold=locked["rsi_threshold"],
        sma_trend_period=int(config["entry"]["sma_trend_period"]),
        min_adv_inr=float(config["entry"]["min_adv_inr"]),
        min_price=float(config["entry"]["min_price_inr"]),
        stop_atr_mult=locked["stop_atr_mult"],
    )
    membership = historical_liquidity_membership(
        ohlcv, size=100, lookback_sessions=50, min_history_sessions=200, reconstitute="semiannual"
    )
    long_signals = filter_signals_to_historical_membership(long_signals, membership).filter(
        (pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= end_date)
    )
    short_signals = scan_short_mean_reversion(
        ohlcv,
        rsi_period=2,
        rsi_threshold=locked["short_rsi_threshold"],
        sma_trend_period=int(config["entry"]["sma_trend_period"]),
        min_adv_inr=float(config["entry"]["min_adv_inr"]),
        min_price=float(config["entry"]["min_price_inr"]),
        stop_atr_mult=locked["stop_atr_mult"],
    )
    short_signals = filter_signals_to_historical_membership(short_signals, membership).filter(
        (pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= end_date)
    )

    exit_config = MRExitConfig(
        exit_mode=locked["exit_mode"],
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=locked["stop_atr_mult"],
        max_hold_days=int(config["exit"].get("max_hold_days", 20)),
    )
    long_only = simulate_mr_portfolio(
        long_signals,
        ohlcv.filter(pl.col("date") <= end_date),
        start_date=start_date,
        end_date=end_date,
        max_positions=locked["max_positions"],
        max_sector_positions=locked["max_sector_positions"],
        initial_capital=locked["initial_capital"],
        risk_per_trade_pct=locked["risk_per_trade_pct"],
        exit_config=exit_config,
        sector_map=sector_map,
        round_trip_cost_pct=round_trip_cost_pct,
    )
    long_only_metrics = compute_metrics(long_only["daily_equity"], long_only["trades"])
    long_only_metrics.update(
        {
            "variant": "long_only",
            "version": "long_only",
            "signals": long_signals.height,
            "filled_signals": long_only["filled_signals"],
            "round_trip_cost_pct": round_trip_cost_pct,
            **locked,
        }
    )

    combined = simulate_mr_long_short_portfolio(
        long_signals,
        short_signals,
        ohlcv.filter(pl.col("date") <= end_date),
        start_date=start_date,
        end_date=end_date,
        max_positions=locked["max_positions"],
        max_sector_positions=locked["max_sector_positions"],
        initial_capital=locked["initial_capital"],
        risk_per_trade_pct=locked["risk_per_trade_pct"],
        exit_config=exit_config,
        sector_map=sector_map,
        round_trip_cost_pct=round_trip_cost_pct,
    )
    combined_metrics = compute_metrics(combined["daily_equity"], combined["trades"])
    combined_metrics.update(
        {
            "variant": "long_short_shared_book",
            "long_signals": combined["long_signals"],
            "short_signals": combined["short_signals"],
            "filled_long_signals": combined["filled_long_signals"],
            "filled_short_signals": combined["filled_short_signals"],
            "round_trip_cost_pct": round_trip_cost_pct,
            "long_leg": direction_metrics(combined["trades"], "long"),
            "short_leg": direction_metrics(combined["trades"], "short"),
            **locked,
        }
    )

    comparison_rows = [
        {
            "variant": "long_only",
            "version": "long_only",
            "signals": long_signals.height,
            "fills": long_only_metrics.get("fills", 0),
            "cagr_pct": long_only_metrics.get("cagr_pct"),
            "max_drawdown_pct": long_only_metrics.get("max_drawdown_pct"),
            "calmar": long_only_metrics.get("calmar"),
            "sharpe": long_only_metrics.get("sharpe"),
            "win_rate": long_only_metrics.get("win_rate"),
            "average_r": long_only_metrics.get("average_r"),
            "payoff_ratio": long_only_metrics.get("payoff_ratio"),
            "avg_holding_days": long_only_metrics.get("avg_holding_days"),
        },
        {
            "variant": "long_short_shared_book",
            "version": "long_short",
            "signals": long_signals.height + short_signals.height,
            "fills": combined_metrics.get("fills", 0),
            "cagr_pct": combined_metrics.get("cagr_pct"),
            "max_drawdown_pct": combined_metrics.get("max_drawdown_pct"),
            "calmar": combined_metrics.get("calmar"),
            "sharpe": combined_metrics.get("sharpe"),
            "win_rate": combined_metrics.get("win_rate"),
            "average_r": combined_metrics.get("average_r"),
            "payoff_ratio": combined_metrics.get("payoff_ratio"),
            "avg_holding_days": combined_metrics.get("avg_holding_days"),
        },
    ]
    pl.DataFrame(comparison_rows).write_csv(output_dir / "short_side_comparison.csv")
    long_signals.write_csv(output_dir / "long_signals.csv")
    short_signals.write_csv(output_dir / "short_signals.csv")
    write_outputs(output_dir / "long_only", {"signals": long_signals, **long_only, "metrics": long_only_metrics})
    write_outputs(output_dir / "long_short_shared_book", {"signals": pl.concat([long_signals, short_signals], how="diagonal"), **combined, "metrics": combined_metrics})

    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "long_only": long_only_metrics,
                "long_short_shared_book": combined_metrics,
                "practical_note": "NSE overnight equity shorts are not available to retail cash equity accounts; this is theoretical unless implemented with stock futures or another borrowable instrument.",
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\n=== SHORT SIDE STUDY ===")
    print(f"Long signals:  {long_signals.height}")
    print(f"Short signals: {short_signals.height}")
    print_comparison_table("LONG ONLY VS LONG+SHORT", comparison_rows)
    print("\nLong+short leg breakdown:")
    print(json.dumps({"long_leg": combined_metrics["long_leg"], "short_leg": combined_metrics["short_leg"]}, indent=2, default=str))
    print("\nPractical note: NSE overnight equity shorts need futures/borrowable instruments; this is not directly executable in a retail cash equity book.")


def run_regime_study(
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    universe: set[str],
    sector_map: dict[str, str],
    start_date: date,
    end_date: date,
    output_dir: Path,
    round_trip_cost_pct: float = 0.0,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    common = dict(
        config=config,
        ohlcv=ohlcv,
        universe=universe,
        sector_map=sector_map,
        start_date=start_date,
        end_date=end_date,
        rsi_threshold=float(config["entry"]["rsi_threshold"]),
        exit_mode=str(config["exit"]["mode"]),
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=float(config["entry"]["stop_atr_mult"]),
        risk_per_trade_pct=float(config["portfolio"]["risk_per_trade_pct"]),
        round_trip_cost_pct=round_trip_cost_pct,
    )

    for regime in ("all", "bull", "bear"):
        print(f"\n=== REGIME: {regime.upper()} ===")
        result = run_variant(**common, regime=regime)
        write_outputs(output_dir / regime, result)
        print_summary(result["metrics"])

    rows = []
    for regime in ("all", "bull", "bear"):
        summary_path = output_dir / regime / "summary.json"
        with summary_path.open(encoding="utf-8") as fh:
            m = json.load(fh)
        rows.append(
            {
                "regime": regime,
                "fills": m.get("fills"),
                "cagr_pct": round(m.get("cagr_pct") or 0, 2),
                "max_drawdown_pct": round(m.get("max_drawdown_pct") or 0, 2),
                "calmar": round(m.get("calmar") or 0, 3),
                "sharpe": round(m.get("sharpe") or 0, 3),
                "win_rate": round((m.get("win_rate") or 0) * 100, 1),
                "avg_holding_days": round(m.get("avg_holding_days") or 0, 1),
                "profit_factor": round(m.get("profit_factor") or 0, 3),
            }
        )
    frame = pl.DataFrame(rows)
    frame.write_csv(output_dir / "regime_comparison.csv")
    print("\n=== REGIME COMPARISON ===")
    print(frame)
    print(f"\nWrote outputs to {output_dir}")


def main() -> None:
    args = parse_args()
    start_date = parse_date(args.start)
    end_date = parse_date(args.end)
    config = load_mr_config(Path(args.config))
    universe: set[str] | None = None
    sector_map = load_sector_map(Path(config["data"]["sector_map_path"]))
    ohlcv = load_ohlcv(config, start_date, end_date)
    print(f"Loaded {ohlcv.height} OHLCV rows; historical selection uses PIT liquidity membership.")

    output_root = Path(args.output_dir)
    if args.sweep:
        run_sweep(config, ohlcv, universe, sector_map, start_date, end_date, output_root / "sweep", round_trip_cost_pct=args.cost)
        return
    if args.sweep_v2:
        run_v2_sweep(config, ohlcv, universe, sector_map, start_date, end_date, output_root / "sweep_v2", round_trip_cost_pct=args.cost)
        return
    if args.short_side:
        run_short_side_study(config, ohlcv, universe, sector_map, start_date, end_date, output_root / "short_side", round_trip_cost_pct=args.cost)
        return
    if args.regime_study:
        run_regime_study(config, ohlcv, universe, sector_map, start_date, end_date, output_root / "regime_study", round_trip_cost_pct=args.cost)
        return

    result = run_variant(
        config=config,
        ohlcv=ohlcv,
        universe=universe,
        sector_map=sector_map,
        start_date=start_date,
        end_date=end_date,
        rsi_threshold=float(config["entry"]["rsi_threshold"]),
        exit_mode=str(config["exit"]["mode"]),
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=float(config["entry"]["stop_atr_mult"]),
        risk_per_trade_pct=float(config["portfolio"]["risk_per_trade_pct"]),
        round_trip_cost_pct=args.cost,
    )
    output_dir = output_root / f"{start_date.isoformat()}_{end_date.isoformat()}"
    write_outputs(output_dir, result)
    print_summary(result["metrics"])
    print(f"\nWrote outputs to {output_dir}")


if __name__ == "__main__":
    main()

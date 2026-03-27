from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import requests

from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals
from sepa_scanner.settings import load_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

try:
    import multibagger_core
except ImportError:  # pragma: no cover - exercised only when extension is missing
    multibagger_core = None


OFFICIAL_TRI_URL = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
OFFICIAL_CONSTITUENTS_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"


@dataclass(slots=True)
class PendingSignal:
    symbol: str
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    rs_rank: float | None
    expiration_date: date


@dataclass(slots=True)
class OpenPosition:
    symbol: str
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    entry_date: date
    entry_price: float
    shares: float
    active_stop: float
    initial_risk: float
    target_price: float | None
    mae_price: float
    mfe_price: float
    breakeven_activated: bool
    trail_activated: bool
    partial_taken: bool
    partial_exit_price: float | None
    partial_exit_date: date | None
    partial_exit_r: float | None
    realized_pnl: float
    original_shares: float
    cost_basis: float
    dead_money_deadline_days: int | None
    dead_money_extended: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark and regime diagnostics for the current Rust portfolio path.")
    parser.add_argument("--start", default="2010-01-04")
    parser.add_argument("--end", default="2026-03-17")
    parser.add_argument("--output-dir", default="reports/portfolio_diagnostics")
    parser.add_argument("--reference-dir", default="data/reference")
    return parser.parse_args()


def format_nifty_date(value: date) -> str:
    return value.strftime("%d-%b-%y")


def _tri_payload(start_date: date, end_date: date) -> dict[str, str]:
    cinfo = (
        "{'name':'equity',"
        f"'startDate':'{format_nifty_date(start_date)}',"
        f"'endDate':'{format_nifty_date(end_date)}',"
        "'indexName':'Nifty 500'}"
    )
    return {"cinfo": cinfo}


def ensure_reference_data(reference_dir: Path, start_date: date, end_date: date) -> tuple[Path, Path]:
    reference_dir.mkdir(parents=True, exist_ok=True)
    tri_csv_path = reference_dir / f"nifty500_tri_{start_date.isoformat()}_{end_date.isoformat()}.csv"
    tri_raw_path = reference_dir / f"nifty500_tri_raw_{start_date.isoformat()}_{end_date.isoformat()}.json"
    constituent_csv_path = reference_dir / "nifty500_constituents.csv"

    if not constituent_csv_path.exists():
        tmp_csv = Path("/tmp/ind_nifty500list.csv")
        if tmp_csv.exists():
            constituent_csv_path.write_bytes(tmp_csv.read_bytes())
        else:
            response = requests.get(OFFICIAL_CONSTITUENTS_URL, timeout=30)
            response.raise_for_status()
            constituent_csv_path.write_bytes(response.content)

    if not tri_csv_path.exists():
        if tri_raw_path.exists():
            rows = parse_tri_payload_json(tri_raw_path)
        else:
            tmp_payload = Path("/tmp/nifty_totalreturn_payload.json")
            if tmp_payload.exists():
                try:
                    rows = parse_tri_payload_json(tmp_payload)
                except (KeyError, json.JSONDecodeError):
                    response = requests.post(OFFICIAL_TRI_URL, json=_tri_payload(start_date, end_date), timeout=60)
                    response.raise_for_status()
                    tri_raw_path.write_text(response.text, encoding="utf-8")
                    rows = parse_tri_payload_text(response.text)
            else:
                response = requests.post(OFFICIAL_TRI_URL, json=_tri_payload(start_date, end_date), timeout=60)
                response.raise_for_status()
                tri_raw_path.write_text(response.text, encoding="utf-8")
                rows = parse_tri_payload_text(response.text)
        pl.DataFrame(rows).sort("date").write_csv(tri_csv_path)

    return tri_csv_path, constituent_csv_path


def parse_tri_payload_json(path: Path) -> list[dict[str, Any]]:
    return parse_tri_payload_text(path.read_text(encoding="utf-8"))


def parse_tri_payload_text(text: str) -> list[dict[str, Any]]:
    outer = json.loads(text)
    if "d" not in outer:
        raise KeyError("Official TRI payload missing 'd' field")
    raw = outer["d"]
    entries = json.loads(raw)
    rows: list[dict[str, Any]] = []
    for row in entries:
        tri_raw = row.get("TotalReturnsIndex")
        if tri_raw in (None, "", "-"):
            continue
        rows.append(
            {
                "date": datetime.strptime(row["Date"], "%d %b %Y").date(),
                "tri": float(str(tri_raw).replace(",", "")),
            }
        )
    return rows


def load_sector_map(path: Path) -> dict[str, str]:
    df = pl.read_csv(path).rename({"Industry": "sector", "Symbol": "symbol"})
    return {
        str(row["symbol"]): str(row["sector"])
        for row in df.select("symbol", "sector").to_dicts()
    }


def resolve_exit_config(settings: Any) -> dict[str, Any]:
    return {
        "breakeven_activation_r": settings.exit_strategy.breakeven_activation_r,
        "partial_profit_r": settings.exit_strategy.partial_profit_r,
        "partial_profit_pct": settings.exit_strategy.partial_profit_pct,
        "trail_activation_r": settings.exit_strategy.trail_activation_r,
        "trail_pct": settings.exit_strategy.trail_pct,
        "trail_pct_bull": settings.exit_strategy.trail_pct_bull,
        "trail_pct_bear": settings.exit_strategy.trail_pct_bear,
        "dead_money_days": settings.exit_strategy.dead_money_days,
        "dead_money_extend_days": settings.exit_strategy.dead_money_extend_days,
        "dead_money_min_open_r": settings.exit_strategy.dead_money_min_open_r,
        "dead_money_min_mfe_r": settings.exit_strategy.dead_money_min_mfe_r,
        "time_stop_days": settings.exit_strategy.time_stop_days,
        "max_hold_before_tighten": settings.exit_strategy.max_hold_before_tighten,
    }


def load_inputs(start_date: date, end_date: date) -> tuple[dict[str, Any], pl.DataFrame, pl.DataFrame]:
    settings = load_settings()
    parquet_store = ParquetOhlcvStore(settings.runtime.data_dir / "yf_adjusted")
    conn = duckdb.connect(str(settings.runtime.data_dir / "db" / "sepa.duckdb"), read_only=True)

    signals = get_backtest_signals(conn, start_date=start_date, end_date=end_date, symbol=None)
    symbols = signals["symbol"].unique().to_list()
    ohlcv_end_date = end_date + timedelta(days=(settings.backtest.pending_days + settings.backtest.max_hold_days) * 2)
    ohlcv = get_backtest_ohlcv(parquet_store, symbols=symbols, start_date=start_date, end_date=ohlcv_end_date)

    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=settings.backtest.max_hold_days,
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(settings.backtest.exit_mode),
        trail_activation_r=settings.exit_strategy.trail_activation_r,
        trail_pct=settings.exit_strategy.trail_pct,
        dead_money_days=settings.exit_strategy.dead_money_days,
        dead_money_extend_days=settings.exit_strategy.dead_money_extend_days,
        dead_money_min_open_r=settings.exit_strategy.dead_money_min_open_r,
        dead_money_min_mfe_r=settings.exit_strategy.dead_money_min_mfe_r,
    )

    portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        "max_positions": settings.portfolio.max_positions,
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": settings.portfolio.max_heat_pct,
        "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
        "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
        "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": settings.portfolio.regime_high_volatility_threshold,
        "regime_cooldown_days": settings.portfolio.regime_cooldown_days,
        "breadth_soft_floor_pct": settings.portfolio.breadth_soft_floor_pct,
        "breadth_soft_size_mult": settings.portfolio.breadth_soft_size_mult,
    }

    return {
        "settings": settings,
        "params": params,
        "portfolio": portfolio,
        "exit_config": resolve_exit_config(settings),
        "parquet_store": parquet_store,
    }, signals, ohlcv


def build_symbol_data(ohlcv: pl.DataFrame) -> tuple[dict[str, dict[str, Any]], list[date]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    unique_dates = sorted(set(ohlcv["date"].to_list()))

    for group in ohlcv.partition_by("symbol", maintain_order=True):
        symbol = str(group["symbol"][0])
        dates = group["date"].to_list()
        opens = group["open"].to_list()
        highs = group["high"].to_list()
        lows = group["low"].to_list()
        closes = group["close"].to_list()
        date_to_idx = {d: idx for idx, d in enumerate(dates)}
        by_symbol[symbol] = {
            "dates": dates,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "date_to_idx": date_to_idx,
        }
    return by_symbol, unique_dates


def rust_portfolio_result(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    params: BacktestParams,
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    if multibagger_core is None:
        raise RuntimeError("multibagger_core is not installed")

    ohlcv_sorted = ohlcv.sort(["symbol", "date"])
    all_dates = [d.strftime("%Y-%m-%d") for d in ohlcv_sorted["date"]]
    opens = ohlcv_sorted["open"].to_numpy().astype(np.float64)
    highs = ohlcv_sorted["high"].to_numpy().astype(np.float64)
    lows = ohlcv_sorted["low"].to_numpy().astype(np.float64)
    closes = ohlcv_sorted["close"].to_numpy().astype(np.float64)

    symbol_indices: dict[str, tuple[int, int]] = {}
    current_idx = 0
    counts = ohlcv_sorted.group_by("symbol", maintain_order=True).len()
    for sym, count in counts.iter_rows():
        symbol_indices[str(sym)] = (current_idx, current_idx + int(count))
        current_idx += int(count)

    rust_signals = []
    for sig in signals.sort(["signal_date", "symbol"]).to_dicts():
        rust_signals.append(
            {
                "symbol": str(sig["symbol"]),
                "sector": "UNKNOWN",
                "signal_date": sig["signal_date"].strftime("%Y-%m-%d"),
                "entry_ref": float(sig["entry_ref"]),
                "stop": float(sig["stop"]),
                "pivot": float(sig["pivot"]) if sig.get("pivot") is not None else None,
                "rs_rank": float(sig.get("rs_rank", 0.0)),
            }
        )

    params_json = json.dumps(
        {
            "pending_days": params.pending_days,
            "max_hold_days": params.max_hold_days,
            "target_r": params.target_r,
            "exit_mode": params.exit_mode.value,
            "trail_activation_r": params.trail_activation_r,
            "trail_pct": params.trail_pct,
            "dead_money_days": params.dead_money_days,
            "dead_money_extend_days": params.dead_money_extend_days,
            "dead_money_min_open_r": params.dead_money_min_open_r,
            "dead_money_min_mfe_r": params.dead_money_min_mfe_r,
        }
    )

    results_json = multibagger_core.run_portfolio_backtest_rust(
        json.dumps(rust_signals),
        params_json,
        json.dumps(portfolio),
        all_dates,
        opens,
        highs,
        lows,
        closes,
        symbol_indices,
    )
    return json.loads(results_json)


def simulate_portfolio_python(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    params: BacktestParams,
    portfolio: dict[str, Any],
    analysis_sector_map: dict[str, str],
    *,
    exit_config: dict[str, Any] | None = None,
    entry_gate: dict[date, bool] | None = None,
    entry_size_mult: dict[date, float] | None = None,
) -> dict[str, Any]:
    symbols = build_signal_rows(signals)
    symbol_data, unique_dates = build_symbol_data(ohlcv)
    exit_cfg = {
        "breakeven_activation_r": 1.0,
        "partial_profit_r": None,
        "partial_profit_pct": 0.0,
        "trail_pct_bull": params.trail_pct,
        "trail_pct_bear": params.trail_pct,
        "time_stop_days": None,
        "max_hold_before_tighten": params.max_hold_days,
    }
    if exit_config:
        for key, value in exit_config.items():
            if value is not None:
                exit_cfg[key] = value
    entry_gate = entry_gate or {}
    entry_size_mult = entry_size_mult or {}

    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for signal in symbols:
        signals_by_date[signal["signal_date"]].append(signal)

    cash = float(portfolio["initial_capital"])
    equity_peak = float(portfolio["initial_capital"])
    open_positions: list[OpenPosition] = []
    pending_signals: list[PendingSignal] = []
    closed_trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous_equity: float | None = None

    for today in unique_dates:
        i = 0
        while i < len(open_positions):
            pos = open_positions[i]
            sym_prices = symbol_data.get(pos.symbol)
            if sym_prices is None:
                i += 1
                continue

            idx = sym_prices["date_to_idx"].get(today)
            if idx is not None:
                open_price = sym_prices["opens"][idx]
                high_price = sym_prices["highs"][idx]
                low_price = sym_prices["lows"][idx]
                close_price = sym_prices["closes"][idx]
                holding_days = (today - pos.entry_date).days
                market_hostile = not bool(entry_gate.get(today, True))

                pos.mae_price = min(pos.mae_price, low_price)
                pos.mfe_price = max(pos.mfe_price, high_price)

                exit_price: float | None = None
                status = ""

                if open_price <= pos.active_stop:
                    exit_price = open_price
                    status = "stopped"
                elif pos.target_price is not None:
                    if open_price >= pos.target_price:
                        exit_price = open_price
                        status = "target"
                    elif high_price >= pos.target_price and low_price <= pos.active_stop:
                        exit_price = pos.active_stop
                        status = "stopped"
                    elif low_price <= pos.active_stop:
                        exit_price = pos.active_stop
                        status = "stopped"
                    elif high_price >= pos.target_price:
                        exit_price = pos.target_price
                        status = "target"
                elif low_price <= pos.active_stop:
                    exit_price = pos.active_stop
                    status = "stopped"

                risk = pos.initial_risk
                reached_1r = risk > 0.0 and high_price >= (pos.entry_price + risk)
                if exit_price is None and risk > 0.0:
                    breakeven_activation_r = float(exit_cfg["breakeven_activation_r"])
                    partial_profit_r = exit_cfg["partial_profit_r"]
                    partial_profit_pct = float(exit_cfg["partial_profit_pct"])
                    max_hold_before_tighten = int(exit_cfg["max_hold_before_tighten"])
                    if not pos.breakeven_activated and high_price >= (pos.entry_price + (risk * breakeven_activation_r)):
                        pos.breakeven_activated = True
                        pos.active_stop = max(pos.active_stop, pos.entry_price)

                    if (
                        partial_profit_r is not None
                        and partial_profit_pct > 0.0
                        and not pos.partial_taken
                        and pos.shares > 0.0
                        and high_price >= (pos.entry_price + (risk * float(partial_profit_r)))
                    ):
                        partial_trigger_price = pos.entry_price + (risk * float(partial_profit_r))
                        partial_fill_price = max(open_price, partial_trigger_price) if open_price >= partial_trigger_price else partial_trigger_price
                        sell_shares = min(pos.shares, pos.shares * partial_profit_pct)
                        if sell_shares > 0.0:
                            cash += sell_shares * partial_fill_price
                            pos.realized_pnl += sell_shares * (partial_fill_price - pos.entry_price)
                            pos.shares -= sell_shares
                            pos.partial_taken = True
                            pos.partial_exit_price = partial_fill_price
                            pos.partial_exit_date = today
                            pos.partial_exit_r = (partial_fill_price - pos.entry_price) / risk
                            pos.breakeven_activated = True
                            pos.trail_activated = True
                            pos.active_stop = max(pos.active_stop, pos.entry_price)

                    if not pos.trail_activated and high_price >= (pos.entry_price + (risk * params.trail_activation_r)):
                        pos.trail_activated = True

                    if pos.trail_activated:
                        trail_pct = float(exit_cfg["trail_pct_bear"] if market_hostile else exit_cfg["trail_pct_bull"])
                        if holding_days >= max_hold_before_tighten:
                            trail_pct = min(trail_pct, max(0.05, trail_pct * 0.6))
                        if params.exit_mode == ExitMode.ADVANCED_SEPA:
                            if high_price >= (pos.entry_price + (risk * 3.0)):
                                lookback = 10
                                if idx >= lookback:
                                    min_low = min(sym_prices["lows"][idx - lookback:idx])
                                    pos.active_stop = max(pos.active_stop, min_low)
                                pos.active_stop = max(pos.active_stop, pos.entry_price + (risk * 2.0))
                        trail_price = pos.mfe_price * (1.0 - trail_pct)
                        pos.active_stop = max(pos.active_stop, trail_price)

                if reached_1r:
                    pos.breakeven_activated = True
                if exit_price is None and exit_cfg["time_stop_days"] is not None and not pos.breakeven_activated and holding_days >= int(exit_cfg["time_stop_days"]):
                    exit_price = close_price
                    status = "time_stop"
                if (
                    exit_price is None
                    and pos.dead_money_deadline_days is not None
                    and not pos.breakeven_activated
                    and not reached_1r
                    and holding_days >= pos.dead_money_deadline_days
                ):
                    open_r = ((close_price - pos.entry_price) / risk) if risk > 0.0 else None
                    mfe_r = ((pos.mfe_price - pos.entry_price) / risk) if risk > 0.0 else None
                    can_extend_dead_money = (
                        not pos.dead_money_extended
                        and params.dead_money_extend_days > 0
                        and open_r is not None
                        and mfe_r is not None
                        and open_r >= params.dead_money_min_open_r
                        and mfe_r >= params.dead_money_min_mfe_r
                    )
                    if can_extend_dead_money:
                        pos.dead_money_deadline_days += params.dead_money_extend_days
                        pos.dead_money_extended = True
                    else:
                        exit_price = close_price
                        status = "dead_money"
                if exit_price is None and holding_days >= params.max_hold_days:
                    exit_price = close_price
                    status = "time_stop"

                if exit_price is not None:
                    cash += pos.shares * exit_price
                    closed_trades.append(
                        close_trade_dict(
                            symbol=pos.symbol,
                            signal_date=pos.signal_date,
                            status=status,
                            entry_ref=pos.entry_ref,
                            stop=pos.stop,
                            pivot=pos.pivot,
                            entry_date=pos.entry_date,
                            entry_price=pos.entry_price,
                            exit_date=today,
                            exit_price=exit_price,
                            holding_days=holding_days,
                            mae_price=pos.mae_price,
                            mfe_price=pos.mfe_price,
                            shares=pos.shares,
                            sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                            original_shares=pos.original_shares,
                            realized_pnl=pos.realized_pnl,
                        )
                    )
                    open_positions.pop(i)
                    continue
            i += 1

        invested_value = 0.0
        marked_sector_values: dict[str, float] = defaultdict(float)
        open_sector_costs: dict[str, float] = defaultdict(float)
        for pos in open_positions:
            sym_prices = symbol_data[pos.symbol]
            idx = sym_prices["date_to_idx"].get(today)
            marked_value = pos.shares * (sym_prices["closes"][idx] if idx is not None else pos.entry_price)
            invested_value += marked_value
            sector = analysis_sector_map.get(pos.symbol, "UNKNOWN")
            marked_sector_values[sector] += marked_value
            open_sector_costs[sector] += pos.cost_basis

        current_equity = cash + invested_value
        equity_peak = max(equity_peak, current_equity)

        if today in signals_by_date:
            for sig in signals_by_date[today]:
                pending_signals.append(
                    PendingSignal(
                        symbol=sig["symbol"],
                        signal_date=sig["signal_date"],
                        entry_ref=sig["entry_ref"],
                        stop=sig["stop"],
                        pivot=sig["pivot"],
                        rs_rank=sig["rs_rank"],
                        expiration_date=today + timedelta(days=params.pending_days),
                    )
                )

        dd_pct = ((equity_peak - current_equity) / equity_peak) if equity_peak > 0.0 else 0.0
        if dd_pct >= portfolio["drawdown_close_pct"]:
            size_mult = 0.0
        elif dd_pct >= portfolio["drawdown_stop_pct"]:
            size_mult = 0.0
        elif dd_pct >= portfolio["drawdown_half_pct"]:
            size_mult = 0.5
        else:
            size_mult = 1.0
        day_entries_allowed = bool(entry_gate.get(today, True)) if entry_gate else True
        day_entry_size_mult = float(entry_size_mult.get(today, 1.0)) if entry_size_mult else 1.0

        j = 0
        while j < len(pending_signals):
            pending = pending_signals[j]
            if pending.expiration_date < today:
                closed_trades.append(
                    empty_trade_dict(
                        symbol=pending.symbol,
                        signal_date=pending.signal_date,
                        status="not_filled",
                        entry_ref=pending.entry_ref,
                        stop=pending.stop,
                    )
                )
                pending_signals.pop(j)
                continue

            if not day_entries_allowed:
                j += 1
                continue

            if size_mult == 0.0 or len(open_positions) >= portfolio["max_positions"] or current_equity <= 0.0:
                closed_trades.append(
                    empty_trade_dict(
                        symbol=pending.symbol,
                        signal_date=pending.signal_date,
                        status="skipped_portfolio",
                        entry_ref=pending.entry_ref,
                        stop=pending.stop,
                    )
                )
                pending_signals.pop(j)
                continue

            sym_prices = symbol_data.get(pending.symbol)
            if sym_prices is None:
                closed_trades.append(
                    empty_trade_dict(
                        symbol=pending.symbol,
                        signal_date=pending.signal_date,
                        status="missing_data",
                        entry_ref=pending.entry_ref,
                        stop=pending.stop,
                    )
                )
                pending_signals.pop(j)
                continue

            idx = sym_prices["date_to_idx"].get(today)
            filled = False
            if idx is not None:
                open_price = sym_prices["opens"][idx]
                high_price = sym_prices["highs"][idx]

                if open_price >= pending.entry_ref or high_price >= pending.entry_ref:
                    fill_price = max(open_price, pending.entry_ref)
                    risk_budget = current_equity * portfolio["risk_per_trade_pct"] * size_mult * day_entry_size_mult
                    total_heat = sum(pos.initial_risk * pos.shares for pos in open_positions)

                    if ((total_heat + risk_budget) / current_equity) > portfolio["max_heat_pct"]:
                        closed_trades.append(
                            empty_trade_dict(
                                symbol=pending.symbol,
                                signal_date=pending.signal_date,
                                status="skipped_portfolio",
                                entry_ref=pending.entry_ref,
                                stop=pending.stop,
                            )
                        )
                        pending_signals.pop(j)
                        continue

                    risk_per_share = fill_price - pending.stop
                    if risk_per_share > 0.0:
                        shares = risk_budget / risk_per_share
                        cost = shares * fill_price
                        if cost <= cash:
                            cash -= cost
                            target_price = None
                            if params.target_r is not None:
                                target_price = fill_price + (risk_per_share * params.target_r)

                            open_positions.append(
                                OpenPosition(
                                    symbol=pending.symbol,
                                    signal_date=pending.signal_date,
                                    entry_ref=pending.entry_ref,
                                    stop=pending.stop,
                                    pivot=pending.pivot,
                                    entry_date=today,
                                    entry_price=fill_price,
                                    shares=shares,
                                    active_stop=pending.stop,
                                    initial_risk=risk_per_share,
                                    target_price=target_price,
                                    mae_price=fill_price,
                                    mfe_price=fill_price,
                                    breakeven_activated=False,
                                    trail_activated=False,
                                    partial_taken=False,
                                    partial_exit_price=None,
                                    partial_exit_date=None,
                                    partial_exit_r=None,
                                    realized_pnl=0.0,
                                    original_shares=shares,
                                    cost_basis=cost,
                                    dead_money_deadline_days=params.dead_money_days,
                                    dead_money_extended=False,
                                )
                            )
                            filled = True

            if filled:
                pending_signals.pop(j)
            else:
                j += 1

        top_sector_weight = 0.0
        if invested_value > 0.0 and marked_sector_values:
            top_sector_weight = max(marked_sector_values.values()) / invested_value

        daily_pnl = None if previous_equity is None else current_equity - previous_equity
        daily_return = None if previous_equity in (None, 0.0) else (current_equity / previous_equity) - 1.0
        daily_rows.append(
            {
                "date": today,
                "equity": current_equity,
                "cash": cash,
                "invested_value": invested_value,
                "daily_pnl": daily_pnl,
                "daily_return": daily_return,
                "open_positions": len(open_positions),
                "drawdown_pct": (current_equity / equity_peak) - 1.0 if equity_peak > 0.0 else 0.0,
                "size_mult": size_mult,
                "top_sector_weight": top_sector_weight,
            }
        )
        previous_equity = current_equity

    if unique_dates:
        last_date = unique_dates[-1]
        for pos in open_positions:
            sym_prices = symbol_data[pos.symbol]
            idx = sym_prices["date_to_idx"].get(last_date)
            final_price = sym_prices["closes"][idx] if idx is not None else pos.entry_price
            holding_days = (last_date - pos.entry_date).days
            cash += pos.shares * final_price
            closed_trades.append(
                close_trade_dict(
                    symbol=pos.symbol,
                    signal_date=pos.signal_date,
                    status="open",
                    entry_ref=pos.entry_ref,
                    stop=pos.stop,
                    pivot=pos.pivot,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=last_date,
                    exit_price=final_price,
                    holding_days=holding_days,
                    mae_price=pos.mae_price,
                    mfe_price=pos.mfe_price,
                    shares=pos.shares,
                    sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                    original_shares=pos.original_shares,
                    realized_pnl=pos.realized_pnl,
                )
            )

    return {
        "final_equity": cash,
        "daily_equity": pl.DataFrame(daily_rows),
        "trades": pl.DataFrame(closed_trades).sort(["signal_date", "symbol"]),
    }


def build_signal_rows(signals: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in signals.sort(["signal_date", "symbol"]).to_dicts():
        rows.append(
            {
                "symbol": str(row["symbol"]),
                "signal_date": row["signal_date"],
                "entry_ref": float(row["entry_ref"]),
                "stop": float(row["stop"]),
                "pivot": float(row["pivot"]) if row.get("pivot") is not None else None,
                "rs_rank": float(row["rs_rank"]) if row.get("rs_rank") is not None else None,
            }
        )
    return rows


def empty_trade_dict(symbol: str, signal_date: date, status: str, entry_ref: float, stop: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "status": status,
        "entry_ref": entry_ref,
        "stop": stop,
        "pivot": None,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "holding_days": None,
        "return_pct": None,
        "r_multiple": None,
        "mae_price": None,
        "mfe_price": None,
        "shares": None,
        "pnl": 0.0,
        "sector": "UNKNOWN",
    }


def close_trade_dict(
    *,
    symbol: str,
    signal_date: date,
    status: str,
    entry_ref: float,
    stop: float,
    pivot: float | None,
    entry_date: date,
    entry_price: float,
    exit_date: date,
    exit_price: float,
    holding_days: int,
    mae_price: float,
    mfe_price: float,
    shares: float,
    sector: str,
    original_shares: float | None = None,
    realized_pnl: float = 0.0,
) -> dict[str, Any]:
    risk = entry_price - stop
    total_shares = original_shares if original_shares is not None else shares
    pnl = realized_pnl + (shares * (exit_price - entry_price))
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "status": status,
        "entry_ref": entry_ref,
        "stop": stop,
        "pivot": pivot,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "holding_days": holding_days,
        "return_pct": ((pnl / (total_shares * entry_price)) * 100.0) if entry_price and total_shares > 0.0 else None,
        "r_multiple": (pnl / (total_shares * risk)) if risk > 0.0 and total_shares > 0.0 else None,
        "mae_price": mae_price,
        "mfe_price": mfe_price,
        "shares": total_shares,
        "pnl": pnl,
        "sector": sector,
    }


def compute_performance_stats(curve: pl.DataFrame, value_col: str) -> dict[str, float]:
    series = curve.sort("date")
    start_value = float(series[value_col][0])
    end_value = float(series[value_col][-1])
    start_date = series["date"][0]
    end_date = series["date"][-1]
    years = max((end_date - start_date).days / 365.2425, 1e-9)
    cagr = (end_value / start_value) ** (1.0 / years) - 1.0 if start_value > 0 else 0.0

    running_peak = -math.inf
    max_drawdown = 0.0
    for value in series[value_col].to_list():
        running_peak = max(running_peak, float(value))
        if running_peak > 0:
            max_drawdown = min(max_drawdown, (float(value) / running_peak) - 1.0)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else float("inf")
    return {
        "start_value": start_value,
        "end_value": end_value,
        "cagr": cagr,
        "max_drawdown_pct": max_drawdown,
        "calmar": calmar,
    }


def compute_rolling_cagr(curve: pl.DataFrame, value_col: str, window_days: int = 756) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    dates = curve["date"].to_list()
    values = curve[value_col].to_list()
    for idx in range(window_days, len(dates)):
        start_idx = idx - window_days
        start_value = float(values[start_idx])
        end_value = float(values[idx])
        years = max((dates[idx] - dates[start_idx]).days / 365.2425, 1e-9)
        rows.append(
            {
                "date": dates[idx],
                "rolling_3y_cagr": (end_value / start_value) ** (1.0 / years) - 1.0 if start_value > 0 else None,
            }
        )
    return pl.DataFrame(rows)


def join_strategy_and_benchmark(strategy_curve: pl.DataFrame, benchmark_curve: pl.DataFrame) -> pl.DataFrame:
    joined = strategy_curve.select("date", "equity").join(benchmark_curve, on="date", how="inner")
    joined = joined.rename({"tri": "benchmark_tri"})
    return joined


def compute_rolling_summary(strategy_roll: pl.DataFrame, benchmark_roll: pl.DataFrame) -> dict[str, Any]:
    joined = strategy_roll.join(benchmark_roll, on="date", how="inner", suffix="_benchmark")
    beat_rate = float(
        joined.select((pl.col("rolling_3y_cagr") > pl.col("rolling_3y_cagr_benchmark")).mean()).item()
    ) if joined.height else 0.0
    return {
        "strategy_latest": float(strategy_roll["rolling_3y_cagr"][-1]) if strategy_roll.height else None,
        "benchmark_latest": float(benchmark_roll["rolling_3y_cagr"][-1]) if benchmark_roll.height else None,
        "strategy_median": float(strategy_roll["rolling_3y_cagr"].median()) if strategy_roll.height else None,
        "benchmark_median": float(benchmark_roll["rolling_3y_cagr"].median()) if benchmark_roll.height else None,
        "strategy_best": float(strategy_roll["rolling_3y_cagr"].max()) if strategy_roll.height else None,
        "benchmark_best": float(benchmark_roll["rolling_3y_cagr"].max()) if benchmark_roll.height else None,
        "strategy_worst": float(strategy_roll["rolling_3y_cagr"].min()) if strategy_roll.height else None,
        "benchmark_worst": float(benchmark_roll["rolling_3y_cagr"].min()) if benchmark_roll.height else None,
        "strategy_beat_rate": beat_rate,
    }


def compute_year_contribution(strategy_curve: pl.DataFrame) -> pl.DataFrame:
    return (
        strategy_curve
        .drop_nulls(["daily_pnl", "daily_return"])
        .with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.col("daily_pnl").sum().alias("pnl"),
            (((1.0 + pl.col("daily_return")).product()) - 1.0).alias("return_pct"),
        )
        .sort("year")
    )


def compute_ex_2023_cagr(strategy_curve: pl.DataFrame, initial_capital: float) -> dict[str, float]:
    curve = strategy_curve.sort("date")
    years = max((curve["date"][-1] - curve["date"][0]).days / 365.2425, 1e-9)
    equity = initial_capital
    for row in curve.iter_rows(named=True):
        daily_return = row["daily_return"]
        if daily_return is None:
            continue
        if row["date"].year == 2023:
            continue
        equity *= 1.0 + float(daily_return)
    cagr = (equity / initial_capital) ** (1.0 / years) - 1.0 if initial_capital > 0.0 else 0.0
    return {"final_equity_ex_2023": equity, "cagr_ex_2023": cagr}


def load_benchmark_curve(path: Path, start_date: date, end_date: date) -> pl.DataFrame:
    return (
        pl.read_csv(path, try_parse_dates=True)
        .with_columns(pl.col("date").cast(pl.Date))
        .filter((pl.col("date") >= pl.lit(start_date)) & (pl.col("date") <= pl.lit(end_date)))
        .sort("date")
    )


def compute_regime_inputs(
    settings: Any,
    start_date: date,
    end_date: date,
    benchmark_curve: pl.DataFrame,
) -> pl.DataFrame:
    lookback_start = start_date - timedelta(days=500)
    parquet_path = settings.runtime.data_dir / "yf_adjusted" / "ohlcv_all.parquet"

    breadth = (
        pl.scan_parquet(parquet_path)
        .filter((pl.col("date") >= pl.lit(lookback_start)) & (pl.col("date") <= pl.lit(end_date)))
        .select("date", "symbol", "close")
        .sort(["symbol", "date"])
        .with_columns(pl.col("close").rolling_mean(200).over("symbol").alias("sma200"))
        .with_columns((pl.col("close") > pl.col("sma200")).cast(pl.Float64).alias("above_200"))
        .filter(pl.col("date") >= pl.lit(start_date))
        .group_by("date")
        .agg(
            pl.col("above_200").mean().alias("breadth_pct"),
            pl.col("above_200").count().alias("breadth_count"),
        )
        .sort("date")
        .collect()
    )

    benchmark = (
        benchmark_curve
        .sort("date")
        .with_columns(
            pl.col("tri").rolling_mean(50).alias("tri_sma50"),
            pl.col("tri").rolling_mean(200).alias("tri_sma200"),
            pl.col("tri").shift(20).rolling_mean(200).alias("tri_sma200_lag20"),
            (pl.col("tri") / pl.col("tri").shift(1) - 1.0).alias("benchmark_return"),
        )
        .with_columns(
            pl.col("benchmark_return").rolling_std(20).mul(math.sqrt(252.0)).alias("vol_20_ann")
        )
        .with_columns(
            (
                (pl.col("tri") > pl.col("tri_sma200"))
                & (pl.col("tri_sma50") > pl.col("tri_sma200"))
                & (pl.col("tri_sma200") > pl.col("tri_sma200_lag20"))
            ).alias("trending_market")
        )
        .with_columns(
            (
                pl.col("vol_20_ann") > float(settings.portfolio.regime_high_volatility_threshold)
            ).alias("high_vol_market")
        )
        .select("date", "trending_market", "high_vol_market", "vol_20_ann")
    )

    return benchmark.join(breadth, on="date", how="left").with_columns(
        (pl.col("breadth_pct") < float(settings.market_filter.breadth_weak_pct)).alias("weak_breadth")
    )


def filter_signals(
    signals: pl.DataFrame,
    *,
    rs_rank_min: float | None = None,
    rs_rank_max: float | None = None,
) -> pl.DataFrame:
    filtered = signals
    if rs_rank_min is not None and "rs_rank" in filtered.columns:
        filtered = filtered.filter(pl.col("rs_rank") >= float(rs_rank_min))
    if rs_rank_max is not None and "rs_rank" in filtered.columns:
        filtered = filtered.filter(pl.col("rs_rank") <= float(rs_rank_max))
    return filtered.sort(["signal_date", "symbol"])


def build_entry_controls(
    settings: Any,
    regime_df: pl.DataFrame,
    *,
    portfolio_overrides: dict[str, Any] | None = None,
) -> tuple[dict[date, bool], dict[date, float]]:
    overrides = portfolio_overrides or {}
    regime_gate_enabled = bool(overrides.get("regime_gate_enabled", settings.portfolio.regime_gate_enabled))
    if not regime_gate_enabled or regime_df.is_empty():
        return {}, {}

    high_volatility_threshold = float(
        overrides.get(
            "regime_high_volatility_threshold",
            settings.portfolio.regime_high_volatility_threshold,
        )
    )
    cooldown_days = int(overrides.get("regime_cooldown_days", settings.portfolio.regime_cooldown_days))
    breadth_soft_floor = overrides.get("breadth_soft_floor_pct", settings.portfolio.breadth_soft_floor_pct)
    breadth_soft_size_mult = float(
        overrides.get("breadth_soft_size_mult", settings.portfolio.breadth_soft_size_mult)
    )
    gate: dict[date, bool] = {}
    size_mult: dict[date, float] = {}
    cooldown_remaining = 0

    for row in regime_df.sort("date").iter_rows(named=True):
        today = row["date"]
        vol_20_ann = row.get("vol_20_ann")
        if vol_20_ann is None:
            high_vol = bool(row.get("high_vol_market") or False)
        else:
            high_vol = float(vol_20_ann) > high_volatility_threshold
        entries_allowed = (not high_vol) and cooldown_remaining == 0
        gate[today] = entries_allowed

        breadth = row.get("breadth_pct")
        if breadth_soft_floor is not None and breadth is not None and float(breadth) < float(breadth_soft_floor):
            size_mult[today] = breadth_soft_size_mult
        else:
            size_mult[today] = 1.0

        if high_vol:
            cooldown_remaining = cooldown_days
        elif cooldown_remaining > 0:
            cooldown_remaining -= 1

    return gate, size_mult


def compute_regime_summary(strategy_curve: pl.DataFrame, trades: pl.DataFrame, regime_df: pl.DataFrame) -> dict[str, Any]:
    merged_daily = strategy_curve.join(regime_df, on="date", how="left")
    merged_daily = merged_daily.with_columns((pl.col("top_sector_weight") > 0.35).alias("sector_concentration"))

    regime_daily_stats: dict[str, Any] = {}
    for flag in ("trending_market", "weak_breadth", "high_vol_market", "sector_concentration"):
        active = merged_daily.filter(pl.col(flag) == True).drop_nulls(["daily_pnl"])
        inactive = merged_daily.filter(pl.col(flag) == False).drop_nulls(["daily_pnl"])
        active_pnl = float(active["daily_pnl"].sum()) if active.height else 0.0
        inactive_pnl = float(inactive["daily_pnl"].sum()) if inactive.height else 0.0
        regime_daily_stats[flag] = {
            "active_days": active.height,
            "inactive_days": inactive.height,
            "active_pnl": active_pnl,
            "inactive_pnl": inactive_pnl,
            "active_avg_daily_return": float(active["daily_return"].mean()) if active.height else None,
            "inactive_avg_daily_return": float(inactive["daily_return"].mean()) if inactive.height else None,
        }

    entry_regime = trades.filter(pl.col("entry_date").is_not_null()).join(
        regime_df.rename({"date": "entry_date"}), on="entry_date", how="left"
    )
    trade_regime_stats: dict[str, Any] = {}
    for flag in ("trending_market", "weak_breadth", "high_vol_market"):
        active = entry_regime.filter(pl.col(flag) == True).drop_nulls(["pnl", "r_multiple"])
        if active.height:
            trade_regime_stats[flag] = {
                "trade_count": active.height,
                "win_rate": float(active.select((pl.col("pnl") > 0).mean()).item()),
                "avg_r": float(active["r_multiple"].mean()),
                "total_pnl": float(active["pnl"].sum()),
            }
        else:
            trade_regime_stats[flag] = {
                "trade_count": 0,
                "win_rate": None,
                "avg_r": None,
                "total_pnl": 0.0,
            }

    sweet_spot = merged_daily.filter(
        (pl.col("trending_market") == True)
        & (pl.col("weak_breadth") == False)
        & (pl.col("high_vol_market") == False)
    ).drop_nulls(["daily_pnl"])
    hostile = merged_daily.filter(
        (pl.col("trending_market") == False)
        | (pl.col("weak_breadth") == True)
        | (pl.col("high_vol_market") == True)
    ).drop_nulls(["daily_pnl"])

    return {
        "daily": regime_daily_stats,
        "trade_entries": trade_regime_stats,
        "sweet_spot": {
            "days": sweet_spot.height,
            "pnl": float(sweet_spot["daily_pnl"].sum()) if sweet_spot.height else 0.0,
            "avg_daily_return": float(sweet_spot["daily_return"].mean()) if sweet_spot.height else None,
        },
        "hostile": {
            "days": hostile.height,
            "pnl": float(hostile["daily_pnl"].sum()) if hostile.height else 0.0,
            "avg_daily_return": float(hostile["daily_return"].mean()) if hostile.height else None,
        },
        "daily_frame": merged_daily,
    }


def to_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100.0


def main() -> None:
    args = parse_args()
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    output_dir = Path(args.output_dir) / f"{start_date.isoformat()}_{end_date.isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    tri_csv_path, constituent_csv_path = ensure_reference_data(Path(args.reference_dir), start_date, end_date)
    sector_map = load_sector_map(constituent_csv_path)

    bundle, signals, ohlcv = load_inputs(start_date, end_date)
    benchmark_curve = load_benchmark_curve(tri_csv_path, start_date, end_date)
    filtered_signals = filter_signals(
        signals,
        rs_rank_min=bundle["settings"].backtest.rs_rank_min,
        rs_rank_max=bundle["settings"].backtest.rs_rank_max,
    )
    regime_inputs = compute_regime_inputs(bundle["settings"], start_date, end_date, benchmark_curve)
    entry_gate: dict[date, bool] = {}
    entry_size_mult: dict[date, float] = {}
    if bundle["settings"].backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = build_entry_controls(bundle["settings"], regime_inputs)

    py_result = simulate_portfolio_python(
        filtered_signals,
        ohlcv,
        bundle["params"],
        bundle["portfolio"],
        sector_map,
        exit_config=bundle["exit_config"] if bundle["settings"].backtest.use_extended_exit_model else None,
        entry_gate=entry_gate,
        entry_size_mult=entry_size_mult,
    )
    verify_rust = False
    rust_result: dict[str, Any] | None = None
    if verify_rust:
        rust_result = rust_portfolio_result(filtered_signals, ohlcv, bundle["params"], bundle["portfolio"])
        if not math.isclose(float(rust_result["final_equity"]), float(py_result["final_equity"]), rel_tol=1e-9, abs_tol=1e-6):
            raise RuntimeError(
                f"Python diagnostic simulation does not match Rust final equity: "
                f"rust={rust_result['final_equity']}, python={py_result['final_equity']}"
            )

    strategy_curve = py_result["daily_equity"].sort("date")
    trades = py_result["trades"]
    joined_curve = join_strategy_and_benchmark(strategy_curve, benchmark_curve)

    strategy_stats = compute_performance_stats(strategy_curve.select("date", "equity"), "equity")
    benchmark_stats = compute_performance_stats(benchmark_curve.select("date", "tri"), "tri")

    strategy_roll = compute_rolling_cagr(joined_curve.select("date", "equity"), "equity")
    benchmark_roll = compute_rolling_cagr(joined_curve.select("date", "benchmark_tri"), "benchmark_tri")
    rolling_summary = compute_rolling_summary(strategy_roll, benchmark_roll)

    closed_trade_pnl = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    total_pnl = float(py_result["final_equity"] - bundle["portfolio"]["initial_capital"])
    top10_trade_pnl = float(closed_trade_pnl.sort("pnl", descending=True).head(10)["pnl"].sum()) if closed_trade_pnl.height else 0.0

    year_contribution = compute_year_contribution(strategy_curve)
    pnl_2023 = float(
        year_contribution.filter(pl.col("year") == 2023).select("pnl").item()
    ) if year_contribution.filter(pl.col("year") == 2023).height else 0.0
    ex_2023 = compute_ex_2023_cagr(strategy_curve, bundle["portfolio"]["initial_capital"])

    regime_summary = compute_regime_summary(strategy_curve, closed_trade_pnl, regime_inputs)
    regime_daily_frame = regime_summary.pop("daily_frame")

    sector_coverage = float(
        closed_trade_pnl.filter(pl.col("sector") != "UNKNOWN").height / closed_trade_pnl.height
    ) if closed_trade_pnl.height else 0.0

    summary = {
        "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "verification": {
            "rust_final_equity": float(rust_result["final_equity"]) if rust_result is not None else None,
            "python_final_equity": float(py_result["final_equity"]),
            "rust_trade_rows": len(rust_result["trades"]) if rust_result is not None else None,
            "python_trade_rows": trades.height,
        },
        "strategy": {
            **strategy_stats,
            "total_return_pct": (float(py_result["final_equity"]) / bundle["portfolio"]["initial_capital"] - 1.0),
            "filled_trades": int(closed_trade_pnl.height),
            "total_signals": int(filtered_signals.height),
        },
        "benchmark": benchmark_stats,
        "rolling_3y": rolling_summary,
        "contribution": {
            "total_pnl": total_pnl,
            "top10_trade_pnl": top10_trade_pnl,
            "top10_trade_share": (top10_trade_pnl / total_pnl) if total_pnl else None,
            "pnl_2023": pnl_2023,
            "pnl_2023_share": (pnl_2023 / total_pnl) if total_pnl else None,
            **ex_2023,
        },
        "regime": {
            **regime_summary,
            "sector_map_trade_coverage": sector_coverage,
        },
    }

    summary_path = output_dir / "summary.json"
    strategy_curve.write_csv(output_dir / "strategy_daily_equity.csv")
    benchmark_curve.write_csv(output_dir / "benchmark_daily_tri.csv")
    strategy_roll.rename({"rolling_3y_cagr": "strategy_rolling_3y_cagr"}).join(
        benchmark_roll.rename({"rolling_3y_cagr": "benchmark_rolling_3y_cagr"}),
        on="date",
        how="inner",
    ).write_csv(output_dir / "rolling_3y_cagr.csv")
    closed_trade_pnl.sort(["exit_date", "symbol"]).write_csv(output_dir / "trade_pnl.csv")
    year_contribution.write_csv(output_dir / "yearly_pnl_contribution.csv")
    regime_daily_frame.write_csv(output_dir / "regime_daily_tags.csv")
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote diagnostics to: {output_dir}")


if __name__ == "__main__":
    main()

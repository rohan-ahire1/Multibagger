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

from sepa_scanner.backtest import (
    BacktestParams,
    ExitMode,
    simulate_portfolio_python,
    build_entry_controls,
    compute_regime_inputs,
    filter_signals,
)
from sepa_scanner.backtest.queries import (
    get_backtest_diagnostics,
    get_backtest_ohlcv,
    get_backtest_signals,
)
from sepa_scanner.backtest.exit_engine import (
    BarData,
    ExitConfig,
    TradeState,
    evaluate_exit,
)
from sepa_scanner.backtest.models import ExitPhase
from sepa_scanner.backtest.simulation import _build_exit_config, _rolling_mean, _rolling_min, _ema
from sepa_scanner.strategy.ranking import (
    add_signal_score,
    add_signal_size_multiplier,
    load_sector_map,
    select_top_signals_by_day,
)
from sepa_scanner.settings import load_settings, AppSettings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

try:
    import multibagger_core
except ImportError:  # pragma: no cover - exercised only when extension is missing
    multibagger_core = None


OFFICIAL_TRI_URL = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
OFFICIAL_CONSTITUENTS_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"

SIGNAL_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "default": {},
    "championship": {
        "rs_rank_min": 70.0,
        "rs_rank_max": 99.5,
        "range10_max": 0.20,
        "voldry_max": 1.20,
        "contraction_count_min": 0,
    },
    "championship_loose": {
        "rs_rank_min": 60.0,
        "rs_rank_max": 99.5,
        "range10_max": 0.25,
        "voldry_max": 1.50,
        "contraction_count_min": 0,
    },
}

SIGNAL_QUALITY_FEATURE_COLUMNS = [
    "tt_pass",
    "rs_pass",
    "setup_pass",
    "vcp1_range10_ok",
    "vcp2_voldry_ok",
    "vcp3_atr_trend_ok",
    "vcp4_atr_contract_ok",
    "vcp5_extension_ok",
    "vcp6_base_depth_ok",
    "contraction_count",
    "contraction_quality",
    "setup_active_streak",
    "pivot_age_days",
    "watch_fail_mask",
    "breakout_fail_mask",
]


@dataclass(slots=True)
class PendingSignal:
    symbol: str
    signal_date: date
    entry_ref: float
    stop: float
    pivot: float | None
    rs_rank: float | None
    size_mult: float
    expiration_date: date
    range10: float | None = None
    voldry: float | None = None


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
    signal_size_mult: float
    dead_money_deadline_days: int | None
    dead_money_extended: bool
    pyramid_adds_taken: int
    next_pyramid_r: float | None
    phase: ExitPhase = ExitPhase.INITIAL_STOP
    trail_mode: str = "adaptive"
    tighten_activated: bool = False
    rs_rank: float | None = None
    range10: float | None = None
    voldry: float | None = None


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
        "dead_money_extend_to_breakeven": settings.exit_strategy.dead_money_extend_to_breakeven,
        "time_stop_days": settings.exit_strategy.time_stop_days,
        "max_hold_before_tighten": settings.exit_strategy.max_hold_before_tighten,
        "climax_min_hold_days": settings.exit_strategy.climax_min_hold_days,
        "climax_conditions_required": settings.exit_strategy.climax_conditions_required,
        "soft_expiry_days": settings.exit_strategy.soft_expiry_days,
        "soft_expiry_gain": settings.exit_strategy.soft_expiry_gain,
    }


def load_inputs(start_date: date, end_date: date) -> tuple[dict[str, Any], pl.DataFrame, pl.DataFrame]:
    settings = load_settings()
    parquet_store = ParquetOhlcvStore(settings.runtime.data_dir / "yf_adjusted")
    conn = duckdb.connect(str(settings.runtime.data_dir / "db" / "sepa.duckdb"), read_only=True)

    signals = get_backtest_signals(conn, start_date=start_date, end_date=end_date, symbol=None)
    symbols = signals["symbol"].unique().to_list()
    diagnostics = get_backtest_diagnostics(conn, start_date=start_date, end_date=end_date, symbols=symbols)
    ohlcv_end_date = end_date + timedelta(days=(settings.backtest.pending_days + settings.backtest.max_hold_days) * 2)
    ohlcv = get_backtest_ohlcv(parquet_store, symbols=symbols, start_date=start_date, end_date=ohlcv_end_date)
    conn.close()

    # CRITICAL: Adjust signal prices to match OHLCV adjusted basis
    # Join signals with ohlcv on date/symbol to get the adjustment factor
    sig_with_ohlcv = signals.join(
        ohlcv.select(["date", "symbol", "close"]).rename({"close": "ohlcv_close", "date": "signal_date"}),
        on=["signal_date", "symbol"],
        how="inner"
    )
    
    # Calculate adjustment factor
    # Some signals might not have a matching OHLCV row if data is missing
    sig_with_ohlcv = sig_with_ohlcv.with_columns(
        (pl.col("ohlcv_close") / pl.col("close")).alias("_adj_factor")
    )
    
    # Apply factor
    signals = sig_with_ohlcv.with_columns(
        (pl.col("entry_ref") * pl.col("_adj_factor")).alias("entry_ref"),
        (pl.col("stop") * pl.col("_adj_factor")).alias("stop"),
        (pl.when(pl.col("pivot").is_not_null()).then(pl.col("pivot") * pl.col("_adj_factor")).otherwise(None)).alias("pivot")
    ).drop(["ohlcv_close", "_adj_factor"])
    
    print(f"Adjusted prices for {signals.height} signals to match OHLCV basis")

    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=max(settings.backtest.max_hold_days, 90),
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(settings.backtest.exit_mode),
        trail_activation_r=settings.exit_strategy.trail_activation_r,
        trail_pct=settings.exit_strategy.trail_pct,
        dead_money_days=settings.exit_strategy.dead_money_days,
        dead_money_extend_days=settings.exit_strategy.dead_money_extend_days,
        dead_money_min_open_r=settings.exit_strategy.dead_money_min_open_r,
        dead_money_min_mfe_r=settings.exit_strategy.dead_money_min_mfe_r,
        dead_money_extend_to_breakeven=settings.exit_strategy.dead_money_extend_to_breakeven,
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
        "pyramid_max_adds": settings.portfolio.pyramid_max_adds,
        "pyramid_trigger_r": settings.portfolio.pyramid_trigger_r,
        "pyramid_add_interval_r": settings.portfolio.pyramid_add_interval_r,
        "pyramid_risk_frac": settings.portfolio.pyramid_risk_frac,
    }

    return {
        "settings": settings,
        "params": params,
        "portfolio": portfolio,
        "exit_config": resolve_exit_config(settings),
        "diagnostics": diagnostics,
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
        volumes = group["volume"].to_list() if "volume" in group.columns else [0] * len(closes)
        date_to_idx = {d: idx for idx, d in enumerate(dates)}

        # Technical indicators for exit engine
        sma_50 = _rolling_mean(closes, 50)
        sma_200 = _rolling_mean(closes, 200)
        ema_10 = _ema(closes, 10)
        ema_21 = _ema(closes, 21)
        ema_50 = _ema(closes, 50)
        low_10d = _rolling_min(lows, 10)
        prev_close = [0.0] + closes[:-1]
        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range_20 = _rolling_mean(ranges, 20)
        vol_floats = [float(v) for v in volumes]
        avg_volume_50 = _rolling_mean(vol_floats, 50)

        by_symbol[symbol] = {
            "dates": dates,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes,
            "date_to_idx": date_to_idx,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "ema_10": ema_10,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "low_10d": low_10d,
            "prev_close": prev_close,
            "avg_range_20": avg_range_20,
            "avg_volume_50": avg_volume_50,
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
            "dead_money_extend_to_breakeven": params.dead_money_extend_to_breakeven,
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
    print(f"Unique dates in simulation: {len(unique_dates)}")
    if unique_dates:
        print(f"Date range: {unique_dates[0]} to {unique_dates[-1]}")
    # Build phased exit config using sensible defaults.
    # The settings file may have aggressive values tuned for the old inline engine
    # (e.g. time_stop_days=2), so we use ExitConfig defaults and only override
    # fields that are compatible with the phased exit approach.
    phased_exit_config = ExitConfig()
    if exit_config:
        safe_overrides = {
            "breakeven_activation_r", "partial_profit_r", "partial_profit_pct",
            "trail_pct_bull", "trail_pct_bear", "trail_pct",
            "climax_min_hold_days", "climax_conditions_required",
        }
        for key, value in exit_config.items():
            if value is not None and key in safe_overrides and hasattr(phased_exit_config, key):
                setattr(phased_exit_config, key, type(getattr(phased_exit_config, key))(value))

    entry_gate = entry_gate or {}
    entry_size_mult = entry_size_mult or {}

    signals_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for signal in symbols:
        signals_by_date[signal["signal_date"]].append(signal)

    initial_capital = float(portfolio["initial_capital"])
    cash = initial_capital
    equity_peak = initial_capital
    open_positions: list[OpenPosition] = []
    pending_signals: list[PendingSignal] = []
    closed_trades: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous_equity = initial_capital

    for today in unique_dates:
        # print(f"Processing {today}...")
        i = 0
        while i < len(open_positions):
            pos = open_positions[i]
            sym_prices = symbol_data.get(pos.symbol)
            if sym_prices is None:
                i += 1
                continue

            idx = sym_prices["date_to_idx"].get(today)
            if idx is not None:
                open_price = float(sym_prices["opens"][idx])
                high_price = float(sym_prices["highs"][idx])
                low_price = float(sym_prices["lows"][idx])
                close_price = float(sym_prices["closes"][idx])
                holding_days = (today - pos.entry_date).days
                market_hostile = not bool(entry_gate.get(today, True))

                pos.mae_price = min(pos.mae_price, low_price)
                pos.mfe_price = max(pos.mfe_price, high_price)

                # --- Gap-down stop: if open gaps below stop, exit at open ---
                if open_price <= pos.active_stop:
                    cash += pos.shares * open_price
                    closed_trades.append(
                        close_trade_dict(
                            symbol=pos.symbol, signal_date=pos.signal_date,
                            status="stopped", entry_ref=pos.entry_ref,
                            stop=pos.stop, pivot=pos.pivot,
                            entry_date=pos.entry_date, entry_price=pos.entry_price,
                            exit_date=today, exit_price=open_price,
                            holding_days=holding_days,
                            mae_price=pos.mae_price, mfe_price=pos.mfe_price,
                            shares=pos.shares, sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                            original_shares=pos.original_shares, realized_pnl=pos.realized_pnl,
                            rs_rank=pos.rs_rank, range10=pos.range10, voldry=pos.voldry,
                        )
                    )
                    open_positions.pop(i)
                    continue

                # --- Build TradeState from OpenPosition ---
                trade_state = TradeState(
                    symbol=pos.symbol,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    initial_risk=pos.initial_risk,
                    initial_stop=pos.stop,
                    planned_shares=int(pos.original_shares),
                    phase=pos.phase,
                    current_shares=int(pos.shares),
                    active_stop=pos.active_stop,
                    highest_close=pos.mfe_price,
                    trail_mode=pos.trail_mode,
                    breakeven_activated=pos.breakeven_activated,
                    partial_taken=pos.partial_taken,
                    trail_activated=pos.trail_activated,
                    tighten_activated=pos.tighten_activated,
                    days_held=holding_days - 1,  # evaluate_exit increments by 1
                )

                # --- Build BarData from symbol_data ---
                bar = BarData(
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=int(sym_prices["volumes"][idx]),
                    sma_50=float(sym_prices["sma_50"][idx]),
                    sma_200=float(sym_prices["sma_200"][idx]),
                    ema_10=float(sym_prices["ema_10"][idx]),
                    ema_21=float(sym_prices["ema_21"][idx]),
                    ema_50=float(sym_prices["ema_50"][idx]),
                    low_10d=float(sym_prices["low_10d"][idx]),
                    prev_close=float(sym_prices["prev_close"][idx]),
                    avg_range_20=float(sym_prices["avg_range_20"][idx]),
                    avg_volume_50=float(sym_prices["avg_volume_50"][idx]),
                )

                # --- Call phased exit engine ---
                market_regime = "defensive" if market_hostile else "bull"
                actions = evaluate_exit(
                    trade_state,
                    bar=bar,
                    config=phased_exit_config,
                    market_regime=market_regime,
                    rs_rank=80.0,
                )

                # --- Sync state back to OpenPosition ---
                pos.phase = trade_state.phase
                pos.active_stop = trade_state.active_stop
                pos.breakeven_activated = trade_state.breakeven_activated
                pos.partial_taken = trade_state.partial_taken
                pos.trail_activated = trade_state.trail_activated
                pos.tighten_activated = trade_state.tighten_activated
                pos.trail_mode = trade_state.trail_mode
                pos.mfe_price = max(pos.mfe_price, trade_state.highest_close)

                # --- Process actions ---
                exit_price: float | None = None
                status = ""
                for action in actions:
                    if action.action == "SELL_ALL":
                        exit_price = close_price
                        status = action.reason
                        break
                    elif action.action == "SELL_PARTIAL":
                        sell_shares = min(pos.shares, float(action.shares))
                        if sell_shares > 0:
                            cash += sell_shares * close_price
                            pos.realized_pnl += sell_shares * (close_price - pos.entry_price)
                            pos.shares -= sell_shares
                            pos.partial_exit_price = close_price
                            pos.partial_exit_date = today
                            pos.partial_exit_r = (close_price - pos.entry_price) / pos.initial_risk if pos.initial_risk > 0 else 0.0

                # --- Hard max hold cap ---
                if exit_price is None and holding_days >= params.max_hold_days:
                    exit_price = close_price
                    status = "max_hold"

                if exit_price is not None:
                    cash += pos.shares * exit_price
                    closed_trades.append(
                        close_trade_dict(
                            symbol=pos.symbol, signal_date=pos.signal_date,
                            status=status, entry_ref=pos.entry_ref,
                            stop=pos.stop, pivot=pos.pivot,
                            entry_date=pos.entry_date, entry_price=pos.entry_price,
                            exit_date=today, exit_price=exit_price,
                            holding_days=holding_days,
                            mae_price=pos.mae_price, mfe_price=pos.mfe_price,
                            shares=pos.shares, sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                            original_shares=pos.original_shares, realized_pnl=pos.realized_pnl,
                            rs_rank=pos.rs_rank, range10=pos.range10, voldry=pos.voldry,
                        )
                    )
                    open_positions.pop(i)
                    continue
            i += 1

        invested_value = 0.0
        marked_sector_values: dict[str, float] = defaultdict(float)
        open_sector_costs: dict[str, float] = defaultdict(float)
        open_sector_counts: dict[str, int] = defaultdict(int)
        for pos in open_positions:
            sym_prices = symbol_data[pos.symbol]
            idx = sym_prices["date_to_idx"].get(today)
            marked_value = pos.shares * (sym_prices["closes"][idx] if idx is not None else pos.entry_price)
            invested_value += marked_value
            sector = analysis_sector_map.get(pos.symbol, "UNKNOWN")
            marked_sector_values[sector] += marked_value
            open_sector_costs[sector] += pos.cost_basis
            open_sector_counts[sector] += 1

        current_equity = cash + invested_value
        equity_peak = max(equity_peak, current_equity)

        dd_pct = ((equity_peak - current_equity) / equity_peak) if equity_peak > 0.0 else 0.0
        if dd_pct >= portfolio["drawdown_close_pct"] and open_positions:
            forced_closes: list[OpenPosition] = list(open_positions)
            open_positions.clear()
            for pos in forced_closes:
                sym_prices = symbol_data[pos.symbol]
                idx = sym_prices["date_to_idx"].get(today)
                close_price = sym_prices["closes"][idx] if idx is not None else pos.entry_price
                holding_days = (today - pos.entry_date).days
                cash += pos.shares * close_price
                closed_trades.append(
                    close_trade_dict(
                        symbol=pos.symbol,
                        signal_date=pos.signal_date,
                        status="drawdown_close",
                        entry_ref=pos.entry_ref,
                        stop=pos.stop,
                        pivot=pos.pivot,
                        entry_date=pos.entry_date,
                        entry_price=pos.entry_price,
                        exit_date=today,
                        exit_price=close_price,
                        holding_days=holding_days,
                        mae_price=pos.mae_price,
                        mfe_price=pos.mfe_price,
                        shares=pos.shares,
                        sector=analysis_sector_map.get(pos.symbol, "UNKNOWN"),
                        original_shares=pos.original_shares,
                        realized_pnl=pos.realized_pnl,
                        rs_rank=pos.rs_rank, range10=pos.range10, voldry=pos.voldry,
                    )
                )

            invested_value = 0.0
            marked_sector_values = defaultdict(float)
            open_sector_costs = defaultdict(float)
            open_sector_counts = defaultdict(int)
            current_equity = cash
            dd_pct = ((equity_peak - current_equity) / equity_peak) if equity_peak > 0.0 else 0.0

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
                        size_mult=float(sig.get("signal_size_mult", 1.0) or 1.0),
                        expiration_date=today + timedelta(days=params.pending_days),
                        range10=float(sig["range10"]) if sig.get("range10") is not None else None,
                        voldry=float(sig["voldry"]) if sig.get("voldry") is not None else None,
                    )
                )

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

        pyramid_max_adds = int(portfolio.get("pyramid_max_adds", 0) or 0)
        if pyramid_max_adds > 0 and day_entries_allowed and size_mult > 0.0 and current_equity > 0.0:
            for pos in open_positions:
                if (
                    pos.next_pyramid_r is None
                    or pos.pyramid_adds_taken >= pyramid_max_adds
                    or pos.active_stop < pos.entry_price
                ):
                    continue

                sym_prices = symbol_data.get(pos.symbol)
                if sym_prices is None:
                    continue
                idx = sym_prices["date_to_idx"].get(today)
                if idx is None:
                    continue

                open_price = sym_prices["opens"][idx]
                high_price = sym_prices["highs"][idx]
                close_price = sym_prices["closes"][idx]
                add_trigger_price = pos.entry_price + (pos.initial_risk * pos.next_pyramid_r)
                if high_price < add_trigger_price:
                    continue

                add_fill_price = max(open_price, add_trigger_price)
                add_risk_per_share = add_fill_price - pos.active_stop
                if add_risk_per_share <= 0.0:
                    continue

                add_risk_budget = (
                    current_equity
                    * portfolio["risk_per_trade_pct"]
                    * size_mult
                    * day_entry_size_mult
                    * pos.signal_size_mult
                    * float(portfolio.get("pyramid_risk_frac", 0.50))
                )
                if add_risk_budget <= 0.0:
                    continue
                total_heat = sum(p.initial_risk * p.shares for p in open_positions)
                if ((total_heat + add_risk_budget) / current_equity) > portfolio["max_heat_pct"]:
                    continue

                add_shares = add_risk_budget / add_risk_per_share
                add_cost = add_shares * add_fill_price
                if add_cost > cash:
                    continue
                sector = analysis_sector_map.get(pos.symbol, "UNKNOWN")
                max_sector_pct = float(portfolio.get("max_sector_pct", 1.0) or 1.0)
                if (
                    current_equity > 0.0
                    and max_sector_pct < 1.0
                    and ((marked_sector_values.get(sector, 0.0) + add_cost) / current_equity) > max_sector_pct
                ):
                    continue

                cash -= add_cost
                invested_value += add_shares * close_price
                current_equity = cash + invested_value
                marked_sector_values[sector] += add_cost
                open_sector_costs[sector] += add_cost
                pos.shares += add_shares
                pos.original_shares += add_shares
                pos.cost_basis += add_cost
                pos.pyramid_adds_taken += 1
                pos.next_pyramid_r += float(portfolio.get("pyramid_add_interval_r", 0.5))

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

            if size_mult == 0.0 or len(open_positions) >= int(portfolio["max_positions"]) or current_equity <= 0.0:
                j += 1
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
                open_price = float(sym_prices["opens"][idx])
                high_price = float(sym_prices["highs"][idx])
                target = float(pending.entry_ref)

                if open_price >= target or high_price >= target:
                    fill_price = max(open_price, pending.entry_ref)
                    risk_budget = (
                        current_equity
                        * portfolio["risk_per_trade_pct"]
                        * size_mult
                        * day_entry_size_mult
                        * max(0.0, pending.size_mult)
                    )
                    if risk_budget <= 0.0:
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
                        # Size by risk budget
                        shares_by_risk = risk_budget / risk_per_share
                        # Size by available cash
                        shares_by_cash = cash / fill_price

                        shares = min(shares_by_risk, shares_by_cash)
                        cost = shares * fill_price

                        if shares > 0:
                            sector = analysis_sector_map.get(pending.symbol, "UNKNOWN")
                            max_sector_positions = int(portfolio.get("max_sector_positions", 0) or 0)
                            if max_sector_positions > 0 and open_sector_counts.get(sector, 0) >= max_sector_positions:
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
                            max_sector_pct = float(portfolio.get("max_sector_pct", 1.0) or 1.0)
                            if (
                                current_equity > 0.0
                                and max_sector_pct < 1.0
                                and ((marked_sector_values.get(sector, 0.0) + cost) / current_equity) > max_sector_pct
                            ):
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
                                        signal_size_mult=max(0.0, pending.size_mult),
                                        dead_money_deadline_days=params.dead_money_days,
                                        dead_money_extended=False,
                                        pyramid_adds_taken=0,
                                        next_pyramid_r=(
                                            float(portfolio.get("pyramid_trigger_r", 1.0))
                                            if int(portfolio.get("pyramid_max_adds", 0) or 0) > 0
                                            else None
                                        ),
                                        rs_rank=pending.rs_rank,
                                        range10=pending.range10,
                                        voldry=pending.voldry,
                                    )
                                )
                                marked_sector_values[sector] += cost
                                open_sector_costs[sector] += cost
                                open_sector_counts[sector] += 1
                                filled = True

            if filled:
                pending_signals.pop(j)
            else:
                j += 1

        # Recalculate end-of-day equity
        invested_value = 0.0
        marked_sector_values = defaultdict(float)
        for pos in open_positions:
            sym_prices = symbol_data[pos.symbol]
            idx = sym_prices["date_to_idx"].get(today)
            price = sym_prices["closes"][idx] if idx is not None else pos.entry_price
            mv = pos.shares * price
            invested_value += mv
            sector = analysis_sector_map.get(pos.symbol, "UNKNOWN")
            marked_sector_values[sector] += mv

        current_equity = cash + invested_value
        equity_peak = max(equity_peak, current_equity)

        top_sector_weight = 0.0
        if invested_value > 0.0 and marked_sector_values:
            top_sector_weight = max(marked_sector_values.values()) / invested_value

        daily_pnl = current_equity - previous_equity
        daily_return = (current_equity / previous_equity) - 1.0 if previous_equity > 0.0 else 0.0

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
                    rs_rank=pos.rs_rank, range10=pos.range10, voldry=pos.voldry,
                )
            )

    daily_equity_df = pl.DataFrame(daily_rows, infer_schema_length=10000)
    if not daily_equity_df.is_empty():
        cols_to_cast = [c for c in daily_equity_df.columns if c != "date"]
        daily_equity_df = daily_equity_df.with_columns([
            pl.col("date").cast(pl.Date),
            *[pl.col(c).cast(pl.Float64) for c in cols_to_cast]
        ])

    return {
        "final_equity": cash,
        "daily_equity": daily_equity_df,
        "trades": pl.DataFrame(closed_trades, infer_schema_length=10000).sort(["signal_date", "symbol"]),
    }


def build_signal_rows(signals: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in signals.sort(["signal_date", "symbol"]).to_dicts():
        # Ensure signal_date is a date object for dictionary lookup
        sig_date = row["signal_date"]
        if isinstance(sig_date, datetime):
            sig_date = sig_date.date()
            
        rows.append(
            {
                "symbol": str(row["symbol"]),
                "signal_date": sig_date,
                "entry_ref": float(row["entry_ref"]),
                "stop": float(row["stop"]),
                "pivot": float(row["pivot"]) if row.get("pivot") is not None else None,
                "rs_rank": float(row["rs_rank"]) if row.get("rs_rank") is not None else None,
                "range10": float(row["range10"]) if row.get("range10") is not None else None,
                "voldry": float(row["voldry"]) if row.get("voldry") is not None else None,
                "signal_size_mult": float(row.get("signal_size_mult", 1.0) or 1.0),
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
    rs_rank: float | None = None,
    range10: float | None = None,
    voldry: float | None = None,
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
        "rs_rank": rs_rank,
        "range10": range10,
        "voldry": voldry,
    }


def compute_performance_stats(
    curve: pl.DataFrame, value_col: str, first_trade_date: date | None = None
) -> dict[str, float]:
    series = curve.sort("date")
    if first_trade_date:
        series = series.filter(pl.col("date") >= first_trade_date)

    if series.is_empty():
        return {
            "start_value": 0.0,
            "end_value": 0.0,
            "cagr": 0.0,
            "max_drawdown_pct": 0.0,
            "calmar": 0.0,
        }

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
    calmar = cagr / abs(max_drawdown) if max_drawdown < -0.0001 else 50.0
    return {
        "start_value": start_value,
        "end_value": end_value,
        "cagr": cagr,
        "max_drawdown_pct": max_drawdown,
        "calmar": min(calmar, 50.0),
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


def resolve_signal_quality_overrides(settings: Any) -> dict[str, Any]:
    return {
        "range10_min": settings.backtest.signal_quality_range10_min,
        "range10_max": settings.backtest.signal_quality_range10_max,
        "voldry_min": settings.backtest.signal_quality_voldry_min,
        "voldry_max": settings.backtest.signal_quality_voldry_max,
        "contraction_count_min": settings.backtest.signal_quality_contraction_min,
        "contraction_count_max": settings.backtest.signal_quality_contraction_max,
        "setup_active_streak_min": settings.backtest.signal_quality_setup_streak_min,
        "pivot_age_days_max": settings.backtest.signal_quality_pivot_age_max,
        "offense_score_min": settings.backtest.signal_quality_offense_score_min,
        "offense_score_max": settings.backtest.signal_quality_offense_score_max,
        "size_tilt_mode": settings.backtest.signal_quality_size_tilt_mode,
        "size_tilt_score_min": settings.backtest.signal_quality_size_tilt_score_min,
        "size_tilt_score_max": settings.backtest.signal_quality_size_tilt_score_max,
        "size_tilt_min_mult": settings.backtest.signal_quality_size_tilt_min_mult,
        "size_tilt_max_mult": settings.backtest.signal_quality_size_tilt_max_mult,
    }


def resolve_signal_quality_filters(
    *,
    quality_profile: str = "default",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if quality_profile not in SIGNAL_QUALITY_PROFILES:
        raise ValueError(
            f"Unknown signal quality profile '{quality_profile}'. "
            f"Expected one of: {sorted(SIGNAL_QUALITY_PROFILES)}"
        )
    quality_filters = dict(SIGNAL_QUALITY_PROFILES[quality_profile])
    for key, value in (overrides or {}).items():
        if value is not None:
            quality_filters[key] = value
    return quality_filters


def apply_signal_quality_filters(
    signals: pl.DataFrame,
    diagnostics: pl.DataFrame | None,
    quality_filters: dict[str, Any],
    *,
    ranking_mode: str = "default",
) -> pl.DataFrame:
    if not quality_filters:
        return signals
    if diagnostics is None or diagnostics.is_empty():
        return signals

    merged = attach_signal_quality_features(signals, diagnostics)

    if quality_filters.get("range10_min") is not None:
        merged = merged.filter(pl.col("range10") >= float(quality_filters["range10_min"]))
    if quality_filters.get("range10_max") is not None:
        merged = merged.filter(pl.col("range10") <= float(quality_filters["range10_max"]))
    if quality_filters.get("voldry_min") is not None:
        merged = merged.filter(pl.col("voldry") >= float(quality_filters["voldry_min"]))
    if quality_filters.get("voldry_max") is not None:
        merged = merged.filter(pl.col("voldry") <= float(quality_filters["voldry_max"]))
    if quality_filters.get("contraction_count_min") is not None:
        merged = merged.filter(
            pl.col("contraction_count") >= int(quality_filters["contraction_count_min"])
        )
    if quality_filters.get("contraction_count_max") is not None:
        merged = merged.filter(
            pl.col("contraction_count") <= int(quality_filters["contraction_count_max"])
        )
    if quality_filters.get("setup_active_streak_min") is not None:
        merged = merged.filter(
            pl.col("setup_active_streak") >= int(quality_filters["setup_active_streak_min"])
        )
    if quality_filters.get("pivot_age_days_max") is not None:
        merged = merged.filter(pl.col("pivot_age_days") <= int(quality_filters["pivot_age_days_max"]))
    offense_score_min = quality_filters.get("offense_score_min")
    offense_score_max = quality_filters.get("offense_score_max")
    if offense_score_min is not None or offense_score_max is not None:
        merged = add_signal_score(merged, ranking_mode="offense_v2", score_column="_offense_v2_score")
        if offense_score_min is not None:
            merged = merged.filter(pl.col("_offense_v2_score") >= float(offense_score_min))
        if offense_score_max is not None:
            merged = merged.filter(pl.col("_offense_v2_score") <= float(offense_score_max))
        merged = merged.drop("_offense_v2_score", strict=False)

    return merged


def attach_signal_quality_features(
    signals: pl.DataFrame,
    diagnostics: pl.DataFrame | None,
) -> pl.DataFrame:
    if diagnostics is None or diagnostics.is_empty() or signals.is_empty():
        return signals

    diag_feature_cols = [
        col for col in SIGNAL_QUALITY_FEATURE_COLUMNS
        if col in diagnostics.columns and col not in signals.columns
    ]
    if not diag_feature_cols:
        return signals

    return signals.join(
        diagnostics.select(pl.col("diag_date").alias("signal_date"), "symbol", *diag_feature_cols),
        on=["signal_date", "symbol"],
        how="left",
    )


def apply_signal_size_tilt(
    signals: pl.DataFrame,
    diagnostics: pl.DataFrame | None,
    *,
    tilt_mode: str = "off",
    score_min: float = 5.5,
    score_max: float = 8.0,
    min_mult: float = 0.60,
    max_mult: float = 1.40,
) -> pl.DataFrame:
    if signals.is_empty() or tilt_mode in {"off", "none", ""}:
        return signals

    merged = attach_signal_quality_features(signals, diagnostics)
    if merged.is_empty():
        return signals.with_columns(pl.lit(1.0).alias("signal_size_mult"))

    return add_signal_size_multiplier(
        merged,
        tilt_mode=tilt_mode,
        score_min=float(score_min),
        score_max=float(score_max),
        min_mult=float(min_mult),
        max_mult=float(max_mult),
        mult_column="signal_size_mult",
    )


def filter_signals(
    signals: pl.DataFrame,
    *,
    settings: AppSettings,
    diagnostics: pl.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    rs_rank_min: float | None = None,
    rs_rank_max: float | None = None,
    ranking_mode: str = "default",
    max_signals_per_day: int | None = None,
    quality_profile: str = "default",
    quality_overrides: dict[str, Any] | None = None,
) -> pl.DataFrame:
    original_columns = list(signals.columns)
    quality_filters = resolve_signal_quality_filters(
        quality_profile=quality_profile,
        overrides=quality_overrides,
    )
    size_tilt_config = {
        "tilt_mode": quality_filters.pop("size_tilt_mode", "off"),
        "score_min": quality_filters.pop("size_tilt_score_min", 5.5),
        "score_max": quality_filters.pop("size_tilt_score_max", 8.0),
        "min_mult": quality_filters.pop("size_tilt_min_mult", 0.60),
        "max_mult": quality_filters.pop("size_tilt_max_mult", 1.40),
    }
    if rs_rank_min is None:
        rs_rank_min = quality_filters.pop("rs_rank_min", None)
    else:
        quality_filters.pop("rs_rank_min", None)
    if rs_rank_max is None:
        rs_rank_max = quality_filters.pop("rs_rank_max", None)
    else:
        quality_filters.pop("rs_rank_max", None)

    # Add Sector RS if needed
    if settings.backtest.sector_alpha_enabled and sector_map:
        sector_df = pl.DataFrame([
            {"symbol": sym, "sector": sec} for sym, sec in sector_map.items()
        ])
        signals = signals.join(sector_df, on="symbol", how="left").fill_null("UNKNOWN")
        
        # Calculate Sector RS: Median RS Rank of signals in that sector on that day
        sector_rs_df = (
            signals.group_by(["signal_date", "sector"])
            .agg(pl.col("rs_rank").median().alias("sector_rs"))
        )
        signals = signals.join(sector_rs_df, on=["signal_date", "sector"], how="left")
        
        # Apply Size Tilt based on Sector Alpha from Settings
        s_cfg = settings.backtest
        signals = signals.with_columns(
            pl.when(pl.col("sector_rs") >= s_cfg.sector_alpha_threshold_high).then(pl.lit(s_cfg.sector_alpha_mult_high))
            .when(pl.col("sector_rs") <= s_cfg.sector_alpha_threshold_low).then(pl.lit(s_cfg.sector_alpha_mult_low))
            .otherwise(pl.lit(1.0))
            .alias("signal_size_mult")
        )

    filtered = signals
    if rs_rank_min is not None and "rs_rank" in filtered.columns:
        filtered = filtered.filter(pl.col("rs_rank") >= float(rs_rank_min))
    print(f"Signals after RS rank filter ({rs_rank_min}): {filtered.height}")
    
    if rs_rank_max is not None and "rs_rank" in filtered.columns:
        filtered = filtered.filter(pl.col("rs_rank") <= float(rs_rank_max))
    
    filtered = apply_signal_quality_filters(
        filtered,
        diagnostics,
        quality_filters,
        ranking_mode=ranking_mode,
    )
    print(f"Signals after quality filters: {filtered.height}")
    
    filtered = attach_signal_quality_features(filtered, diagnostics)
    ranked = select_top_signals_by_day(
        filtered.sort(["signal_date", "symbol"]),
        max_signals_per_day=max_signals_per_day,
        ranking_mode=ranking_mode,
    )
    print(f"Signals after daily ranking: {ranked.height}")
    ranked = apply_signal_size_tilt(ranked, diagnostics, **size_tilt_config)
    
    output_columns = [column for column in original_columns if column in ranked.columns]
    if "signal_size_mult" in ranked.columns and "signal_size_mult" not in output_columns:
        output_columns.append("signal_size_mult")
    return ranked.select(output_columns)


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
    if trades.is_empty():
        return {
            "daily": {},
            "trade_entries": {},
            "sweet_spot": {"days": 0, "pnl": 0.0, "avg_daily_return": 0.0},
            "hostile": {"days": 0, "pnl": 0.0, "avg_daily_return": 0.0},
            "daily_frame": strategy_curve,
        }
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

    # tri_csv_path, constituent_csv_path = ensure_reference_data(Path(args.reference_dir), start_date, end_date)
    bundle, signals, ohlcv = load_inputs(start_date, end_date)
    settings = bundle["settings"]
    
    # Load benchmark from OHLCV if available
    benchmark_curve = (
        ohlcv.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select(["date", "close"])
        .rename({"close": "tri"})
        .filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
        .sort("date")
    )
    if benchmark_curve.is_empty():
        # Fallback to local file if Parquet doesn't have it
        local_ref = Path(args.reference_dir) / f"nifty500_tri_2010-01-04_2026-03-17.csv"
        if local_ref.exists():
            benchmark_curve = load_benchmark_curve(local_ref, start_date, end_date)
        else:
            raise RuntimeError(f"Benchmark {settings.market.benchmark_symbol} not found in OHLCV or local ref")
    
    constituent_csv_path = Path(args.reference_dir) / "nifty500_constituents.csv"
    sector_map = load_sector_map(constituent_csv_path)
    print(f"Loaded RS rank min: {bundle['settings'].backtest.rs_rank_min}")
    print(f"Loaded max positions: {bundle['portfolio']['max_positions']}")
    print(f"Loaded risk per trade: {bundle['portfolio']['risk_per_trade_pct']}")
    
    filtered_signals = filter_signals(
        signals,
        settings=bundle["settings"],
        diagnostics=bundle["diagnostics"],
        sector_map=sector_map,
        rs_rank_min=bundle["settings"].backtest.rs_rank_min,
        rs_rank_max=bundle["settings"].backtest.rs_rank_max,
        ranking_mode=bundle["settings"].backtest.signal_ranking_mode,
        max_signals_per_day=bundle["settings"].backtest.max_signals_per_day,
        quality_profile=bundle["settings"].backtest.signal_quality_profile,
        quality_overrides=resolve_signal_quality_overrides(bundle["settings"]),
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
    
    first_entry = trades.filter(pl.col("entry_date").is_not_null())["entry_date"].min()
    if first_entry is None:
        first_entry = start_date

    joined_curve = join_strategy_and_benchmark(strategy_curve, benchmark_curve)

    strategy_stats = compute_performance_stats(strategy_curve.select("date", "equity"), "equity", first_trade_date=first_entry)
    benchmark_stats = compute_performance_stats(benchmark_curve.select("date", "tri"), "tri", first_trade_date=first_entry)

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

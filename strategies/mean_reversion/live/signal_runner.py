from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.strategy.ranking import load_sector_map
from strategies.mean_reversion.exit_engine import MRExitConfig, evaluate_mr_exit
from strategies.mean_reversion.live.config import LOCKED_PARAMS, OHLCV_PATH, SECTOR_MAP_PATH
from strategies.mean_reversion.live.friction import (
    BUY_TAX_MULT,
    SLIPPAGE_BUY_MULT,
    buy_cash_outflow,
    sell_cash_inflow,
)
from strategies.mean_reversion.live.paper_portfolio import process_paper_fills
from strategies.mean_reversion.live.portfolio_state import PortfolioState
from strategies.mean_reversion.signal_engine import scan_mean_reversion
from strategies.mean_reversion.simulation import build_mr_symbol_data
from strategies.mean_reversion.universe import load_nifty100_universe


@dataclass(slots=True)
class DailyResult:
    exits: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    paper_exits: list[dict[str, Any]]
    paper_entries: list[dict[str, Any]]
    as_of_date: date
    paper_fills: list[dict[str, Any]] = field(default_factory=list)
    paper_equity: float = 0.0
    paper_cash: float = 0.0
    paper_positions: int = 0
    live_equity: float = 0.0
    live_cash: float = 0.0
    live_positions: int = 0
    latest_data_date: date | None = None
    note: str = ""


def ensure_latest_data(today: date, store_path: Path = OHLCV_PATH) -> None:
    if not store_path.exists():
        return
    latest = ParquetOhlcvStore(store_path.parent).latest_date()
    if latest is None or latest < today:
        from scripts.yf_daily_update import daily_update

        daily_update(store_path=store_path, target_date=today.isoformat())


def load_live_ohlcv(today: date, *, update_data: bool = True) -> pl.DataFrame:
    if update_data:
        ensure_latest_data(today)
    universe = load_nifty100_universe()
    benchmark_symbols = {"^NSEI", "NIFTY 50", "NIFTYBEES"}
    store = ParquetOhlcvStore(OHLCV_PATH.parent)
    frame = store.read_frame(
        start_date=None,
        end_date=today,
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
    )
    if frame.is_empty():
        return frame
    return frame.filter(pl.col("symbol").is_in(sorted(universe | benchmark_symbols))).sort(["symbol", "date"])


def _current_prices(symbol_data: dict[str, dict[str, Any]], today: date) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, data in symbol_data.items():
        if symbol.startswith("_"):
            continue
        idx = data["date_to_idx"].get(today)
        if idx is not None:
            prices[symbol] = float(data["closes"][idx])
    return prices


def _exit_rows(
    state: PortfolioState,
    symbol_data: dict[str, dict[str, Any]],
    today: date,
    *,
    mutate: bool,
) -> list[dict[str, Any]]:
    exit_config = MRExitConfig(
        exit_mode=str(LOCKED_PARAMS["exit_mode"]),
        time_stop_days=int(LOCKED_PARAMS["time_stop_days"]),
        stop_atr_mult=float(LOCKED_PARAMS["stop_atr_mult"]),
        max_hold_days=int(LOCKED_PARAMS["max_hold_days"]),
    )
    rows: list[dict[str, Any]] = []
    for position in list(state.positions):
        data = symbol_data.get(position.symbol)
        bar_idx = data["date_to_idx"].get(today) if data else None
        if bar_idx is None:
            continue
        close_price = float(data["closes"][bar_idx])
        action = evaluate_mr_exit(
            entry_price=position.entry_price,
            entry_date=position.entry_date,
            active_stop=position.active_stop,
            today=today,
            open_price=float(data["opens"][bar_idx]),
            high_price=float(data["highs"][bar_idx]),
            low_price=float(data["lows"][bar_idx]),
            close_price=close_price,
            sma5=float(data["sma5"][bar_idx]),
            sma3=float(data["sma3"][bar_idx]),
            rsi2=float(data["rsi2"][bar_idx]),
            cum_rsi2=float(data["cum_rsi"][bar_idx]),
            config=exit_config,
        )
        if action.action != "SELL_ALL":
            continue
        # Net-of-cost P&L estimate: the user will sell at tomorrow's open, so
        # today's close is a proxy. Apply sell-side friction for an honest figure.
        _, _, _, net_proceeds = sell_cash_inflow(position.shares, close_price)
        pnl_est = net_proceeds - position.cost_basis
        row = {
            "symbol": position.symbol,
            "exit_price_est": close_price,
            "reason": action.reason,
            "shares": position.shares,
            "pnl_est": pnl_est,
            "entry_price": position.entry_price,
            "holding_days": (today - position.entry_date).days,
        }
        rows.append(row)
        if mutate:
            closed = state.close_position(position.symbol, close_price, today, action.reason)
            row["closed_trade"] = closed
    return rows


def _entry_rows(
    signals: pl.DataFrame,
    state: PortfolioState,
    sector_map: dict[str, str],
    current_prices: dict[str, float],
    *,
    is_paper: bool,
) -> list[dict[str, Any]]:
    if signals.is_empty():
        return []

    entries: list[dict[str, Any]] = []
    open_symbols = state.open_symbols()
    equity = state.equity(current_prices)
    slots_left = max(int(LOCKED_PARAMS["max_positions"]) - len(state.positions), 0)
    if slots_left <= 0:
        return []

    for signal in signals.sort(["rsi2", "symbol"]).to_dicts():
        if len(entries) >= slots_left:
            break
        symbol = str(signal["symbol"])
        if symbol in open_symbols:
            continue

        sector = sector_map.get(symbol, "UNKNOWN")
        if sector != "UNKNOWN":
            current_sector_count = state.sector_count(sector, sector_map)
            planned_sector_count = sum(1 for row in entries if row["sector"] == sector)
            if current_sector_count + planned_sector_count >= int(LOCKED_PARAMS["max_sector_positions"]):
                continue

        entry_est = float(signal["close"])
        stop = float(signal["stop"])
        risk_per_share = entry_est - stop
        if risk_per_share <= 0.0 or math.isnan(risk_per_share):
            continue
        shares = math.floor((equity * float(LOCKED_PARAMS["risk_per_trade_pct"])) / risk_per_share)
        if shares <= 0:
            continue
        # Cost basis includes adverse slippage and statutory buy-side charges,
        # reserved out of cash so the displayed size is actually affordable.
        _, _, cost_basis = buy_cash_outflow(float(shares), entry_est)
        if cost_basis > state.cash:
            shares = math.floor(state.cash / (entry_est * SLIPPAGE_BUY_MULT * BUY_TAX_MULT)) if entry_est > 0 else 0
            if shares <= 0:
                continue
            _, _, cost_basis = buy_cash_outflow(float(shares), entry_est)

        entries.append(
            {
                "symbol": symbol,
                "signal_date": signal["signal_date"].isoformat(),
                "entry_price_est": entry_est,
                "stop": stop,
                "shares": float(shares),
                "cost_basis": cost_basis,
                "sector": sector,
                "risk_amount": (entry_est - stop) * shares,
                "rsi2": float(signal["rsi2"]),
                "atr14": float(signal["atr14"]),
                "conditional_open_gt_stop": True,
                "is_paper": is_paper,
            }
        )
    return entries


def _today_signals(ohlcv: pl.DataFrame, today: date) -> pl.DataFrame:
    if ohlcv.is_empty():
        return scan_mean_reversion(ohlcv)
    universe = load_nifty100_universe()
    # scan_mean_reversion skips the last bar of each symbol (needs next-day bar for
    # backtest entry). For live use we append a synthetic next-day row so today's
    # close is not skipped, then filter to signal_date == today.
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    last_bars = (
        ohlcv.filter(pl.col("date") == today)
        .with_columns(pl.lit(tomorrow).alias("date"))
    )
    ohlcv_extended = pl.concat([ohlcv, last_bars]).sort(["symbol", "date"])
    return scan_mean_reversion(
        ohlcv_extended,
        rsi_period=2,
        rsi_threshold=float(LOCKED_PARAMS["rsi_threshold"]),
        sma_trend_period=int(LOCKED_PARAMS["sma_trend_period"]),
        min_adv_inr=float(LOCKED_PARAMS["min_adv_inr"]),
        min_price=float(LOCKED_PARAMS["min_price"]),
        stop_atr_mult=float(LOCKED_PARAMS["stop_atr_mult"]),
        universe_symbols=universe,
    ).filter(pl.col("signal_date") == today)


def run_daily_signals(
    today: date,
    state: PortfolioState,
    paper_state: PortfolioState,
    *,
    update_data: bool = True,
) -> DailyResult:
    ohlcv = load_live_ohlcv(today, update_data=update_data)
    if ohlcv.is_empty():
        return DailyResult(
            exits=[],
            entries=[],
            paper_exits=[],
            paper_entries=[],
            as_of_date=today,
            note="No OHLCV data available.",
        )

    latest_data_date = ohlcv["date"].max()
    symbol_data, _ = build_mr_symbol_data(ohlcv)
    current_prices = _current_prices(symbol_data, today)
    if not current_prices:
        current_prices = _current_prices(symbol_data, latest_data_date)
    sector_map = load_sector_map(SECTOR_MAP_PATH)

    paper_fills = process_paper_fills(paper_state, symbol_data, today, paper_state.pending_entries)
    exits = _exit_rows(state, symbol_data, today, mutate=False)
    paper_exits = _exit_rows(paper_state, symbol_data, today, mutate=True)

    signals = _today_signals(ohlcv, today)
    entries = _entry_rows(signals, state, sector_map, current_prices, is_paper=False)
    paper_entries = _entry_rows(signals, paper_state, sector_map, current_prices, is_paper=True)
    paper_state.pending_entries = paper_entries

    note = ""
    if latest_data_date != today:
        note = f"No bar for {today.isoformat()}; latest data is {latest_data_date.isoformat()}."

    return DailyResult(
        exits=exits,
        entries=entries,
        paper_exits=paper_exits,
        paper_entries=paper_entries,
        as_of_date=today,
        paper_fills=paper_fills,
        paper_equity=paper_state.equity(current_prices),
        paper_cash=paper_state.cash,
        paper_positions=len(paper_state.positions),
        live_equity=state.equity(current_prices),
        live_cash=state.cash,
        live_positions=len(state.positions),
        latest_data_date=latest_data_date,
        note=note,
    )


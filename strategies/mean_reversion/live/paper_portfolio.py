from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path
from typing import Any

from strategies.mean_reversion.live.config import PAPER_TRADES_PATH
from strategies.mean_reversion.live.friction import BUY_TAX_MULT, buy_cash_outflow
from strategies.mean_reversion.live.portfolio_state import LivePosition, PortfolioState


PAPER_TRADE_FIELDS = [
    "fill_date",
    "symbol",
    "entry_price",
    "shares",
    "cost_basis",
    "stop",
    "initial_risk",
    "rsi2_at_entry",
    "atr14",
]


def append_paper_fills(fills: list[dict[str, Any]], path: Path = PAPER_TRADES_PATH) -> None:
    if not fills:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAPER_TRADE_FIELDS)
        if needs_header:
            writer.writeheader()
        for fill in fills:
            writer.writerow({field: fill.get(field) for field in PAPER_TRADE_FIELDS})


def process_paper_fills(
    paper_state: PortfolioState,
    symbol_data: dict[str, dict[str, Any]],
    today: date,
    pending_signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    open_symbols = paper_state.open_symbols()

    for signal in pending_signals:
        symbol = str(signal["symbol"])
        signal_date = date.fromisoformat(str(signal["signal_date"]))
        data = symbol_data.get(symbol)
        bar_idx = data["date_to_idx"].get(today) if data else None
        if bar_idx is None or today <= signal_date:
            remaining.append(signal)
            continue
        if symbol in open_symbols:
            continue

        open_price = float(data["opens"][bar_idx])
        stop = float(signal["stop"])
        # Gap-down guard uses the raw market open: if the stock opens at or
        # below our stop, the entry is invalid regardless of slippage.
        if open_price <= stop:
            continue

        shares = float(signal["shares"])
        # Apply realistic buy-side friction: adverse slippage on the fill price
        # plus statutory NSE delivery charges, all reserved out of cash.
        fill_price, charges, cost_basis = buy_cash_outflow(shares, open_price)
        # Cash guard: downsize to what the paper account can actually afford,
        # including charges. Integer shares to match live order sizing.
        if cost_basis > paper_state.cash:
            if paper_state.cash <= 0.0 or fill_price <= 0.0:
                continue
            shares = float(math.floor(paper_state.cash / (fill_price * BUY_TAX_MULT)))
            if shares <= 0.0:
                continue
            fill_price, charges, cost_basis = buy_cash_outflow(shares, open_price)

        initial_risk = fill_price - stop
        if initial_risk <= 0.0:
            continue
        position = LivePosition(
            symbol=symbol,
            entry_date=today,
            entry_price=fill_price,
            stop=stop,
            active_stop=stop,
            shares=shares,
            initial_risk=initial_risk,
            cost_basis=cost_basis,
            rsi2_at_entry=float(signal["rsi2"]),
            atr14=float(signal["atr14"]),
            is_paper=True,
        )
        paper_state.add_position(position)
        open_symbols.add(symbol)
        fills.append(
            {
                "fill_date": today.isoformat(),
                "symbol": symbol,
                "entry_price": fill_price,
                "shares": shares,
                "cost_basis": cost_basis,
                "entry_charges": charges,
                "stop": stop,
                "initial_risk": initial_risk,
                "rsi2_at_entry": float(signal["rsi2"]),
                "atr14": float(signal["atr14"]),
            }
        )

    paper_state.pending_entries = remaining
    return fills


from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from strategies.mean_reversion.live.config import LOCKED_PARAMS
from strategies.mean_reversion.live.friction import sell_cash_inflow


@dataclass(slots=True)
class LivePosition:
    symbol: str
    entry_date: date
    entry_price: float
    stop: float
    active_stop: float
    shares: float
    initial_risk: float
    cost_basis: float
    rsi2_at_entry: float
    atr14: float
    is_paper: bool

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> LivePosition:
        return cls(
            symbol=str(row["symbol"]),
            entry_date=date.fromisoformat(str(row["entry_date"])),
            entry_price=float(row["entry_price"]),
            stop=float(row["stop"]),
            active_stop=float(row["active_stop"]),
            shares=float(row["shares"]),
            initial_risk=float(row["initial_risk"]),
            cost_basis=float(row["cost_basis"]),
            rsi2_at_entry=float(row["rsi2_at_entry"]),
            atr14=float(row["atr14"]),
            is_paper=bool(row.get("is_paper", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["entry_date"] = self.entry_date.isoformat()
        return row


@dataclass(slots=True)
class PortfolioState:
    cash: float
    positions: list[LivePosition]
    closed_trades: list[dict[str, Any]]
    pending_entries: list[dict[str, Any]]

    @classmethod
    def empty(cls) -> PortfolioState:
        return cls(
            cash=float(LOCKED_PARAMS["initial_capital"]),
            positions=[],
            closed_trades=[],
            pending_entries=[],
        )

    @classmethod
    def load(cls, path: Path) -> PortfolioState:
        if not path.exists():
            return cls.empty()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            cash=float(payload.get("cash", LOCKED_PARAMS["initial_capital"])),
            positions=[LivePosition.from_dict(row) for row in payload.get("positions", [])],
            closed_trades=list(payload.get("closed_trades", [])),
            pending_entries=list(payload.get("pending_entries", [])),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cash": self.cash,
            "positions": [position.to_dict() for position in self.positions],
            "closed_trades": self.closed_trades,
            "pending_entries": self.pending_entries,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def add_position(self, pos: LivePosition) -> None:
        if pos.symbol in self.open_symbols():
            raise ValueError(f"Position already open for {pos.symbol}")
        self.positions.append(pos)
        self.cash -= pos.cost_basis

    def close_position(self, symbol: str, exit_price: float, exit_date: date, reason: str) -> dict[str, Any]:
        for idx, pos in enumerate(self.positions):
            if pos.symbol != symbol:
                continue
            # Realistic sell-side friction: adverse slippage, statutory NSE
            # charges and the flat DP charge. ``cost_basis`` already includes
            # buy-side charges, so ``pnl`` is fully cost-inclusive.
            fill_price, exit_charges, dp_charge, proceeds = sell_cash_inflow(pos.shares, exit_price)
            pnl = proceeds - pos.cost_basis
            risk_amount = pos.initial_risk * pos.shares
            trade = {
                "symbol": pos.symbol,
                "entry_date": pos.entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "entry_price": pos.entry_price,
                "exit_price": fill_price,
                "exit_price_raw": exit_price,
                "shares": pos.shares,
                "cost_basis": pos.cost_basis,
                "proceeds": proceeds,
                "exit_charges": exit_charges,
                "dp_charge": dp_charge,
                "pnl": pnl,
                "return_pct": (pnl / pos.cost_basis) * 100.0 if pos.cost_basis > 0 else 0.0,
                "r_multiple": pnl / risk_amount if risk_amount > 0 else 0.0,
                "holding_days": (exit_date - pos.entry_date).days,
                "exit_reason": reason,
                "is_paper": pos.is_paper,
                "rsi2_at_entry": pos.rsi2_at_entry,
                "atr14": pos.atr14,
            }
            self.cash += proceeds
            self.closed_trades.append(trade)
            self.positions.pop(idx)
            return trade
        raise KeyError(f"No open position for {symbol}")

    def open_symbols(self) -> set[str]:
        return {position.symbol for position in self.positions}

    def equity(self, current_prices: dict[str, float]) -> float:
        marked = 0.0
        for position in self.positions:
            price = current_prices.get(position.symbol, position.entry_price)
            marked += position.shares * price
        return self.cash + marked

    def sector_count(self, sector: str, sector_map: dict[str, str]) -> int:
        return sum(1 for position in self.positions if sector_map.get(position.symbol, "UNKNOWN") == sector)


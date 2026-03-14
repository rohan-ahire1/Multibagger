from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenPosition:
    symbol: str
    sector: str
    risk_amount: float
    position_value: float


@dataclass
class PortfolioManager:
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.01
    max_positions: int = 10
    max_sector_positions: int = 3
    max_sector_pct: float = 0.40
    max_heat_pct: float = 0.10
    drawdown_half_pct: float = 0.10
    drawdown_stop_pct: float = 0.15
    drawdown_close_pct: float = 0.20

    equity_peak: float = field(init=False)
    current_equity: float = field(init=False)
    positions: list[OpenPosition] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.equity_peak = self.initial_capital
        self.current_equity = self.initial_capital

    @property
    def drawdown_pct(self) -> float:
        if self.equity_peak == 0:
            return 0.0
        return (self.equity_peak - self.current_equity) / self.equity_peak

    @property
    def position_size_multiplier(self) -> float:
        dd = self.drawdown_pct
        if dd >= self.drawdown_close_pct:
            return 0.0
        if dd >= self.drawdown_stop_pct:
            return 0.0
        if dd >= self.drawdown_half_pct:
            return 0.5
        return 1.0

    @property
    def total_heat(self) -> float:
        return sum(p.risk_amount for p in self.positions)

    def update_equity(self, equity: float) -> None:
        self.current_equity = equity
        self.equity_peak = max(self.equity_peak, equity)

    def can_open_trade(self, sector: str) -> bool:
        if self.position_size_multiplier == 0.0:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        sector_count = sum(1 for p in self.positions if p.sector == sector)
        if sector_count >= self.max_sector_positions:
            return False
        risk_amount = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier
        if (self.total_heat + risk_amount) / self.current_equity > self.max_heat_pct:
            return False
        return True

    def open_trade(
        self, symbol: str, sector: str, risk_per_share: float, entry_price: float,
    ) -> float:
        """Open a position. Returns the number of shares."""
        risk_budget = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier
        shares = risk_budget / risk_per_share if risk_per_share > 0 else 0
        position_value = shares * entry_price
        self.positions.append(OpenPosition(
            symbol=symbol, sector=sector,
            risk_amount=risk_budget, position_value=position_value,
        ))
        return shares

    def close_trade(self, symbol: str) -> None:
        self.positions = [p for p in self.positions if p.symbol != symbol]

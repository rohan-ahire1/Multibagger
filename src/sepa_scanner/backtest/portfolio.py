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
    regime_heat_mult_strong: float = 1.0
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

    @staticmethod
    def _has_known_sector(sector: str) -> bool:
        normalized = sector.strip().upper()
        return bool(normalized and normalized != "UNKNOWN")

    def sector_value(self, sector: str) -> float:
        return sum(p.position_value for p in self.positions if p.sector == sector)

    def preview_position_value(self, risk_per_share: float, entry_price: float, base_depth: float | None = None) -> float:
        if risk_per_share <= 0 or entry_price <= 0:
            return 0.0
        
        # Depth-weighted risk scaling
        depth_mult = 1.0
        if base_depth is not None:
            if base_depth > 0.20:
                depth_mult = 0.25
            elif base_depth > 0.10:
                depth_mult = 0.50
                
        risk_budget = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier * depth_mult
        shares = risk_budget / risk_per_share
        return shares * entry_price

    def update_equity(self, equity: float) -> None:
        self.current_equity = equity
        self.equity_peak = max(self.equity_peak, equity)

    def can_open_trade(self, sector: str, position_value: float | None = None, base_depth: float | None = None) -> bool:
        if self.position_size_multiplier == 0.0:
            return False
        if self.current_equity <= 0:
            return False
        if len(self.positions) >= self.max_positions:
            return False
        if self._has_known_sector(sector):
            sector_count = sum(1 for p in self.positions if p.sector == sector)
            if sector_count >= self.max_sector_positions:
                return False
            if position_value is not None:
                sector_pct = (self.sector_value(sector) + position_value) / self.current_equity
                if sector_pct > self.max_sector_pct:
                    return False
        
        # Depth-weighted risk amount
        depth_mult = 1.0
        if base_depth is not None:
            if base_depth > 0.20:
                depth_mult = 0.25
            elif base_depth > 0.10:
                depth_mult = 0.50

        risk_amount = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier * depth_mult
        if (self.total_heat + risk_amount) / self.current_equity > self.max_heat_pct:
            return False
        return True

    def open_trade(
        self, symbol: str, sector: str, risk_per_share: float, entry_price: float, base_depth: float | None = None,
    ) -> float:
        """Open a position. Returns the number of shares."""
        # Depth-weighted risk scaling
        depth_mult = 1.0
        if base_depth is not None:
            if base_depth > 0.20:
                depth_mult = 0.25
            elif base_depth > 0.10:
                depth_mult = 0.50

        risk_budget = self.current_equity * self.risk_per_trade_pct * self.position_size_multiplier * depth_mult
        shares = risk_budget / risk_per_share if risk_per_share > 0 else 0
        position_value = shares * entry_price
        self.positions.append(OpenPosition(
            symbol=symbol, sector=sector,
            risk_amount=risk_budget, position_value=position_value,
        ))
        return shares

    def close_trade(self, symbol: str) -> None:
        self.positions = [p for p in self.positions if p.symbol != symbol]

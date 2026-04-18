"""Minervini-style 6-phase exit engine: INITIAL_STOP → BREAKEVEN → PARTIAL_PROFIT → TRAILING → TIGHTEN → EXIT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sepa_scanner.backtest.models import ExitPhase


@dataclass(slots=True)
class ExitConfig:
    breakeven_activation_r: float = 1.0
    partial_profit_r: float = 2.0
    partial_profit_pct: float = 0.50
    trail_activation_r: float = 2.0
    max_loss_pct: float = 0.08
    absolute_max_loss_pct: float = 0.10
    trail_mode: str = "adaptive"
    trail_pct: float = 0.20
    trail_pct_bull: float = 0.25
    trail_pct_bear: float = 0.15
    extension_limit_200d: float = 2.0
    extension_limit_200d_extreme: float = 2.5
    extension_limit_50d: float = 1.25
    largest_day_gain_pct: float = 0.15
    gap_sell_threshold: float = 0.10
    time_stop_days: int = 15
    time_stop_min_r: float = 0.3
    max_hold_before_tighten: int = 60
    climax_min_hold_days: int = 15
    climax_conditions_required: int = 3
    soft_expiry_days: int = 45
    soft_expiry_gain: float = 0.20
    trail_widen_r: float = 3.0


@dataclass(slots=True)
class ExitAction:
    action: str  # "SELL_ALL" | "SELL_PARTIAL"
    reason: str
    shares: int = 0


@dataclass
class TradeState:
    symbol: str
    entry_date: date
    entry_price: float
    initial_risk: float
    initial_stop: float
    planned_shares: int

    phase: ExitPhase = ExitPhase.INITIAL_STOP
    current_shares: int = 0
    active_stop: float = 0.0
    highest_close: float = 0.0
    trail_mode: str = ""

    breakeven_activated: bool = False
    partial_taken: bool = False
    trail_activated: bool = False
    tighten_activated: bool = False

    days_held: int = 0
    r_multiple: float = 0.0
    partial_exit_price: float = 0.0
    partial_exit_date: date | None = None
    partial_r: float = 0.0

    exit_price: float = 0.0
    exit_date: date | None = None
    exit_reason: str = ""
    final_r: float = 0.0

    def __post_init__(self) -> None:
        if self.current_shares == 0:
            self.current_shares = self.planned_shares
        if self.active_stop == 0.0:
            self.active_stop = self.initial_stop
        if self.highest_close == 0.0:
            self.highest_close = self.entry_price


@dataclass(slots=True)
class BarData:
    open: float
    high: float
    low: float
    close: float
    volume: int
    sma_50: float
    sma_200: float
    ema_10: float
    ema_21: float
    ema_50: float
    low_10d: float
    prev_close: float
    avg_range_20: float
    avg_volume_50: float


def select_trail_mode(rs_rank: float, gain_pct: float, market_regime: str) -> str:
    if rs_rank >= 95 and gain_pct > 0.30:
        return "trail_50ema"
    if rs_rank >= 90 and market_regime == "bull" and gain_pct > 0.20:
        return "trail_50ema"
    if market_regime == "bull":
        return "trail_pct"
    return "trail_21ema"


def tighten_trail(current_mode: str) -> str:
    _map = {
        "trail_50ema": "trail_21ema",
        "trail_21ema": "trail_10d_low",
        "trail_10d_low": "trail_10d_low",
        "trail_pct": "trail_pct",
    }
    return _map.get(current_mode, "trail_21ema")


def compute_trail_value(
    trade: TradeState,
    *,
    trail_mode: str,
    market_regime: str,
    config: ExitConfig,
    ema21: float | None = None,
    ema50: float | None = None,
    low_10d: float | None = None,
) -> float:
    if trail_mode == "trail_21ema":
        return ema21 if ema21 is not None else trade.active_stop
    if trail_mode == "trail_50ema":
        return ema50 if ema50 is not None else trade.active_stop
    if trail_mode == "trail_10d_low":
        return low_10d if low_10d is not None else trade.active_stop
    if trail_mode == "trail_pct":
        if market_regime == "bull":
            pct = config.trail_pct_bull
        elif market_regime == "defensive":
            pct = config.trail_pct_bear
        else:
            pct = config.trail_pct
        return trade.highest_close * (1.0 - pct)
    raise ValueError(f"Unknown trail mode: {trail_mode}")


def detect_climax_top(
    *,
    days_held: int,
    daily_range: float,
    avg_range_20: float,
    vol_ratio: float,
    upper_wick_ratio: float,
    extension_50: float,
    gap_up_pct: float,
    config: ExitConfig,
) -> bool:
    if days_held < config.climax_min_hold_days:
        return False
    conditions = [
        daily_range > 2.0 * avg_range_20,
        vol_ratio > 2.5,
        upper_wick_ratio > 0.6,
        extension_50 > 1.20,
        gap_up_pct > 0.03,
    ]
    return sum(conditions) >= config.climax_conditions_required


def check_sell_into_strength(
    *,
    close: float,
    sma_200: float,
    sma_50: float,
    daily_return: float,
    gap_up_pct: float,
    vol_ratio: float,
    current_shares: int,
    config: ExitConfig,
) -> ExitAction | None:
    if sma_200 > 0 and close / sma_200 > config.extension_limit_200d_extreme:
        return ExitAction(action="SELL_ALL", reason="extension_200d_extreme")
    if sma_200 > 0 and close / sma_200 > config.extension_limit_200d:
        sell = max(1, int(current_shares * 0.50))
        return ExitAction(action="SELL_PARTIAL", reason="extension_200d", shares=sell)
    if daily_return > config.largest_day_gain_pct and vol_ratio > 2.0:
        sell = max(1, int(current_shares * 0.50))
        return ExitAction(action="SELL_PARTIAL", reason="largest_day_gain", shares=sell)
    if gap_up_pct > config.gap_sell_threshold and vol_ratio > 3.0:
        sell = max(1, int(current_shares * 0.50))
        return ExitAction(action="SELL_PARTIAL", reason="gap_sell", shares=sell)
    return None


def evaluate_exit(
    trade: TradeState,
    *,
    bar: BarData,
    config: ExitConfig,
    market_regime: str,
    rs_rank: float = 80.0,
) -> list[ExitAction]:
    actions: list[ExitAction] = []
    close = bar.close
    high = bar.high

    trade.days_held += 1
    trade.highest_close = max(trade.highest_close, close)
    if trade.initial_risk > 0:
        trade.r_multiple = (close - trade.entry_price) / trade.initial_risk

    # Phase 0: INITIAL_STOP
    if trade.phase == ExitPhase.INITIAL_STOP:
        if trade.days_held >= config.time_stop_days and trade.r_multiple < config.time_stop_min_r:
            actions.append(ExitAction(action="SELL_ALL", reason="time_stop"))
            return actions
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason="initial_stop"))
            return actions
        if high >= trade.entry_price + config.breakeven_activation_r * trade.initial_risk:
            trade.phase = ExitPhase.BREAKEVEN
            trade.breakeven_activated = True
            trade.active_stop = max(trade.active_stop, trade.entry_price)

    # Phase 1: BREAKEVEN
    if trade.phase == ExitPhase.BREAKEVEN:
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason="breakeven_stop"))
            return actions
        if high >= trade.entry_price + config.partial_profit_r * trade.initial_risk:
            trade.phase = ExitPhase.PARTIAL_PROFIT

    # Phase 2: PARTIAL_PROFIT
    if trade.phase == ExitPhase.PARTIAL_PROFIT and not trade.partial_taken:
        sell_shares = int(trade.current_shares * config.partial_profit_pct)
        if sell_shares > 0:
            actions.append(
                ExitAction(action="SELL_PARTIAL", reason="partial_profit", shares=sell_shares)
            )
            trade.partial_taken = True
            trade.current_shares -= sell_shares
            trade.partial_exit_price = close
            trade.partial_r = trade.r_multiple
        trade.phase = ExitPhase.TRAILING
        trade.trail_activated = True
        if config.trail_mode == "adaptive":
            gain_pct = (
                (close - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
            )
            trade.trail_mode = select_trail_mode(
                rs_rank=rs_rank, gain_pct=gain_pct, market_regime=market_regime
            )
        else:
            trade.trail_mode = config.trail_mode

    # Phase 3: TRAILING
    if trade.phase == ExitPhase.TRAILING:
        # Widen trail for big winners — once a trade reaches trail_widen_r,
        # switch to 50 EMA trail to give it room to consolidate
        if trade.r_multiple >= config.trail_widen_r and trade.trail_mode != "trail_50ema":
            trade.trail_mode = "trail_50ema"

        trail_value = compute_trail_value(
            trade,
            trail_mode=trade.trail_mode,
            market_regime=market_regime,
            config=config,
            ema21=bar.ema_21,
            ema50=bar.ema_50,
            low_10d=bar.low_10d,
        )
        trade.active_stop = max(trade.active_stop, trail_value)
        if close <= trade.active_stop:
            actions.append(
                ExitAction(action="SELL_ALL", reason=f"trail_{trade.trail_mode}")
            )
            return actions

        # Sell-into-strength
        strength = check_sell_into_strength(
            close=close,
            sma_200=bar.sma_200,
            sma_50=bar.sma_50,
            daily_return=(close - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0,
            gap_up_pct=(bar.open - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0,
            vol_ratio=bar.volume / bar.avg_volume_50 if bar.avg_volume_50 > 0 else 0,
            current_shares=trade.current_shares,
            config=config,
        )
        if strength:
            actions.append(strength)
            if strength.action == "SELL_ALL":
                return actions
            trade.current_shares -= strength.shares

        # Climax detection
        daily_range = (bar.high - bar.low) / bar.low if bar.low > 0 else 0
        upper_wick = (
            (bar.high - close) / (bar.high - bar.low) if (bar.high - bar.low) > 0 else 0
        )
        ext_50 = close / bar.sma_50 if bar.sma_50 > 0 else 0
        gap = (bar.low - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0
        if detect_climax_top(
            days_held=trade.days_held,
            daily_range=daily_range,
            avg_range_20=bar.avg_range_20,
            vol_ratio=bar.volume / bar.avg_volume_50 if bar.avg_volume_50 > 0 else 0,
            upper_wick_ratio=upper_wick,
            extension_50=ext_50,
            gap_up_pct=gap,
            config=config,
        ):
            sell = max(1, int(trade.current_shares * 0.50))
            if sell > 0 and trade.current_shares > 0:
                actions.append(ExitAction(action="SELL_PARTIAL", reason="climax_sell", shares=sell))
                trade.current_shares -= sell

        # Check tighten
        if _should_tighten(trade, bar=bar, config=config):
            trade.phase = ExitPhase.TIGHTEN
            trade.tighten_activated = True
            
        # Velocity Sniper: Soft Expiry
        # If we haven't reached the gain threshold by the soft expiry day, rotate the cash.
        # If we have, hold for the triple digits by tightening the trail.
        if trade.days_held == config.soft_expiry_days:
            gain_pct = (close - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
            if gain_pct < config.soft_expiry_gain:
                actions.append(ExitAction(action="SELL_ALL", reason="soft_expiry_rotation"))
                return actions
            else:
                # Stock is a monster, tighten trail to 10d low and keep holding
                trade.phase = ExitPhase.TIGHTEN
                trade.trail_mode = "trail_10d_low"
                trade.tighten_activated = True

    # Phase 4: TIGHTEN
    if trade.phase == ExitPhase.TIGHTEN:
        trade.trail_mode = tighten_trail(trade.trail_mode)
        trail_value = compute_trail_value(
            trade,
            trail_mode=trade.trail_mode,
            market_regime=market_regime,
            config=config,
            ema21=bar.ema_21,
            ema50=bar.ema_50,
            low_10d=bar.low_10d,
        )
        trade.active_stop = max(trade.active_stop, trail_value)
        if close <= trade.active_stop:
            actions.append(
                ExitAction(action="SELL_ALL", reason=f"tighten_{trade.trail_mode}")
            )
            return actions

    return actions


def evaluate_exit_baseline(
    trade: TradeState,
    *,
    bar: BarData,
    exit_mode: str,
) -> list[ExitAction]:
    """Strict Minervini (TLASMW) exit — three rules only:

    1. Hard stop: 7% below entry (stop is pre-set on TradeState).
    2. Breakeven: when price reaches entry + 3 × initial_risk (≈+21% on a 7% stop),
       raise stop to entry price.
    3. Trail (EMA modes only, active after breakeven):
       - baseline_ema10: close below 10-day EMA → exit
       - baseline_ema21: close below 21-day EMA → exit
       - baseline_hard:  no trail — just hold until stopped at entry or next stop level

    No partial profits, no time stops, no climax sells.
    """
    actions: list[ExitAction] = []
    close = bar.close

    trade.days_held += 1
    trade.highest_close = max(trade.highest_close, close)
    if trade.initial_risk > 0:
        trade.r_multiple = (close - trade.entry_price) / trade.initial_risk

    # Breakeven trigger: 3R above entry
    if not trade.breakeven_activated:
        if close >= trade.entry_price + 3.0 * trade.initial_risk:
            trade.breakeven_activated = True
            trade.active_stop = max(trade.active_stop, trade.entry_price)
            trade.phase = ExitPhase.BREAKEVEN

    # EMA trail: ratchet stop up to EMA value (only after breakeven, only EMA modes)
    if trade.breakeven_activated and exit_mode != "baseline_hard":
        ema_val = bar.ema_10 if exit_mode == "baseline_ema10" else bar.ema_21
        if ema_val > 0:
            trade.active_stop = max(trade.active_stop, ema_val)

    # Check stop
    if close <= trade.active_stop:
        if not trade.breakeven_activated:
            reason = "initial_stop"
        elif exit_mode == "baseline_hard":
            reason = "breakeven_stop"
        else:
            reason = f"trail_{exit_mode}"
        actions.append(ExitAction(action="SELL_ALL", reason=reason))

    return actions


def _should_tighten(trade: TradeState, *, bar: BarData, config: ExitConfig) -> bool:
    if trade.tighten_activated:
        return False
    if bar.close < bar.ema_21 and bar.prev_close >= bar.ema_21 and trade.days_held > 20:
        return True
    if trade.days_held >= config.max_hold_before_tighten:
        return True
    if bar.sma_50 > 0 and bar.close / bar.sma_50 > config.extension_limit_50d:
        return True
    return False

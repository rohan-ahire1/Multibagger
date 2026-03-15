"""Tests for the Minervini multi-phase exit engine."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sepa_scanner.backtest.exit_engine import (
    BarData,
    ExitAction,
    ExitConfig,
    TradeState,
    check_sell_into_strength,
    compute_trail_value,
    detect_climax_top,
    evaluate_exit,
    select_trail_mode,
    tighten_trail,
    _should_tighten,
)
from sepa_scanner.backtest.models import ExitPhase, ExitMode


# --- ExitPhase enum ---
def test_exit_phase_enum_values() -> None:
    assert ExitPhase.INITIAL_STOP.value == "initial_stop"
    assert ExitPhase.BREAKEVEN.value == "breakeven"
    assert ExitPhase.PARTIAL_PROFIT.value == "partial_profit"
    assert ExitPhase.TRAILING.value == "trailing"
    assert ExitPhase.TIGHTEN.value == "tighten"
    assert ExitPhase.EXIT.value == "exit"


# --- ExitMode.MINERVINI_PHASED ---
def test_exit_mode_minervini_phased_exists() -> None:
    assert hasattr(ExitMode, "MINERVINI_PHASED")
    assert ExitMode.MINERVINI_PHASED.value == "minervini_phased"


def test_exit_mode_labels_includes_minervini_phased() -> None:
    from sepa_scanner.backtest.models import EXIT_MODE_LABELS

    assert ExitMode.MINERVINI_PHASED in EXIT_MODE_LABELS
    assert EXIT_MODE_LABELS[ExitMode.MINERVINI_PHASED] == "Minervini Phased"


# --- ExitConfig defaults ---
def test_exit_config_defaults() -> None:
    c = ExitConfig()
    assert c.breakeven_activation_r == 1.0
    assert c.partial_profit_r == 2.0
    assert c.partial_profit_pct == 0.50
    assert c.trail_activation_r == 2.0
    assert c.max_loss_pct == 0.08
    assert c.absolute_max_loss_pct == 0.10
    assert c.trail_mode == "adaptive"
    assert c.trail_pct == 0.20
    assert c.trail_pct_bull == 0.25
    assert c.trail_pct_bear == 0.15
    assert c.extension_limit_200d == 2.0
    assert c.extension_limit_200d_extreme == 2.5
    assert c.extension_limit_50d == 1.25
    assert c.largest_day_gain_pct == 0.15
    assert c.gap_sell_threshold == 0.10
    assert c.time_stop_days == 15
    assert c.max_hold_before_tighten == 40
    assert c.climax_min_hold_days == 15
    assert c.climax_conditions_required == 3


# --- TradeState ---
def test_trade_state_creation_defaults() -> None:
    ts = TradeState(
        symbol="ABC",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    assert ts.phase == ExitPhase.INITIAL_STOP
    assert ts.current_shares == 100
    assert ts.active_stop == 95.0
    assert ts.highest_close == 100.0
    assert ts.breakeven_activated is False
    assert ts.partial_taken is False
    assert ts.trail_activated is False
    assert ts.tighten_activated is False


def test_trade_state_post_init_with_explicit_values() -> None:
    ts = TradeState(
        symbol="ABC",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=50,
        current_shares=50,
        active_stop=97.0,
        highest_close=105.0,
    )
    assert ts.current_shares == 50
    assert ts.active_stop == 97.0
    assert ts.highest_close == 105.0


# --- ExitAction ---
def test_exit_action_sell_all() -> None:
    a = ExitAction(action="SELL_ALL", reason="initial_stop")
    assert a.action == "SELL_ALL"
    assert a.reason == "initial_stop"
    assert a.shares == 0


def test_exit_action_sell_partial() -> None:
    a = ExitAction(action="SELL_PARTIAL", reason="partial_profit", shares=25)
    assert a.action == "SELL_PARTIAL"
    assert a.reason == "partial_profit"
    assert a.shares == 25


# --- select_trail_mode ---
def test_select_trail_mode_exceptional_leader_50ema() -> None:
    assert select_trail_mode(rs_rank=96, gain_pct=0.35, market_regime="bear") == "trail_50ema"


def test_select_trail_mode_strong_bull_50ema() -> None:
    assert select_trail_mode(rs_rank=92, gain_pct=0.22, market_regime="bull") == "trail_50ema"


def test_select_trail_mode_standard_bull_pct() -> None:
    assert select_trail_mode(rs_rank=80, gain_pct=0.15, market_regime="bull") == "trail_pct"


def test_select_trail_mode_defensive_21ema() -> None:
    assert select_trail_mode(rs_rank=70, gain_pct=0.10, market_regime="bear") == "trail_21ema"


def test_select_trail_mode_defensive_regime() -> None:
    assert select_trail_mode(rs_rank=85, gain_pct=0.25, market_regime="defensive") == "trail_21ema"


# --- tighten_trail ---
def test_tighten_trail_50ema_to_21ema() -> None:
    assert tighten_trail("trail_50ema") == "trail_21ema"


def test_tighten_trail_21ema_to_10d_low() -> None:
    assert tighten_trail("trail_21ema") == "trail_10d_low"


def test_tighten_trail_10d_low_stays() -> None:
    assert tighten_trail("trail_10d_low") == "trail_10d_low"


def test_tighten_trail_pct_stays() -> None:
    assert tighten_trail("trail_pct") == "trail_pct"


def test_tighten_trail_unknown_defaults_21ema() -> None:
    assert tighten_trail("unknown") == "trail_21ema"


# --- compute_trail_value ---
def test_compute_trail_value_pct_bull() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        highest_close=120.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade, trail_mode="trail_pct", market_regime="bull", config=config
    )
    assert v == 120.0 * (1.0 - 0.25)  # trail_pct_bull
    assert v == 90.0


def test_compute_trail_value_pct_bear() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        highest_close=110.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade, trail_mode="trail_pct", market_regime="defensive", config=config
    )
    assert v == 110.0 * (1.0 - 0.15)  # trail_pct_bear
    assert v == 93.5


def test_compute_trail_value_pct_default() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        highest_close=115.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade, trail_mode="trail_pct", market_regime="neutral", config=config
    )
    assert v == 115.0 * (1.0 - 0.20)  # trail_pct
    assert v == 92.0


def test_compute_trail_value_21ema() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        active_stop=98.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade,
        trail_mode="trail_21ema",
        market_regime="bull",
        config=config,
        ema21=99.5,
    )
    assert v == 99.5


def test_compute_trail_value_21ema_fallback() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        active_stop=97.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade, trail_mode="trail_21ema", market_regime="bull", config=config
    )
    assert v == 97.0


def test_compute_trail_value_50ema() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        active_stop=98.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade,
        trail_mode="trail_50ema",
        market_regime="bull",
        config=config,
        ema50=100.2,
    )
    assert v == 100.2


def test_compute_trail_value_10d_low() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
        active_stop=98.0,
    )
    config = ExitConfig()
    v = compute_trail_value(
        trade,
        trail_mode="trail_10d_low",
        market_regime="bull",
        config=config,
        low_10d=99.0,
    )
    assert v == 99.0


def test_compute_trail_value_unknown_raises() -> None:
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    config = ExitConfig()
    with pytest.raises(ValueError, match="Unknown trail mode"):
        compute_trail_value(
            trade, trail_mode="invalid", market_regime="bull", config=config
        )


# --- detect_climax_top ---
def test_detect_climax_too_early() -> None:
    config = ExitConfig()
    assert (
        detect_climax_top(
            days_held=5,
            daily_range=0.10,
            avg_range_20=0.03,
            vol_ratio=5.0,
            upper_wick_ratio=0.8,
            extension_50=1.5,
            gap_up_pct=0.05,
            config=config,
        )
        is False
    )


def test_detect_climax_all_conditions_met() -> None:
    config = ExitConfig()
    assert (
        detect_climax_top(
            days_held=20,
            daily_range=0.10,
            avg_range_20=0.03,  # 10 > 2*3 = 6 ✓
            vol_ratio=3.0,  # 3 > 2.5 ✓
            upper_wick_ratio=0.7,  # 0.7 > 0.6 ✓
            extension_50=1.25,  # 1.25 > 1.20 ✓
            gap_up_pct=0.05,  # 0.05 > 0.03 ✓
            config=config,
        )
        is True
    )


def test_detect_climax_only_two_conditions() -> None:
    config = ExitConfig()
    assert (
        detect_climax_top(
            days_held=20,
            daily_range=0.02,  # 2 < 2*3 ✗
            avg_range_20=0.03,
            vol_ratio=2.0,  # 2 < 2.5 ✗
            upper_wick_ratio=0.5,  # 0.5 < 0.6 ✗
            extension_50=1.10,  # 1.10 < 1.20 ✗
            gap_up_pct=0.01,  # 0.01 < 0.03 ✗
            config=config,
        )
        is False
    )


def test_detect_climax_exactly_three_conditions() -> None:
    config = ExitConfig()
    assert (
        detect_climax_top(
            days_held=20,
            daily_range=0.10,
            avg_range_20=0.03,
            vol_ratio=3.0,
            upper_wick_ratio=0.7,
            extension_50=1.10,  # fail
            gap_up_pct=0.01,  # fail
            config=config,
        )
        is True
    )


# --- check_sell_into_strength ---
def _bar_for_strength(
    close: float = 200.0,
    sma_200: float = 100.0,
    sma_50: float = 90.0,
    daily_return: float = 0.01,
    gap_up_pct: float = 0.02,
    vol_ratio: float = 1.5,
) -> dict[str, Any]:
    return {
        "close": close,
        "sma_200": sma_200,
        "sma_50": sma_50,
        "daily_return": daily_return,
        "gap_up_pct": gap_up_pct,
        "vol_ratio": vol_ratio,
    }


def test_check_sell_into_strength_200d_extreme() -> None:
    config = ExitConfig()
    r = check_sell_into_strength(
        close=260.0,
        sma_200=100.0,
        sma_50=90.0,
        daily_return=0.01,
        gap_up_pct=0.02,
        vol_ratio=1.5,
        current_shares=100,
        config=config,
    )
    assert r is not None
    assert r.action == "SELL_ALL"
    assert r.reason == "extension_200d_extreme"


def test_check_sell_into_strength_200d_moderate() -> None:
    config = ExitConfig()
    r = check_sell_into_strength(
        close=210.0,
        sma_200=100.0,
        sma_50=90.0,
        daily_return=0.01,
        gap_up_pct=0.02,
        vol_ratio=1.5,
        current_shares=100,
        config=config,
    )
    assert r is not None
    assert r.action == "SELL_PARTIAL"
    assert r.reason == "extension_200d"
    assert r.shares == 50


def test_check_sell_into_strength_largest_day_gain() -> None:
    config = ExitConfig()
    r = check_sell_into_strength(
        close=120.0,
        sma_200=100.0,
        sma_50=100.0,
        daily_return=0.18,
        gap_up_pct=0.01,
        vol_ratio=2.5,
        current_shares=100,
        config=config,
    )
    assert r is not None
    assert r.action == "SELL_PARTIAL"
    assert r.reason == "largest_day_gain"
    assert r.shares == 50


def test_check_sell_into_strength_gap_sell() -> None:
    config = ExitConfig()
    r = check_sell_into_strength(
        close=115.0,
        sma_200=100.0,
        sma_50=100.0,
        daily_return=0.02,
        gap_up_pct=0.12,
        vol_ratio=3.5,
        current_shares=100,
        config=config,
    )
    assert r is not None
    assert r.action == "SELL_PARTIAL"
    assert r.reason == "gap_sell"
    assert r.shares == 50


def test_check_sell_into_strength_no_trigger() -> None:
    config = ExitConfig()
    r = check_sell_into_strength(
        close=150.0,
        sma_200=100.0,
        sma_50=100.0,
        daily_return=0.05,
        gap_up_pct=0.02,
        vol_ratio=1.5,
        current_shares=100,
        config=config,
    )
    assert r is None


# --- evaluate_exit Phase 0 (INITIAL_STOP) ---
def _bar(
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 98.0,
    close: float = 103.0,
    volume: int = 1_000_000,
    sma_50: float = 95.0,
    sma_200: float = 90.0,
    ema_21: float = 100.0,
    ema_50: float = 98.0,
    low_10d: float = 97.0,
    prev_close: float = 100.0,
    avg_range_20: float = 0.02,
    avg_volume_50: float = 1_000_000,
) -> BarData:
    return BarData(
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        sma_50=sma_50,
        sma_200=sma_200,
        ema_21=ema_21,
        ema_50=ema_50,
        low_10d=low_10d,
        prev_close=prev_close,
        avg_range_20=avg_range_20,
        avg_volume_50=avg_volume_50,
    )


def test_evaluate_exit_initial_stop_hit() -> None:
    """Test 2: Quick failure - entry 500, stop 465, close 462 → exit."""
    config = ExitConfig()
    trade = TradeState(
        symbol="XYZ",
        entry_date=date(2026, 3, 1),
        entry_price=500.0,
        initial_risk=35.0,
        initial_stop=465.0,
        planned_shares=100,
    )
    bar = _bar(close=462.0, low=461.0, prev_close=500.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].action == "SELL_ALL"
    assert actions[0].reason == "initial_stop"


def test_evaluate_exit_time_stop() -> None:
    """Test 3: Stagnation - 15 days without +1R → exit."""
    config = ExitConfig()
    trade = TradeState(
        symbol="XYZ",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.days_held = 14
    trade.r_multiple = 0.5
    bar = _bar(close=102.5, prev_close=100.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].action == "SELL_ALL"
    assert actions[0].reason == "time_stop"


# --- evaluate_exit Phase progression ---
def test_evaluate_exit_progression_to_breakeven() -> None:
    """Bar hits breakeven trigger (high >= entry + 1R) but not partial profit trigger."""
    config = ExitConfig()
    trade = TradeState(
        symbol="ABC",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    bar = _bar(
        high=107.0,
        close=106.0,
        low=104.0,
        prev_close=100.0,
        sma_50=98.0,
        ema_21=102.0,
    )
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert trade.phase == ExitPhase.BREAKEVEN
    assert trade.breakeven_activated is True
    assert trade.active_stop == 100.0
    assert len(actions) == 0


def test_evaluate_exit_progression_to_partial_and_trailing() -> None:
    """Test 1: Normal winner progresses through phases to trailing."""
    config = ExitConfig()
    trade = TradeState(
        symbol="ABC",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.BREAKEVEN
    trade.breakeven_activated = True
    trade.active_stop = 100.0
    trade.days_held = 5
    bar = _bar(
        high=115.0, close=114.0, low=110.0, prev_close=108.0,
        ema_21=108.0, ema_50=105.0, low_10d=104.0,
    )
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert trade.phase == ExitPhase.TRAILING
    assert trade.partial_taken is True
    assert trade.trail_activated is True
    assert len(actions) == 1
    assert actions[0].action == "SELL_PARTIAL"
    assert actions[0].reason == "partial_profit"
    assert actions[0].shares == 50


# --- _should_tighten ---
def test_should_tighten_first_close_below_21ema() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.days_held = 25
    trade.tighten_activated = False
    bar = _bar(close=99.0, prev_close=101.0, ema_21=100.0)
    assert bar.close < bar.ema_21
    assert bar.prev_close >= bar.ema_21
    assert _should_tighten(trade, bar=bar, config=config) is True


def test_should_tighten_max_hold() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.days_held = 40
    trade.tighten_activated = False
    bar = _bar(close=110.0, prev_close=109.0, ema_21=108.0)
    assert _should_tighten(trade, bar=bar, config=config) is True


def test_should_tighten_extension_above_50d() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.days_held = 25
    trade.tighten_activated = False
    bar = _bar(close=130.0, sma_50=100.0, ema_21=120.0)
    assert bar.close / bar.sma_50 > config.extension_limit_50d
    assert _should_tighten(trade, bar=bar, config=config) is True


def test_should_tighten_already_activated_returns_false() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.days_held = 45
    trade.tighten_activated = True
    bar = _bar(close=110.0, sma_50=100.0)
    assert _should_tighten(trade, bar=bar, config=config) is False


def test_should_tighten_no_conditions() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="X",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.days_held = 10
    trade.tighten_activated = False
    bar = _bar(close=105.0, prev_close=104.0, ema_21=103.0, sma_50=100.0)
    assert _should_tighten(trade, bar=bar, config=config) is False


# --- trailing stop hit ---
def test_evaluate_exit_trailing_stop_hit() -> None:
    config = ExitConfig()
    trade = TradeState(
        symbol="ABC",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=50,
    )
    trade.phase = ExitPhase.TRAILING
    trade.trail_mode = "trail_21ema"
    trade.trail_activated = True
    trade.days_held = 20
    trade.highest_close = 120.0
    trade.active_stop = 105.0
    bar = _bar(close=104.0, low=103.0, ema_21=106.0, ema_50=108.0, low_10d=100.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].action == "SELL_ALL"
    assert "trail_" in actions[0].reason

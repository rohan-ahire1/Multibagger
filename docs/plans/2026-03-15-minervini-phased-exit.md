# Minervini Multi-Phase Exit Strategy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single-mechanism trailing stop with Minervini's 6-phase exit system supporting partial profits, adaptive trails, climax detection, and sell-into-strength rules.

**Architecture:** New `exit_engine.py` module owns all exit logic (phase transitions, trail computation, climax detection). The existing `engine.py` gets a new `_simulate_signal_phased()` path that delegates to `exit_engine.evaluate_exit()` per bar. Legacy exit modes remain available via `exit_engine: "legacy"`. The `BacktestTrade` model gains fields for partial exits, phase tracking, and exit reasons. Frontend updates add the new exit mode selector and phase/reason columns.

**Tech Stack:** Python 3.12+, Polars, dataclasses, FastAPI/Pydantic, React/TypeScript, Zustand, TanStack Table

---

### Task 1: Create ExitConfig and ExitPhase enums

**Files:**
- Modify: `src/sepa_scanner/backtest/models.py`

**Step 1: Write the failing test**

```python
# tests/test_exit_engine.py
from sepa_scanner.backtest.models import ExitMode, ExitPhase

def test_exit_phase_enum_values():
    assert ExitPhase.INITIAL_STOP == "initial_stop"
    assert ExitPhase.BREAKEVEN == "breakeven"
    assert ExitPhase.PARTIAL_PROFIT == "partial_profit"
    assert ExitPhase.TRAILING == "trailing"
    assert ExitPhase.TIGHTEN == "tighten"
    assert ExitPhase.EXIT == "exit"

def test_exit_mode_includes_phased():
    assert ExitMode.MINERVINI_PHASED.value == "minervini_phased"
```

**Step 2:** Run `uv run pytest tests/test_exit_engine.py::test_exit_phase_enum_values -v` — expect FAIL.

**Step 3: Add ExitPhase and update ExitMode in models.py**

In `src/sepa_scanner/backtest/models.py`, add after the `ExitMode` class:

```python
class ExitPhase(str, Enum):
    INITIAL_STOP = "initial_stop"
    BREAKEVEN = "breakeven"
    PARTIAL_PROFIT = "partial_profit"
    TRAILING = "trailing"
    TIGHTEN = "tighten"
    EXIT = "exit"
```

Add to `ExitMode`:
```python
MINERVINI_PHASED = "minervini_phased"
```

Update `EXIT_MODE_LABELS`:
```python
ExitMode.MINERVINI_PHASED: "Minervini Phased",
```

**Step 4:** Run `uv run pytest tests/test_exit_engine.py -v` — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): add ExitPhase enum and MINERVINI_PHASED exit mode"`

---

### Task 2: Create ExitConfig dataclass

**Files:**
- Create: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing test**

```python
# tests/test_exit_engine.py (append)
from sepa_scanner.backtest.exit_engine import ExitConfig

def test_exit_config_defaults():
    cfg = ExitConfig()
    assert cfg.breakeven_activation_r == 1.0
    assert cfg.partial_profit_r == 2.0
    assert cfg.partial_profit_pct == 0.50
    assert cfg.trail_activation_r == 2.0
    assert cfg.max_loss_pct == 0.08
    assert cfg.absolute_max_loss_pct == 0.10
    assert cfg.trail_mode == "adaptive"
    assert cfg.trail_pct == 0.20
    assert cfg.trail_pct_bull == 0.25
    assert cfg.trail_pct_bear == 0.15
    assert cfg.extension_limit_200d == 2.0
    assert cfg.extension_limit_200d_extreme == 2.5
    assert cfg.extension_limit_50d == 1.25
    assert cfg.largest_day_gain_pct == 0.15
    assert cfg.gap_sell_threshold == 0.10
    assert cfg.time_stop_days == 15
    assert cfg.max_hold_before_tighten == 40
    assert cfg.climax_min_hold_days == 15
    assert cfg.climax_conditions_required == 3
```

**Step 2:** Run test — expect FAIL (module doesn't exist).

**Step 3: Create `src/sepa_scanner/backtest/exit_engine.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sepa_scanner.backtest.models import ExitPhase


class TrailMode(str, Enum):
    ADAPTIVE = "adaptive"
    TRAIL_21EMA = "trail_21ema"
    TRAIL_50EMA = "trail_50ema"
    TRAIL_10D_LOW = "trail_10d_low"
    TRAIL_PCT = "trail_pct"


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
    max_hold_before_tighten: int = 40
    climax_min_hold_days: int = 15
    climax_conditions_required: int = 3
```

**Step 4:** Run test — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): add ExitConfig dataclass with Minervini defaults"`

---

### Task 3: Create TradeState and Action dataclasses

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing test**

```python
# tests/test_exit_engine.py (append)
from datetime import date
from sepa_scanner.backtest.exit_engine import TradeState, ExitAction

def test_trade_state_creation():
    ts = TradeState(
        symbol="TEST",
        entry_date=date(2024, 1, 15),
        entry_price=250.0,
        initial_risk=23.0,
        initial_stop=227.0,
        planned_shares=100,
    )
    assert ts.phase == ExitPhase.INITIAL_STOP
    assert ts.current_shares == 100
    assert ts.active_stop == 227.0
    assert ts.highest_close == 250.0
    assert ts.trail_mode == ""
    assert ts.breakeven_activated is False
    assert ts.partial_taken is False
    assert ts.days_held == 0
    assert ts.r_multiple == 0.0

def test_exit_action_sell_all():
    a = ExitAction(action="SELL_ALL", reason="initial_stop")
    assert a.action == "SELL_ALL"
    assert a.shares == 0

def test_exit_action_sell_partial():
    a = ExitAction(action="SELL_PARTIAL", reason="partial_profit", shares=50)
    assert a.shares == 50
```

**Step 2:** Run test — expect FAIL.

**Step 3: Add TradeState and ExitAction to exit_engine.py**

```python
@dataclass
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
```

Add `from datetime import date` to imports at top.

**Step 4:** Run test — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): add TradeState and ExitAction dataclasses"`

---

### Task 4: Implement trail computation functions

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing tests**

```python
# tests/test_exit_engine.py (append)
from sepa_scanner.backtest.exit_engine import (
    compute_trail_value, select_trail_mode, tighten_trail,
)

def test_select_trail_mode_exceptional_leader():
    mode = select_trail_mode(rs_rank=96, gain_pct=0.35, market_regime="bull")
    assert mode == "trail_50ema"

def test_select_trail_mode_strong_leader_bull():
    mode = select_trail_mode(rs_rank=91, gain_pct=0.25, market_regime="bull")
    assert mode == "trail_50ema"

def test_select_trail_mode_standard_bull():
    mode = select_trail_mode(rs_rank=85, gain_pct=0.10, market_regime="bull")
    assert mode == "trail_pct"

def test_select_trail_mode_defensive():
    mode = select_trail_mode(rs_rank=85, gain_pct=0.10, market_regime="defensive")
    assert mode == "trail_21ema"

def test_tighten_trail_50ema_to_21ema():
    assert tighten_trail("trail_50ema") == "trail_21ema"

def test_tighten_trail_21ema_to_10d():
    assert tighten_trail("trail_21ema") == "trail_10d_low"

def test_tighten_trail_10d_stays():
    assert tighten_trail("trail_10d_low") == "trail_10d_low"

def test_tighten_trail_pct_stays():
    assert tighten_trail("trail_pct") == "trail_pct"

def test_compute_trail_pct():
    cfg = ExitConfig(trail_pct=0.20, trail_pct_bull=0.25)
    ts = TradeState(
        symbol="X", entry_date=date(2024,1,1), entry_price=100.0,
        initial_risk=8.0, initial_stop=92.0, planned_shares=100,
    )
    ts.trail_mode = "trail_pct"
    ts.highest_close = 150.0
    val = compute_trail_value(ts, trail_mode="trail_pct", market_regime="bull", config=cfg)
    assert val == 150.0 * (1 - 0.25)  # 112.5

def test_compute_trail_pct_bear():
    cfg = ExitConfig(trail_pct_bear=0.15)
    ts = TradeState(
        symbol="X", entry_date=date(2024,1,1), entry_price=100.0,
        initial_risk=8.0, initial_stop=92.0, planned_shares=100,
    )
    ts.trail_mode = "trail_pct"
    ts.highest_close = 150.0
    val = compute_trail_value(ts, trail_mode="trail_pct", market_regime="defensive", config=cfg)
    assert val == 150.0 * (1 - 0.15)  # 127.5
```

**Step 2:** Run tests — expect FAIL.

**Step 3: Implement functions in exit_engine.py**

```python
def select_trail_mode(
    rs_rank: float, gain_pct: float, market_regime: str,
) -> str:
    if rs_rank >= 95 and gain_pct > 0.30:
        return "trail_50ema"
    if rs_rank >= 90 and market_regime == "bull" and gain_pct > 0.20:
        return "trail_50ema"
    if market_regime == "bull":
        return "trail_pct"
    return "trail_21ema"


def tighten_trail(current_mode: str) -> str:
    _TIGHTEN_MAP = {
        "trail_50ema": "trail_21ema",
        "trail_21ema": "trail_10d_low",
        "trail_10d_low": "trail_10d_low",
        "trail_pct": "trail_pct",
    }
    return _TIGHTEN_MAP.get(current_mode, "trail_21ema")


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
```

**Step 4:** Run tests — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): implement trail computation, selection, and tighten functions"`

---

### Task 5: Implement climax top detection

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing tests**

```python
# tests/test_exit_engine.py (append)
from sepa_scanner.backtest.exit_engine import detect_climax_top

def test_climax_top_too_early():
    assert detect_climax_top(
        days_held=10, daily_range=0.10, avg_range_20=0.03,
        vol_ratio=3.0, upper_wick_ratio=0.7, extension_50=1.25,
        gap_up_pct=0.05, config=ExitConfig(),
    ) is False

def test_climax_top_all_conditions():
    assert detect_climax_top(
        days_held=30, daily_range=0.10, avg_range_20=0.03,
        vol_ratio=3.0, upper_wick_ratio=0.7, extension_50=1.25,
        gap_up_pct=0.05, config=ExitConfig(),
    ) is True

def test_climax_top_only_two_conditions():
    assert detect_climax_top(
        days_held=30, daily_range=0.10, avg_range_20=0.03,
        vol_ratio=1.5, upper_wick_ratio=0.3, extension_50=1.10,
        gap_up_pct=0.01, config=ExitConfig(),
    ) is False
```

**Step 2:** Run tests — expect FAIL.

**Step 3: Implement detect_climax_top**

```python
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
```

**Step 4:** Run tests — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): implement climax top detection with configurable thresholds"`

---

### Task 6: Implement sell-into-strength checks

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing tests**

```python
# tests/test_exit_engine.py (append)
from sepa_scanner.backtest.exit_engine import check_sell_into_strength

def test_sell_strength_200d_moderate():
    action = check_sell_into_strength(
        close=500.0, sma_200=240.0, sma_50=400.0,
        daily_return=0.05, gap_up_pct=0.02,
        vol_ratio=1.5, current_shares=100, config=ExitConfig(),
    )
    assert action is not None
    assert action.reason == "extension_200d"
    assert action.shares == 50  # 50% of 100

def test_sell_strength_200d_extreme():
    action = check_sell_into_strength(
        close=625.0, sma_200=240.0, sma_50=400.0,
        daily_return=0.05, gap_up_pct=0.02,
        vol_ratio=1.5, current_shares=100, config=ExitConfig(),
    )
    assert action is not None
    assert action.reason == "extension_200d_extreme"
    assert action.action == "SELL_ALL"

def test_sell_strength_climax_day():
    action = check_sell_into_strength(
        close=200.0, sma_200=150.0, sma_50=180.0,
        daily_return=0.18, gap_up_pct=0.02,
        vol_ratio=2.5, current_shares=100, config=ExitConfig(),
    )
    assert action is not None
    assert action.reason == "largest_day_gain"
    assert action.shares == 50

def test_sell_strength_gap_up():
    action = check_sell_into_strength(
        close=200.0, sma_200=150.0, sma_50=180.0,
        daily_return=0.05, gap_up_pct=0.12,
        vol_ratio=3.5, current_shares=100, config=ExitConfig(),
    )
    assert action is not None
    assert action.reason == "gap_sell"
    assert action.shares == 50

def test_sell_strength_no_trigger():
    action = check_sell_into_strength(
        close=160.0, sma_200=150.0, sma_50=155.0,
        daily_return=0.02, gap_up_pct=0.01,
        vol_ratio=1.2, current_shares=100, config=ExitConfig(),
    )
    assert action is None
```

**Step 2:** Run tests — expect FAIL.

**Step 3: Implement check_sell_into_strength**

```python
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
```

**Step 4:** Run tests — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): implement sell-into-strength checks (200d extension, climax day, gap sell)"`

---

### Task 7: Implement the core evaluate_exit() function

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py`
- Test: `tests/test_exit_engine.py`

**Step 1: Write the failing tests (validation test cases from spec)**

```python
# tests/test_exit_engine.py (append)
from sepa_scanner.backtest.exit_engine import evaluate_exit, BarData

def test_phase0_initial_stop_hit():
    """Test 2 from spec: quick failure exits on initial stop."""
    cfg = ExitConfig()
    ts = TradeState(
        symbol="FAIL", entry_date=date(2024,1,10), entry_price=500.0,
        initial_risk=35.0, initial_stop=465.0, planned_shares=100,
    )
    bar = BarData(
        open=470.0, high=475.0, low=460.0, close=462.0, volume=100000,
        sma_50=480.0, sma_200=400.0, ema_21=490.0, ema_50=485.0,
        low_10d=460.0, prev_close=475.0, avg_range_20=0.02, avg_volume_50=80000,
    )
    # Day 1, 2 — no trigger. Day 3 — close below stop.
    for _ in range(2):
        actions = evaluate_exit(ts, bar=BarData(
            open=498.0, high=502.0, low=496.0, close=498.0, volume=50000,
            sma_50=480.0, sma_200=400.0, ema_21=490.0, ema_50=485.0,
            low_10d=496.0, prev_close=500.0, avg_range_20=0.02, avg_volume_50=80000,
        ), config=cfg, market_regime="bull")
        assert len(actions) == 0
        assert ts.phase == ExitPhase.INITIAL_STOP

    actions = evaluate_exit(ts, bar=bar, config=cfg, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].reason == "initial_stop"
    assert actions[0].action == "SELL_ALL"

def test_phase0_time_stop():
    """Test 3 from spec: stagnation time stop."""
    cfg = ExitConfig(time_stop_days=15)
    ts = TradeState(
        symbol="STAG", entry_date=date(2024,1,1), entry_price=200.0,
        initial_risk=16.0, initial_stop=184.0, planned_shares=100,
    )
    neutral_bar = BarData(
        open=202.0, high=208.0, low=198.0, close=204.0, volume=50000,
        sma_50=195.0, sma_200=180.0, ema_21=200.0, ema_50=196.0,
        low_10d=198.0, prev_close=202.0, avg_range_20=0.03, avg_volume_50=50000,
    )
    for _ in range(14):
        actions = evaluate_exit(ts, bar=neutral_bar, config=cfg, market_regime="bull")
        assert len(actions) == 0

    actions = evaluate_exit(ts, bar=neutral_bar, config=cfg, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].reason == "time_stop"

def test_phase_progression_to_trailing():
    """Test 1 from spec: normal winner progresses through phases."""
    cfg = ExitConfig(breakeven_activation_r=1.0, partial_profit_r=2.0)
    ts = TradeState(
        symbol="WIN", entry_date=date(2024,1,1), entry_price=100.0,
        initial_risk=8.0, initial_stop=92.0, planned_shares=100,
    )

    # Day 5: high hits 108 → BREAKEVEN
    actions = evaluate_exit(ts, bar=BarData(
        open=105.0, high=109.0, low=104.0, close=107.0, volume=80000,
        sma_50=95.0, sma_200=80.0, ema_21=102.0, ema_50=97.0,
        low_10d=100.0, prev_close=104.0, avg_range_20=0.03, avg_volume_50=50000,
    ), config=cfg, market_regime="bull")
    # Need multiple days to reach day 5
    for _ in range(4):
        evaluate_exit(ts, bar=BarData(
            open=105.0, high=109.0, low=104.0, close=107.0, volume=80000,
            sma_50=95.0, sma_200=80.0, ema_21=102.0, ema_50=97.0,
            low_10d=100.0, prev_close=104.0, avg_range_20=0.03, avg_volume_50=50000,
        ), config=cfg, market_regime="bull")
    assert ts.phase == ExitPhase.BREAKEVEN
    assert ts.active_stop >= 100.0  # moved to breakeven

    # Day 12: high hits 116 → PARTIAL_PROFIT → TRAILING
    for _ in range(6):
        evaluate_exit(ts, bar=BarData(
            open=112.0, high=117.0, low=111.0, close=115.0, volume=90000,
            sma_50=98.0, sma_200=82.0, ema_21=108.0, ema_50=100.0,
            low_10d=104.0, prev_close=113.0, avg_range_20=0.03, avg_volume_50=50000,
        ), config=cfg, market_regime="bull")
    assert ts.phase == ExitPhase.TRAILING
    assert ts.partial_taken is True
    assert ts.current_shares == 50
```

**Step 2:** Run tests — expect FAIL.

**Step 3: Implement evaluate_exit and BarData**

```python
@dataclass(slots=True)
class BarData:
    open: float
    high: float
    low: float
    close: float
    volume: int
    sma_50: float
    sma_200: float
    ema_21: float
    ema_50: float
    low_10d: float
    prev_close: float
    avg_range_20: float
    avg_volume_50: float


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

    # ── PHASE 0: INITIAL_STOP ──
    if trade.phase == ExitPhase.INITIAL_STOP:
        if trade.days_held >= config.time_stop_days and trade.r_multiple < 1.0:
            actions.append(ExitAction(action="SELL_ALL", reason="time_stop"))
            return actions
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason="initial_stop"))
            return actions
        if high >= trade.entry_price + config.breakeven_activation_r * trade.initial_risk:
            trade.phase = ExitPhase.BREAKEVEN
            trade.breakeven_activated = True
            trade.active_stop = max(trade.active_stop, trade.entry_price)

    # ── PHASE 1: BREAKEVEN ──
    if trade.phase == ExitPhase.BREAKEVEN:
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason="breakeven_stop"))
            return actions
        if high >= trade.entry_price + config.partial_profit_r * trade.initial_risk:
            trade.phase = ExitPhase.PARTIAL_PROFIT

    # ── PHASE 2: PARTIAL_PROFIT ──
    if trade.phase == ExitPhase.PARTIAL_PROFIT and not trade.partial_taken:
        sell_shares = int(trade.current_shares * config.partial_profit_pct)
        if sell_shares > 0:
            actions.append(ExitAction(
                action="SELL_PARTIAL", reason="partial_profit", shares=sell_shares,
            ))
            trade.partial_taken = True
            trade.current_shares -= sell_shares
            trade.partial_exit_price = close
            trade.partial_r = trade.r_multiple
        trade.phase = ExitPhase.TRAILING
        trade.trail_activated = True
        if config.trail_mode == "adaptive":
            gain_pct = (close - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
            trade.trail_mode = select_trail_mode(
                rs_rank=rs_rank, gain_pct=gain_pct, market_regime=market_regime,
            )
        else:
            trade.trail_mode = config.trail_mode

    # ── PHASE 3: TRAILING ──
    if trade.phase == ExitPhase.TRAILING:
        trail_value = compute_trail_value(
            trade, trail_mode=trade.trail_mode, market_regime=market_regime,
            config=config, ema21=bar.ema_21, ema50=bar.ema_50, low_10d=bar.low_10d,
        )
        trade.active_stop = max(trade.active_stop, trail_value)
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason=f"trail_{trade.trail_mode}"))
            return actions

        strength = check_sell_into_strength(
            close=close, sma_200=bar.sma_200, sma_50=bar.sma_50,
            daily_return=(close - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0,
            gap_up_pct=(bar.open - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0,
            vol_ratio=bar.volume / bar.avg_volume_50 if bar.avg_volume_50 > 0 else 0,
            current_shares=trade.current_shares, config=config,
        )
        if strength:
            actions.append(strength)
            if strength.action == "SELL_ALL":
                return actions
            trade.current_shares -= strength.shares

        daily_range = (bar.high - bar.low) / bar.low if bar.low > 0 else 0
        upper_wick = (bar.high - close) / (bar.high - bar.low) if (bar.high - bar.low) > 0 else 0
        ext_50 = close / bar.sma_50 if bar.sma_50 > 0 else 0
        gap = (bar.low - bar.prev_close) / bar.prev_close if bar.prev_close > 0 else 0
        if detect_climax_top(
            days_held=trade.days_held, daily_range=daily_range,
            avg_range_20=bar.avg_range_20, vol_ratio=bar.volume / bar.avg_volume_50 if bar.avg_volume_50 > 0 else 0,
            upper_wick_ratio=upper_wick, extension_50=ext_50,
            gap_up_pct=gap, config=config,
        ):
            sell = max(1, int(trade.current_shares * 0.50))
            if sell > 0 and trade.current_shares > 0:
                actions.append(ExitAction(action="SELL_PARTIAL", reason="climax_sell", shares=sell))
                trade.current_shares -= sell

        if _should_tighten(trade, bar=bar, config=config):
            trade.phase = ExitPhase.TIGHTEN
            trade.tighten_activated = True

    # ── PHASE 4: TIGHTEN ──
    if trade.phase == ExitPhase.TIGHTEN:
        trade.trail_mode = tighten_trail(trade.trail_mode)
        trail_value = compute_trail_value(
            trade, trail_mode=trade.trail_mode, market_regime=market_regime,
            config=config, ema21=bar.ema_21, ema50=bar.ema_50, low_10d=bar.low_10d,
        )
        trade.active_stop = max(trade.active_stop, trail_value)
        if close <= trade.active_stop:
            actions.append(ExitAction(action="SELL_ALL", reason=f"tighten_{trade.trail_mode}"))
            return actions

    return actions


def _should_tighten(
    trade: TradeState, *, bar: BarData, config: ExitConfig,
) -> bool:
    if trade.tighten_activated:
        return False
    if bar.close < bar.ema_21 and bar.prev_close >= bar.ema_21 and trade.days_held > 20:
        return True
    if trade.days_held >= config.max_hold_before_tighten:
        return True
    if bar.sma_50 > 0 and bar.close / bar.sma_50 > config.extension_limit_50d:
        return True
    return False
```

**Step 4:** Run `uv run pytest tests/test_exit_engine.py -v` — expect ALL PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): implement evaluate_exit() with full phase state machine"`

---

### Task 8: Update BacktestTrade model for phased exits

**Files:**
- Modify: `src/sepa_scanner/backtest/models.py`
- Modify: `tests/test_backtest_metrics.py`

**Step 1: Add new fields to BacktestTrade**

Add these optional fields to the `BacktestTrade` dataclass in `models.py`:

```python
@dataclass(slots=True)
class BacktestTrade:
    # ... existing fields ...
    exit_phase: str | None = None          # ExitPhase value when trade closed
    exit_reason: str | None = None         # Detailed reason string
    partial_exit_price: float | None = None
    partial_exit_date: date | None = None
    partial_exit_r: float | None = None
    partial_exit_pct: float | None = None  # fraction sold (e.g. 0.50)
    trail_mode_used: str | None = None     # which trail mechanism was active
```

**Step 2: Update BacktestSummary with new counters**

Add these fields to `BacktestSummary`:

```python
    breakeven_exits: int = 0
    partial_profit_exits: int = 0
    trail_exits: int = 0
    tighten_exits: int = 0
    climax_sells: int = 0
    extension_sells: int = 0
    avg_partial_r: float | None = None
    pct_reaching_breakeven: float = 0.0
    pct_reaching_partial: float = 0.0
    pct_reaching_trail: float = 0.0
    exit_engine: str = "legacy"
```

**Step 3:** Run `uv run pytest tests/test_backtest_metrics.py -v` — expect PASS (new fields have defaults, existing tests unaffected).

**Step 4: Commit** — `git add -A && git commit -m "feat(exit): extend BacktestTrade and BacktestSummary with phased exit fields"`

---

### Task 9: Integrate phased exit into backtest engine

**Files:**
- Modify: `src/sepa_scanner/backtest/engine.py`

**Step 1: Write the failing test**

```python
# tests/test_exit_engine.py (append)
import polars as pl
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode

def test_engine_phased_exit_basic():
    """Phased exit mode produces trades with phase/reason metadata."""
    signals = pl.DataFrame({
        "signal_date": [date(2024, 1, 10)],
        "symbol": ["TEST"],
        "signal_type": ["BREAKOUT"],
        "pivot": [100.0],
        "entry_ref": [101.0],
        "stop": [92.0],
        "r_per_share": [9.0],
        "rs_rank": [85.0],
    })
    # 60 bars of gradually rising prices
    dates = [date(2024, 1, 10 + i) for i in range(1, 61)]
    # Simple uptrend: price rises from 100 to 160 over 60 days
    ohlcv = pl.DataFrame({
        "date": dates,
        "symbol": ["TEST"] * 60,
        "open": [100.0 + i for i in range(60)],
        "high": [102.0 + i for i in range(60)],
        "low": [99.0 + i for i in range(60)],
        "close": [101.0 + i for i in range(60)],
        "volume": [100000] * 60,
    })
    params = BacktestParams(
        exit_mode=ExitMode.MINERVINI_PHASED,
        max_hold_days=90,
    )
    result = simulate_breakout_backtest(signals, ohlcv, params)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price is not None
    assert trade.exit_reason is not None
    assert trade.partial_exit_price is not None  # should have taken partial
```

**Step 2:** Run test — expect FAIL.

**Step 3: Add `_simulate_signal_phased()` to engine.py**

Add a new function that creates a `TradeState`, iterates bars calling `evaluate_exit()`, and converts back to `BacktestTrade`. The function computes the required `BarData` indicators (EMA21, EMA50, SMA50, SMA200, 10-day low, avg range, avg volume) from raw OHLCV bars.

Key implementation details:
- Compute EMA21, EMA50 incrementally: `ema = alpha * close + (1 - alpha) * ema`
- SMA50 and SMA200: rolling mean over a buffer of closes
- 10-day low: `min(low_buffer[-10:])`
- `avg_range_20`: rolling average of `(high-low)/low` over 20 bars
- `avg_volume_50`: rolling average of volume over 50 bars
- When the exit mode is `MINERVINI_PHASED`, call `_simulate_signal_phased` instead of `_simulate_signal`
- The `market_regime` defaults to `"bull"` for now (to be replaced with actual regime detection later)
- `rs_rank` is read from the signal dict (key `"rs_rank"`, default 80.0)

In `_simulate_signal` dispatch:
```python
if params.exit_mode == ExitMode.MINERVINI_PHASED:
    return _simulate_signal_phased(signal, symbol_prices, params)
```

The full `_simulate_signal_phased` function handles:
1. Entry fill logic (same as existing `_simulate_signal`)
2. Creating `TradeState` from the filled entry
3. Pre-computing a rolling window of indicator values from all available bars before entry
4. Daily loop calling `evaluate_exit()` per bar
5. Processing `ExitAction` list to build the final `BacktestTrade`
6. Handling `SELL_PARTIAL` by recording partial exit fields
7. Handling `SELL_ALL` by closing the trade
8. Computing weighted R-multiple when partials exist: `(partial_pct * partial_r + remaining_pct * final_r)`

**Step 4:** Run full test suite — `uv run pytest tests/test_exit_engine.py tests/test_backtest_metrics.py -v` — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): integrate phased exit engine into backtest simulator"`

---

### Task 10: Update metrics for phased exits

**Files:**
- Modify: `src/sepa_scanner/backtest/metrics.py`
- Test: `tests/test_backtest_metrics.py`

**Step 1: Update `summarize_backtest` to populate new summary fields**

Count trades by `exit_reason` prefix to populate `breakeven_exits`, `trail_exits`, `tighten_exits`, `climax_sells`, `extension_sells`. Compute `pct_reaching_breakeven`, `pct_reaching_partial`, `pct_reaching_trail` from the `exit_phase` field. Compute `avg_partial_r` from trades that have `partial_exit_r` set.

**Step 2: Write test for new summary fields**

```python
# tests/test_backtest_metrics.py (append)
def test_phased_exit_summary_fields():
    trades = [
        BacktestTrade(
            symbol="A", signal_date=date(2026,3,1), status="stopped",
            entry_ref=101.0, stop=92.0, pivot=100.0,
            entry_date=date(2026,3,2), entry_price=101.0,
            exit_date=date(2026,3,20), exit_price=120.0,
            holding_days=18, return_pct=18.81, r_multiple=2.11,
            exit_phase="trailing", exit_reason="trail_trail_21ema",
            partial_exit_price=117.0, partial_exit_r=1.78, partial_exit_pct=0.50,
            trail_mode_used="trail_21ema",
        ),
    ]
    summary = summarize_backtest(trades)
    assert summary.trail_exits == 1
    assert summary.avg_partial_r == 1.78
    assert summary.pct_reaching_trail > 0
```

**Step 3:** Run tests — expect PASS.

**Step 4: Commit** — `git add -A && git commit -m "feat(exit): update backtest metrics to report phased exit statistics"`

---

### Task 11: Update config.yaml with exit_strategy section

**Files:**
- Modify: `config/config.yaml`

**Step 1: Add exit_strategy section**

```yaml
exit_strategy:
  breakeven_activation_r: 1.0
  partial_profit_r: 2.0
  partial_profit_pct: 0.50
  trail_activation_r: 2.0
  max_loss_pct: 0.08
  absolute_max_loss_pct: 0.10
  trail_mode: "adaptive"
  trail_pct: 0.20
  trail_pct_bull: 0.25
  trail_pct_bear: 0.15
  extension_limit_200d: 2.0
  extension_limit_200d_extreme: 2.5
  extension_limit_50d: 1.25
  largest_day_gain_pct: 0.15
  gap_sell_threshold: 0.10
  time_stop_days: 15
  max_hold_before_tighten: 40
  climax_min_hold_days: 15
  climax_conditions_required: 3
```

**Step 2: Commit** — `git add -A && git commit -m "feat(exit): add exit_strategy config section with Minervini defaults"`

---

### Task 12: Update API routes for phased exit

**Files:**
- Modify: `src/sepa_scanner/api/routes/backtest.py`

**Step 1: Update BacktestRunRequest**

Add `exit_engine` field (default `"legacy"`):
```python
class BacktestRunRequest(BaseModel):
    # ... existing fields ...
    exit_engine: str = "legacy"  # "legacy" | "minervini_phased"
```

When `exit_engine == "minervini_phased"`, set `exit_mode=ExitMode.MINERVINI_PHASED`.

**Step 2: Update _serialize_trade to include new fields**

Add `exit_phase`, `exit_reason`, `partial_exit_price`, `partial_exit_date`, `partial_exit_r`, `partial_exit_pct`, `trail_mode_used` to the serialized output.

**Step 3: Update response params dict**

Include `exit_engine` in the params dict.

**Step 4:** Run `uv run pytest tests/ -v --ignore=tests/test_ui_*.py` — expect PASS.

**Step 5: Commit** — `git add -A && git commit -m "feat(exit): update backtest API routes for phased exit engine"`

---

### Task 13: Update frontend types

**Files:**
- Modify: `sepa-terminal/src/lib/types.ts`

**Step 1: Update BacktestParams type**

```typescript
export interface BacktestParams {
  startDate: string
  endDate: string
  exitMode: 'v1_fixed' | 'v2_trail_10d' | 'v2_trail_21ema' | 'v3_trail_pct' | 'minervini_phased'
  targetR: number | null
  pendingDays: number
  maxHoldDays: number
  trailActivationR: number
  exitEngine: 'legacy' | 'minervini_phased'
}
```

**Step 2: Update BacktestTrade type**

```typescript
export interface BacktestTrade {
  // ... existing fields ...
  exitPhase: string | null
  exitReason: string | null
  partialExitPrice: number | null
  partialExitDate: string | null
  partialExitR: number | null
  partialExitPct: number | null
  trailModeUsed: string | null
}
```

**Step 3: Update BacktestSummary type**

```typescript
export interface BacktestSummary {
  // ... existing fields ...
  breakevenExits: number
  partialProfitExits: number
  trailExits: number
  tightenExits: number
  climaxSells: number
  extensionSells: number
  avgPartialR: number | null
  pctReachingBreakeven: number
  pctReachingPartial: number
  pctReachingTrail: number
  exitEngine: string
}
```

**Step 4: Commit** — `git add -A && git commit -m "feat(exit): update frontend types for phased exit data"`

---

### Task 14: Update frontend backtest store and API hook

**Files:**
- Modify: `sepa-terminal/src/stores/backtest.ts`
- Modify: `sepa-terminal/src/api/useBacktest.ts`

**Step 1: Update default params**

```typescript
function defaultParams(): BacktestParams {
  const now = new Date()
  return {
    startDate: DATA_START_DATE,
    endDate: now.toISOString().slice(0, 10),
    exitMode: 'minervini_phased',
    targetR: null,
    pendingDays: 5,
    maxHoldDays: 90,
    trailActivationR: 2.0,
    exitEngine: 'minervini_phased',
  }
}
```

**Step 2: Update API hook to send new fields**

In `useBacktest.ts`, add `exit_engine` to the body:
```typescript
const body = {
  // ... existing fields ...
  exit_engine: params.exitEngine,
}
```

**Step 3: Commit** — `git add -A && git commit -m "feat(exit): update frontend store and API hook for phased exit"`

---

### Task 15: Update ParamBar component

**Files:**
- Modify: `sepa-terminal/src/components/backtest/ParamBar.tsx`

**Step 1: Add "Minervini Phased" to exit mode dropdown**

Add option:
```tsx
<option value="minervini_phased">Minervini Phased</option>
```

**Step 2: Commit** — `git add -A && git commit -m "feat(exit): add Minervini Phased option to backtest ParamBar"`

---

### Task 16: Update TradeList component

**Files:**
- Modify: `sepa-terminal/src/components/backtest/TradeList.tsx`

**Step 1: Add exit phase and reason columns**

Add new status values to `statusPillProps`:
```typescript
case 'dead_money':
  return { label: 'Dead Money', variant: 'amber' }
```

Add new columns after the existing `status` column:
```typescript
columnHelper.accessor('exitPhase', {
    header: 'Phase',
    size: 70,
    cell: (info) => {
      const val = info.getValue()
      if (!val) return <span className="text-zinc-500">—</span>
      const labels: Record<string, string> = {
        initial_stop: 'INIT', breakeven: 'BE', partial_profit: 'PARTIAL',
        trailing: 'TRAIL', tighten: 'TIGHT', exit: 'EXIT',
      }
      return <span className="text-[11px] font-mono text-zinc-400">{labels[val] ?? val}</span>
    },
  }),
  columnHelper.accessor('exitReason', {
    header: 'Reason',
    size: 90,
    cell: (info) => {
      const val = info.getValue()
      if (!val) return <span className="text-zinc-500">—</span>
      return <span className="text-[11px] font-mono text-zinc-400">{val}</span>
    },
  }),
```

Add `holdingDays` column:
```typescript
columnHelper.accessor('holdingDays', {
    header: 'Days',
    size: 45,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return <span className="tabular-nums text-zinc-400">{val ?? '—'}</span>
    },
  }),
```

**Step 2: Commit** — `git add -A && git commit -m "feat(exit): add phase, reason, and days columns to TradeList"`

---

### Task 17: Update StatsSummary component

**Files:**
- Modify: `sepa-terminal/src/components/backtest/StatsSummary.tsx`

**Step 1: Add phased exit metrics**

Add new metrics to the metrics array when `exitEngine === 'minervini_phased'`:
```typescript
if (summary.exitEngine === 'minervini_phased') {
    metrics.push(
      { label: 'BE%', value: formatPct(summary.pctReachingBreakeven * 100) },
      { label: 'Partial%', value: formatPct(summary.pctReachingPartial * 100) },
      { label: 'Trail%', value: formatPct(summary.pctReachingTrail * 100) },
      { label: 'Avg Partial R', value: summary.avgPartialR != null ? formatR(summary.avgPartialR) : '—', color: '#22c55e' },
      { label: 'Climax Sells', value: String(summary.climaxSells) },
    )
  }
```

**Step 2: Commit** — `git add -A && git commit -m "feat(exit): add phased exit metrics to StatsSummary"`

---

### Task 18: Update STRATEGY.md

**Files:**
- Modify: `docs/STRATEGY.md`

**Step 1: Update the backtest section (Section 15) with phased exit documentation**

Add a new subsection documenting:
- The 6 phases and their transitions
- Trail modes (adaptive, 21ema, 50ema, 10d_low, pct)
- Sell-into-strength rules
- Climax detection
- Config reference pointing to `exit_strategy` section in `config.yaml`
- Legacy mode preserved for A/B comparison

**Step 2: Add change log entry**

```markdown
| 2026-03-15 | Added Minervini multi-phase exit engine | Replace single trailing stop with 6-phase system: initial→breakeven→partial→trail→tighten→exit. Adds partial profits, adaptive trail selection, climax detection, sell-into-strength rules. Legacy modes preserved for A/B comparison. | AI-assisted |
```

**Step 3: Commit** — `git add -A && git commit -m "docs: update STRATEGY.md with Minervini phased exit documentation"`

---

### Task 19: Run full test suite and verify

**Step 1:** Run `uv run pytest tests/ -v --ignore=tests/test_ui_*.py`

**Step 2:** Fix any failures.

**Step 3:** Run the backtest API server and verify the new exit mode works end-to-end.

**Step 4: Final commit** — `git add -A && git commit -m "chore: fix any remaining issues from phased exit integration"`

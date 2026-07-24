# Exit Engine Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline exit logic in `simulation.py` with calls to the existing phased `exit_engine.py`, and tune exit parameters to allow winners to run beyond 3R.

**Architecture:** Enrich `build_symbol_data` with technical indicators needed by `BarData`. Replace the ~90-line inline exit block in `simulate_portfolio_python` with a helper that builds `TradeState`/`BarData` from position state and calls `evaluate_exit()`. Tune `ExitConfig` defaults and add R-dependent trail widening in the exit engine.

**Tech Stack:** Python, Polars, pytest

**Spec:** `docs/superpowers/specs/2026-03-29-exit-engine-integration-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/sepa_scanner/backtest/exit_engine.py` | Modify | Tune time stop threshold, add 3R+ trail widening, update defaults |
| `src/sepa_scanner/backtest/simulation.py` | Modify | Enrich symbol data with indicators, replace inline exits with `evaluate_exit()` calls |
| `tests/test_exit_engine.py` | Modify | Add tests for new time stop threshold, 3R+ trail widening |
| `tests/test_simulation_exit_integration.py` | Create | Integration tests for simulation using phased exit engine |

---

### Task 1: Tune time stop to only kill weak trades (exit_engine.py)

Currently the time stop fires when `r_multiple < 1.0` after 15 days. This kills trades showing strength (e.g., at +0.8R on day 14). Change threshold to `r_multiple < 0.3` so only truly stagnant trades get stopped.

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py:12-19` (ExitConfig) and `src/sepa_scanner/backtest/exit_engine.py:213-215` (time stop logic)
- Test: `tests/test_exit_engine.py`

- [ ] **Step 1: Write failing test for new time stop threshold**

In `tests/test_exit_engine.py`, add these tests after the existing `test_evaluate_exit_time_stop`:

```python
def test_time_stop_spares_trade_at_half_r() -> None:
    """A trade at 0.5R on day 15 should NOT be time-stopped."""
    config = ExitConfig()
    trade = TradeState(
        symbol="XYZ",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.days_held = 14  # will be incremented to 15 inside evaluate_exit
    trade.r_multiple = 0.5
    bar = _bar(close=102.5, high=103.0, prev_close=102.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    # Should NOT fire time_stop — trade is showing strength at 0.5R
    sell_all_reasons = [a.reason for a in actions if a.action == "SELL_ALL"]
    assert "time_stop" not in sell_all_reasons


def test_time_stop_kills_stagnant_trade() -> None:
    """A trade at 0.1R on day 15 should be time-stopped."""
    config = ExitConfig()
    trade = TradeState(
        symbol="XYZ",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.days_held = 14  # will be incremented to 15
    trade.r_multiple = 0.1
    bar = _bar(close=100.5, high=101.0, low=100.0, prev_close=100.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].action == "SELL_ALL"
    assert actions[0].reason == "time_stop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_exit_engine.py::test_time_stop_spares_trade_at_half_r tests/test_exit_engine.py::test_time_stop_kills_stagnant_trade -v`

Expected: `test_time_stop_spares_trade_at_half_r` FAILS (currently time stop fires at < 1.0R, so 0.5R gets stopped). `test_time_stop_kills_stagnant_trade` PASSES (0.1R < 1.0R).

- [ ] **Step 3: Add `time_stop_min_r` to ExitConfig and update time stop logic**

In `src/sepa_scanner/backtest/exit_engine.py`, add field to `ExitConfig`:

```python
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
```

Update the time stop check in `evaluate_exit()` (around line 214):

```python
        if trade.days_held >= config.time_stop_days and trade.r_multiple < config.time_stop_min_r:
            actions.append(ExitAction(action="SELL_ALL", reason="time_stop"))
            return actions
```

- [ ] **Step 4: Update existing time stop test**

The existing `test_evaluate_exit_time_stop` sets `r_multiple = 0.5` and expects a time stop. Update it to use `r_multiple = 0.1` (below the new 0.3 threshold):

In `tests/test_exit_engine.py`, find and update `test_evaluate_exit_time_stop`:

```python
def test_evaluate_exit_time_stop() -> None:
    """Test 3: Stagnation - 15 days without meaningful gain → exit."""
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
    trade.r_multiple = 0.1  # Below time_stop_min_r of 0.3
    bar = _bar(close=100.5, high=101.0, low=100.0, prev_close=100.0)
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert len(actions) == 1
    assert actions[0].action == "SELL_ALL"
    assert actions[0].reason == "time_stop"
```

- [ ] **Step 5: Run all exit engine tests**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_exit_engine.py -v`

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_exit_engine.py src/sepa_scanner/backtest/exit_engine.py
git commit -m "feat(exit): tune time stop to spare trades showing strength (>0.3R)"
```

---

### Task 2: Add R-dependent trail widening for big winners (exit_engine.py)

When a trade reaches 3R+, widen the trail to 50 EMA minimum. This prevents normal consolidation pullbacks from stopping out superperformers.

**Files:**
- Modify: `src/sepa_scanner/backtest/exit_engine.py:256-272` (TRAILING phase)
- Test: `tests/test_exit_engine.py`

- [ ] **Step 1: Write failing test for 3R+ trail widening**

In `tests/test_exit_engine.py`, add:

```python
def test_trail_widens_to_50ema_at_3r() -> None:
    """When a trade reaches 3R+, trail mode should widen to trail_50ema."""
    config = ExitConfig()
    trade = TradeState(
        symbol="SUPER",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=100,
    )
    trade.phase = ExitPhase.TRAILING
    trade.trail_mode = "trail_21ema"
    trade.trail_activated = True
    trade.partial_taken = True
    trade.current_shares = 50
    trade.days_held = 20
    trade.highest_close = 115.0
    trade.active_stop = 108.0
    # Bar where trade hits 3R+ (entry 100 + 3*5 = 115)
    bar = _bar(
        close=116.0, high=117.0, low=114.0,
        prev_close=115.0,
        ema_21=112.0, ema_50=108.0, low_10d=110.0,
        sma_50=107.0,
    )
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    # Trail should have widened to 50ema
    assert trade.trail_mode == "trail_50ema"
    # Should NOT be stopped (close 116 > ema_50 108)
    sell_all = [a for a in actions if a.action == "SELL_ALL"]
    assert len(sell_all) == 0


def test_trail_stays_wide_after_3r_pullback() -> None:
    """After widening at 3R, a pullback to 2.5R should not tighten the trail back."""
    config = ExitConfig()
    trade = TradeState(
        symbol="SUPER",
        entry_date=date(2026, 3, 1),
        entry_price=100.0,
        initial_risk=5.0,
        initial_stop=95.0,
        planned_shares=50,
    )
    trade.phase = ExitPhase.TRAILING
    trade.trail_mode = "trail_50ema"  # Already widened
    trade.trail_activated = True
    trade.partial_taken = True
    trade.current_shares = 50
    trade.days_held = 25
    trade.highest_close = 118.0
    trade.active_stop = 108.0
    trade.r_multiple = 2.5  # Pulled back from 3R+
    # Close is above 50ema, so should hold
    bar = _bar(
        close=112.5, high=113.0, low=111.0,
        prev_close=114.0,
        ema_21=111.0, ema_50=108.5, low_10d=110.0,
        sma_50=107.0,
    )
    actions = evaluate_exit(trade, bar=bar, config=config, market_regime="bull")
    assert trade.trail_mode == "trail_50ema"
    sell_all = [a for a in actions if a.action == "SELL_ALL"]
    assert len(sell_all) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_exit_engine.py::test_trail_widens_to_50ema_at_3r tests/test_exit_engine.py::test_trail_stays_wide_after_3r_pullback -v`

Expected: `test_trail_widens_to_50ema_at_3r` FAILS (trail mode stays at trail_21ema).

- [ ] **Step 3: Add trail widening logic to TRAILING phase**

In `src/sepa_scanner/backtest/exit_engine.py`, add a new config field and update the TRAILING phase. Add to `ExitConfig`:

```python
    trail_widen_r: float = 3.0
```

In `evaluate_exit()`, at the start of the TRAILING phase block (after `if trade.phase == ExitPhase.TRAILING:`), add the widening logic before the trail value computation:

```python
    # Phase 3: TRAILING
    if trade.phase == ExitPhase.TRAILING:
        # Widen trail for big winners — once a trade reaches trail_widen_r,
        # switch to 50 EMA trail to give it room to consolidate
        if trade.r_multiple >= config.trail_widen_r and trade.trail_mode != "trail_50ema":
            trade.trail_mode = "trail_50ema"

        trail_value = compute_trail_value(
```

- [ ] **Step 4: Run all exit engine tests**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_exit_engine.py -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_exit_engine.py src/sepa_scanner/backtest/exit_engine.py
git commit -m "feat(exit): widen trail to 50 EMA when trade reaches 3R+"
```

---

### Task 3: Enrich `build_symbol_data` with technical indicators

The `BarData` struct requires `sma_50`, `sma_200`, `ema_21`, `ema_50`, `low_10d`, `prev_close`, `avg_range_20`, `avg_volume_50`, and `volume`. Currently `build_symbol_data` only stores opens/highs/lows/closes and partial ema_21/sma_50. Compute all indicators from raw OHLCV.

**Files:**
- Modify: `src/sepa_scanner/backtest/simulation.py:137-153` (`build_symbol_data`)
- Test: `tests/test_simulation_exit_integration.py` (create)

- [ ] **Step 1: Write failing test for enriched symbol data**

Create `tests/test_simulation_exit_integration.py`:

```python
"""Integration tests for simulation using the phased exit engine."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from sepa_scanner.backtest.simulation import build_symbol_data


def _make_ohlcv(n_days: int = 250, start: date = date(2024, 1, 1)) -> pl.DataFrame:
    """Generate synthetic OHLCV data for one symbol with an uptrend."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    base = 100.0
    rows = []
    for i, d in enumerate(dates):
        c = base + i * 0.5  # steady uptrend
        rows.append({
            "date": d,
            "symbol": "TEST",
            "open": c - 0.5,
            "high": c + 1.0,
            "low": c - 1.5,
            "close": c,
            "volume": 1_000_000 + i * 1000,
        })
    return pl.DataFrame(rows).cast({"date": pl.Date, "volume": pl.Int64})


def test_build_symbol_data_has_all_indicator_keys():
    ohlcv = _make_ohlcv(250)
    symbol_data, unique_dates = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]

    required_keys = [
        "dates", "opens", "highs", "lows", "closes", "volumes",
        "date_to_idx", "sma_50", "sma_200", "ema_21", "ema_50",
        "low_10d", "prev_close", "avg_range_20", "avg_volume_50",
    ]
    for key in required_keys:
        assert key in sd, f"Missing key: {key}"
    assert len(sd["sma_50"]) == len(sd["closes"])
    assert len(sd["ema_21"]) == len(sd["closes"])


def test_build_symbol_data_sma_50_correct_at_index_60():
    """SMA-50 at index 60 should be the mean of closes[11:61]."""
    ohlcv = _make_ohlcv(250)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # Manual calculation: closes are 100.0, 100.5, 101.0, ... so close[i] = 100 + i*0.5
    # sma_50 at index 60 = mean of closes[11..60] = mean(105.5 .. 130.0)
    expected = sum(100.0 + i * 0.5 for i in range(11, 61)) / 50
    assert abs(sd["sma_50"][60] - expected) < 0.01


def test_build_symbol_data_prev_close():
    ohlcv = _make_ohlcv(100)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # prev_close[0] should be 0 (no previous), prev_close[1] = closes[0]
    assert sd["prev_close"][0] == 0.0
    assert sd["prev_close"][1] == sd["closes"][0]


def test_build_symbol_data_low_10d():
    ohlcv = _make_ohlcv(100)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # At index 15, low_10d should be the min of lows[6:16]
    expected = min(sd["lows"][6:16])
    assert abs(sd["low_10d"][15] - expected) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_simulation_exit_integration.py -v`

Expected: FAIL — `volumes` key missing, `sma_200` missing, etc.

- [ ] **Step 3: Rewrite `build_symbol_data` to compute all indicators**

In `src/sepa_scanner/backtest/simulation.py`, replace the `build_symbol_data` function:

```python
def build_symbol_data(ohlcv: pl.DataFrame) -> tuple[dict[str, dict[str, Any]], list[date]]:
    symbol_data = {}
    unique_dates = ohlcv["date"].unique().sort().to_list()

    for symbol, group in ohlcv.group_by("symbol"):
        group_sorted = group.sort("date")
        closes = group_sorted["close"].to_list()
        highs = group_sorted["high"].to_list()
        lows = group_sorted["low"].to_list()
        opens = group_sorted["open"].to_list()
        volumes = group_sorted["volume"].to_list()
        n = len(closes)

        # SMA
        sma_50 = _rolling_mean(closes, 50)
        sma_200 = _rolling_mean(closes, 200)

        # EMA
        ema_21 = _ema(closes, 21)
        ema_50 = _ema(closes, 50)

        # Rolling 10-day low
        low_10d = _rolling_min(lows, 10)

        # Previous close (shift by 1, fill first with 0)
        prev_close = [0.0] + closes[:-1]

        # Average range (high - low) over 20 periods
        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range_20 = _rolling_mean(ranges, 20)

        # Average volume over 50 periods
        vol_floats = [float(v) for v in volumes]
        avg_volume_50 = _rolling_mean(vol_floats, 50)

        symbol_data[str(symbol)] = {
            "dates": group_sorted["date"].to_list(),
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes,
            "date_to_idx": {d: i for i, d in enumerate(group_sorted["date"].to_list())},
            "sma_50": sma_50,
            "sma_200": sma_200,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "low_10d": low_10d,
            "prev_close": prev_close,
            "avg_range_20": avg_range_20,
            "avg_volume_50": avg_volume_50,
        }
    return symbol_data, unique_dates


def _rolling_mean(values: list[float], window: int) -> list[float]:
    """Compute rolling mean. Returns 0.0 for indices before the window is full."""
    n = len(values)
    result = [0.0] * n
    running_sum = 0.0
    for i in range(n):
        running_sum += values[i]
        if i >= window:
            running_sum -= values[i - window]
        if i >= window - 1:
            result[i] = running_sum / window
    return result


def _rolling_min(values: list[float], window: int) -> list[float]:
    """Compute rolling min. Returns the value itself for indices before the window is full."""
    from collections import deque
    n = len(values)
    result = [0.0] * n
    dq: deque[int] = deque()
    for i in range(n):
        while dq and dq[0] <= i - window:
            dq.popleft()
        while dq and values[dq[-1]] >= values[i]:
            dq.pop()
        dq.append(i)
        result[i] = values[dq[0]]
    return result


def _ema(values: list[float], span: int) -> list[float]:
    """Compute EMA. Returns 0.0 for indices before span-1, then starts EMA from SMA seed."""
    n = len(values)
    result = [0.0] * n
    if n < span:
        return result
    # Seed with SMA of first `span` values
    seed = sum(values[:span]) / span
    result[span - 1] = seed
    multiplier = 2.0 / (span + 1.0)
    for i in range(span, n):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_simulation_exit_integration.py -v`

Expected: ALL PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_exit_engine.py tests/test_backtest_engine.py -v`

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/sepa_scanner/backtest/simulation.py tests/test_simulation_exit_integration.py
git commit -m "feat(simulation): enrich symbol data with technical indicators for BarData"
```

---

### Task 4: Replace inline exit logic with `evaluate_exit()` calls

This is the core change. Replace the ~90-line inline exit block in `simulate_portfolio_python` with calls to `evaluate_exit()`, building `TradeState` and `BarData` from position state and symbol data.

**Files:**
- Modify: `src/sepa_scanner/backtest/simulation.py:199-334` (the main position loop)
- Test: `tests/test_simulation_exit_integration.py`

- [ ] **Step 1: Write integration test for phased exit in simulation**

Add to `tests/test_simulation_exit_integration.py`:

```python
from sepa_scanner.backtest.simulation import simulate_portfolio_python
from sepa_scanner.backtest.models import BacktestParams


def _make_trending_ohlcv(
    symbol: str = "WINNER",
    n_days: int = 120,
    start: date = date(2024, 1, 1),
    daily_gain: float = 0.5,
) -> pl.DataFrame:
    """Generate OHLCV for a stock in a steady uptrend."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    rows = []
    base = 100.0
    for i, d in enumerate(dates):
        c = base + i * daily_gain
        rows.append({
            "date": d,
            "symbol": symbol,
            "open": c - 0.3,
            "high": c + 1.5,
            "low": c - 1.0,
            "close": c,
            "volume": 1_000_000,
        })
    return pl.DataFrame(rows).cast({"date": pl.Date, "volume": pl.Int64})


def test_simulation_uses_phased_exit_engine():
    """A steadily rising stock should be held longer than 15 days with phased exits."""
    ohlcv = _make_trending_ohlcv(n_days=120, daily_gain=0.8)
    # Signal on day 10: entry_ref slightly above close, stop below
    entry_day = date(2024, 1, 11)
    entry_price = 108.0  # close on day 10 = 100 + 10*0.8 = 108
    stop_price = 102.0
    signals = pl.DataFrame({
        "signal_date": [entry_day],
        "symbol": ["WINNER"],
        "entry_ref": [entry_price + 0.5],
        "stop": [stop_price],
        "pivot": [entry_price],
        "rs_rank": [85.0],
        "signal_size_mult": [1.0],
    }).cast({"signal_date": pl.Date})

    params = BacktestParams(pending_days=5, max_hold_days=90)
    portfolio = {
        "initial_capital": 1_000_000,
        "risk_per_trade_pct": 0.01,
        "max_positions": 10,
        "drawdown_half_pct": 0.10,
        "drawdown_stop_pct": 0.15,
        "drawdown_close_pct": 0.20,
        "pyramid_trigger_r": 0.5,
    }

    result = simulate_portfolio_python(
        signals=signals,
        ohlcv=ohlcv,
        params=params,
        portfolio=portfolio,
        analysis_sector_map={"WINNER": "TestSector"},
    )
    trades = result["trades"]
    assert len(trades) >= 1
    filled = [t for t in trades if t.entry_date is not None]
    assert len(filled) == 1
    trade = filled[0]
    # With a steadily rising stock at 0.8/day and risk of ~6,
    # the trade should be held well past 15 days
    assert trade.holding_days > 15, f"Trade held only {trade.holding_days} days — exit engine not wired"
    assert trade.r_multiple > 1.0, f"R-multiple is {trade.r_multiple} — should be >1R on a rising stock"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_simulation_exit_integration.py::test_simulation_uses_phased_exit_engine -v`

Expected: FAIL — trade gets time-stopped at ~15 days with the old inline logic.

- [ ] **Step 3: Add helper to build ExitConfig from dict**

At the top of `simulate_portfolio_python` in `simulation.py`, add a helper function (before the function definition):

```python
def _build_exit_config(exit_cfg: dict[str, Any]) -> ExitConfig:
    """Build an ExitConfig from the exit_cfg dict, using defaults for missing keys."""
    cfg = ExitConfig()
    field_map = {
        "breakeven_activation_r": "breakeven_activation_r",
        "partial_profit_r": "partial_profit_r",
        "partial_profit_pct": "partial_profit_pct",
        "trail_pct": "trail_pct",
        "trail_pct_bull": "trail_pct_bull",
        "trail_pct_bear": "trail_pct_bear",
        "time_stop_days": "time_stop_days",
        "time_stop_min_r": "time_stop_min_r",
        "max_hold_before_tighten": "max_hold_before_tighten",
        "soft_expiry_days": "soft_expiry_days",
        "soft_expiry_gain": "soft_expiry_gain",
    }
    for dict_key, attr in field_map.items():
        val = exit_cfg.get(dict_key)
        if val is not None:
            setattr(cfg, attr, type(getattr(cfg, attr))(val))
    return cfg
```

- [ ] **Step 4: Replace inline exit block with evaluate_exit calls**

Replace the entire exit logic block in the position loop (lines ~219-334 of `simulation.py`) with:

```python
            if idx is not None:
                open_price = float(sym_prices["opens"][idx])
                high_price = float(sym_prices["highs"][idx])
                low_price = float(sym_prices["lows"][idx])
                close_price = float(sym_prices["closes"][idx])
                holding_days = (today - pos.entry_date).days

                pos.mae_price = min(pos.mae_price, low_price)
                pos.mfe_price = max(pos.mfe_price, high_price)

                # --- Gap-down stop: if open gaps below stop, exit at open ---
                if open_price <= pos.active_stop:
                    exit_price = open_price
                    status = "stopped"
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
                    ema_21=float(sym_prices["ema_21"][idx]),
                    ema_50=float(sym_prices["ema_50"][idx]),
                    low_10d=float(sym_prices["low_10d"][idx]),
                    prev_close=float(sym_prices["prev_close"][idx]),
                    avg_range_20=float(sym_prices["avg_range_20"][idx]),
                    avg_volume_50=float(sym_prices["avg_volume_50"][idx]),
                )

                # --- Call phased exit engine ---
                actions = evaluate_exit(
                    trade_state,
                    bar=bar,
                    config=phased_exit_config,
                    market_regime="bull",
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
                exit_price = None
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
```

Also, at the start of `simulate_portfolio_python`, build the `ExitConfig` once. Replace the existing `exit_cfg` dict construction (lines ~169-181) with:

```python
    exit_cfg = {
        "breakeven_activation_r": 1.0,
        "partial_profit_r": 2.0,
        "partial_profit_pct": 0.50,
        "trail_pct_bull": params.trail_pct,
        "trail_pct_bear": params.trail_pct,
        "time_stop_days": params.dead_money_days,
        "max_hold_before_tighten": params.max_hold_days,
    }
    if exit_config:
        for key, value in exit_config.items():
            if value is not None:
                exit_cfg[key] = value

    phased_exit_config = _build_exit_config(exit_cfg)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_simulation_exit_integration.py tests/test_exit_engine.py -v`

Expected: ALL PASS

- [ ] **Step 6: Run all backtest tests for regression**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/ -k "backtest or exit or simulation" -v`

Expected: ALL PASS (some tests may need adjustment if they relied on the old inline exit behavior — fix any that fail)

- [ ] **Step 7: Commit**

```bash
git add src/sepa_scanner/backtest/simulation.py tests/test_simulation_exit_integration.py
git commit -m "feat(simulation): replace inline exits with phased exit engine"
```

---

### Task 5: Validate with portfolio diagnostics

Run the full backtest and compare metrics before/after to confirm the changes improved R-multiple distribution and holding periods.

**Files:**
- None modified — this is a validation step
- Run: `scripts/portfolio_diagnostics.py`

- [ ] **Step 1: Run the portfolio diagnostics**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python scripts/portfolio_diagnostics.py`

(This may take a few minutes depending on data size)

- [ ] **Step 2: Analyze the results**

Check the new `reports/portfolio_diagnostics/` output:

```bash
python3 -c "
import polars as pl
df = pl.read_csv('reports/portfolio_diagnostics/*/trade_pnl.csv')  # adjust path to latest
total = len(df)
winners = df.filter(pl.col('r_multiple') > 0)
print(f'Total trades: {total}')
print(f'Win rate: {len(winners)/total:.1%}')
print(f'Avg hold (all): {df[\"holding_days\"].mean():.0f} days')
print(f'Max hold: {df[\"holding_days\"].max()} days')
print(f'Avg winner R: {winners[\"r_multiple\"].mean():.3f}')
print(f'Max R: {df[\"r_multiple\"].max():.2f}')
print(f'Trades > 3R: {len(df.filter(pl.col(\"r_multiple\") > 3))}')
print(f'Trades > 5R: {len(df.filter(pl.col(\"r_multiple\") > 5))}')
"
```

Expected improvements vs baseline:
- Avg holding days: from 5 → 15-30
- Max holding days: from 21 → 40-60+
- Max R: from 2.64 → 3R+ (ideally some 5R+ trades)
- Trades > 3R: from 0 → several

- [ ] **Step 3: Commit the results if satisfied**

```bash
git add reports/
git commit -m "data: portfolio diagnostics after exit engine integration"
```

# Exit Engine Integration: Wire Phased Exits into Portfolio Simulation

## Problem

`simulation.py` reimplements a simplified exit logic (flat percentage trail, basic time stop) while `exit_engine.py` has a sophisticated 6-phase Minervini exit system (EMA-based trails, climax detection, adaptive trail modes, sell-into-strength). The simulation's simplified exits cap winners at ~2.6R max, with zero 5R+ trades across 16 years. Average holding period is 5 days — incompatible with Minervini's trend-following approach.

Root causes:
1. 15-day time stop kills 65% of trades (257/396), including trades showing strength
2. Flat 15-20% trailing stop chops out winners during normal pullbacks
3. No EMA-based trailing despite `exit_engine.py` implementing it
4. Winners that hit 3R+ MFE exit at avg 2.32R (giving back 1.17R)

## Solution

Replace the inline exit logic in `simulation.py` with calls to `exit_engine.py`'s `evaluate_exit()`, and tune `ExitConfig` defaults to allow winners to run.

## Changes

### 1. Enrich `build_symbol_data` with technical indicators

Compute from raw OHLCV per symbol:
- `sma_50`: 50-period simple moving average of close
- `sma_200`: 200-period simple moving average of close
- `ema_21`: 21-period exponential moving average of close
- `ema_50`: 50-period exponential moving average of close
- `low_10d`: rolling 10-day low
- `prev_close`: previous day's close (shift by 1)
- `avg_range_20`: 20-period average of (high - low)
- `avg_volume_50`: 50-period average of volume
- `volume`: raw volume

These are standard rolling calculations on data already available.

### 2. Replace inline exit logic with `evaluate_exit()` calls

In the daily position loop (`simulation.py:200-334`), replace the inline exit block with:

```python
# Build TradeState from OpenPosition
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
    highest_close=pos.mfe_price,  # reuse MFE tracking
    trail_mode=pos.trail_mode,
    breakeven_activated=pos.breakeven_activated,
    partial_taken=pos.partial_taken,
    trail_activated=pos.trail_activated,
    tighten_activated=pos.tighten_activated,
    days_held=(today - pos.entry_date).days,
)

# Build BarData from symbol_data
bar = BarData(
    open=open_price, high=high_price, low=low_price, close=close_price,
    volume=sym_prices["volumes"][idx],
    sma_50=sym_prices["sma_50"][idx],
    sma_200=sym_prices["sma_200"][idx],
    ema_21=sym_prices["ema_21"][idx],
    ema_50=sym_prices["ema_50"][idx],
    low_10d=sym_prices["low_10d"][idx],
    prev_close=sym_prices["prev_close"][idx],
    avg_range_20=sym_prices["avg_range_20"][idx],
    avg_volume_50=sym_prices["avg_volume_50"][idx],
)

# Call phased exit engine
actions = evaluate_exit(trade_state, bar=bar, config=exit_config, market_regime=regime, rs_rank=rs_rank)

# Process actions: SELL_ALL closes position, SELL_PARTIAL reduces shares + adds cash
```

### 3. Sync state back to OpenPosition

After `evaluate_exit()`, copy mutated `TradeState` fields back:
- `pos.phase = trade_state.phase`
- `pos.active_stop = trade_state.active_stop`
- `pos.breakeven_activated = trade_state.breakeven_activated`
- `pos.partial_taken = trade_state.partial_taken`
- `pos.trail_activated = trade_state.trail_activated`
- `pos.tighten_activated = trade_state.tighten_activated`
- `pos.trail_mode = trade_state.trail_mode`
- `pos.mfe_price = trade_state.highest_close`

### 4. Tune ExitConfig for longer holds

Changes to `exit_engine.py` `ExitConfig` defaults and `evaluate_exit()` logic:

| Parameter | Old | New | Rationale |
|-----------|-----|-----|-----------|
| `time_stop_days` | 15 | 15 (unchanged) | Keep the time stop |
| Time stop condition | `r_multiple < 1.0` | `r_multiple < 0.3` | Don't kill trades showing strength |
| `max_hold_before_tighten` | 40 | 60 | Give winners more room |
| `soft_expiry_days` | 30 | 45 | Longer runway for trend development |
| Trail at 3R+ | Same as 2R | Force `trail_50ema` | Wider trail for big winners |

New logic in `evaluate_exit()`:
- When `r_multiple >= 3.0` and trail mode is tighter than 50 EMA, widen to 50 EMA
- This prevents normal consolidation from stopping out big winners

### 5. Build ExitConfig from exit_config dict

The simulation currently passes `exit_config` as a dict. Add a helper to build an `ExitConfig` dataclass from this dict, falling back to defaults for missing keys.

## Files Modified

- `src/sepa_scanner/backtest/simulation.py` — main changes (replace inline exits, enrich symbol data)
- `src/sepa_scanner/backtest/exit_engine.py` — tune defaults, add 3R+ trail widening logic
- `src/sepa_scanner/backtest/models.py` — remove `dead_money_days` from BacktestParams if no longer needed (or keep for backward compat)

## Testing

Run the portfolio diagnostics script and compare:
- R-multiple distribution (expect more 3R+ and 5R+ trades)
- Average holding period (expect increase from 5 days to 15-30 days)
- Max holding period (expect increase from 21 days to 40-60+ days)
- MFE giveback (expect reduction from 42% to ~25%)
- Overall CAGR and max drawdown (monitor for degradation)

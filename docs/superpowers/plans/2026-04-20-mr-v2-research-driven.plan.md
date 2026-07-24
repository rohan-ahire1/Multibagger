# Mean Reversion v2: Research-Driven Optimization

## Summary

Rebuild the Connors RSI2 mean reversion strategy based on published research findings. The current v1 generates 109% CAGR at 0% cost but collapses to 14% at 0.5% cost due to ~5000 fills over 16 years. The fix is fewer, higher-quality entries with better exits.

This plan adds new indicators, new entry filters, new exit modes, and a structured A/B/C sweep comparing three research-backed configurations against the v1 baseline. All changes are in `strategies/mean_reversion/` — no changes to the SEPA scanner.

## Output Philosophy

Data only. No opinions, no recommendations. Print every metric. The human decides.

## Baseline (v1 best combo for reference)

```
RSI(2)<25, close>SMA(200), exit=SMA(3), stop=ATR(14)×3, risk=5%
At 0.0% cost: 109% CAGR, -44.7% MaxDD, Calmar 2.44, 5180 fills
At 0.2% cost:  65% CAGR, -45.7% MaxDD, Calmar 1.42, 5022 fills
At 0.5% cost:  14% CAGR, -65.4% MaxDD, Calmar 0.22, 5012 fills
```

---

## Task 1: New Indicators (`strategies/mean_reversion/indicators.py`)

Add these functions to the existing file. Do not modify existing functions.

### 1.1 ConnorsRSI

```python
def connors_rsi(
    closes: Sequence[float | int | None],
    rsi_period: int = 3,
    streak_rsi_period: int = 2,
    pct_rank_period: int = 100,
) -> list[float]:
    """
    ConnorsRSI(3,2,100) = (RSI(Close,3) + RSI(Streak,2) + PercentRank(ROC1,100)) / 3
    
    Components:
    1. RSI of close prices with period=3 (not 2)
    2. RSI of streak series with period=2
       - Streak = signed count of consecutive up/down closes
       - Up close: streak += 1 (or reset to +1 if was negative)
       - Down close: streak -= 1 (or reset to -1 if was positive)  
       - Unchanged: streak = 0
    3. PercentRank of today's 1-day ROC over last 100 days
       - ROC1 = (close[t] - close[t-1]) / close[t-1]
       - PercentRank = count(historical ROC1 < today's ROC1) / pct_rank_period * 100
    
    Returns NaN for warmup period (max of rsi_period, streak_rsi_period, pct_rank_period).
    """
```

### 1.2 Streak series (helper for ConnorsRSI)

```python
def streak(closes: Sequence[float | int | None]) -> list[float]:
    """
    Consecutive up/down close counter.
    - If close > prev_close: streak = prev_streak + 1 if prev_streak > 0, else 1
    - If close < prev_close: streak = prev_streak - 1 if prev_streak < 0, else -1
    - If close == prev_close: streak = 0
    First bar: NaN.
    """
```

### 1.3 Percent Rank

```python
def percent_rank(values: Sequence[float | int | None], period: int = 100) -> list[float]:
    """
    For each bar, count how many of the previous `period` values are strictly less
    than the current value. Return as percentage (0-100).
    NaN for first `period` bars.
    """
```

### 1.4 Internal Bar Strength (IBS)

```python
def ibs(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
) -> list[float]:
    """
    IBS = (Close - Low) / (High - Low)
    Returns 0.5 when High == Low (flat bar). Range [0, 1].
    """
```

### 1.5 ADX (Average Directional Index)

```python
def adx(
    highs: Sequence[float | int | None],
    lows: Sequence[float | int | None],
    closes: Sequence[float | int | None],
    period: int = 14,
) -> list[float]:
    """
    Wilder ADX. Steps:
    1. +DM = max(high - prev_high, 0) if > max(prev_low - low, 0), else 0
    2. -DM = max(prev_low - low, 0) if > max(high - prev_high, 0), else 0
    3. Smooth +DM, -DM, and TR with Wilder smoothing over `period`
    4. +DI = smoothed_+DM / smoothed_TR * 100
    5. -DI = smoothed_-DM / smoothed_TR * 100
    6. DX = abs(+DI - -DI) / (+DI + -DI) * 100
    7. ADX = Wilder smooth of DX over `period`
    NaN for warmup (~2*period bars).
    """
```

### 1.6 Cumulative RSI

```python
def cumulative_rsi(
    closes: Sequence[float | int | None],
    rsi_period: int = 2,
    sum_period: int = 2,
) -> list[float]:
    """
    CumRSI(2,2) = RSI(2)[t] + RSI(2)[t-1]
    Sum of the last `sum_period` RSI values.
    NaN if any component is NaN.
    """
```

### 1.7 Volume Percent Rank

```python
def volume_percent_rank(
    volumes: Sequence[float | int | None],
    period: int = 20,
) -> list[float]:
    """
    PercentRank of today's volume vs last `period` days.
    Low values = volume dry-up. Range [0, 100].
    """
```

### 1.8 Days Down counter

```python
def days_down(closes: Sequence[float | int | None], lookback: int = 5) -> list[float]:
    """
    Count of days where close < prev_close in last `lookback` bars.
    Range [0, lookback]. NaN for first `lookback` bars.
    """
```

### Tests (`strategies/mean_reversion/tests/test_indicators.py`)

Add tests for each new indicator. Key cases:

- `connors_rsi`: verify on a 20-bar example where all 3 components can be manually computed
- `streak`: [100, 101, 102, 99, 98, 98, 99] → [NaN, 1, 2, -1, -2, 0, 1]
- `percent_rank`: known 10-value series, verify rank of final value
- `ibs`: bar with close=low → 0.0, close=high → 1.0, close=mid → 0.5
- `adx`: verify on a 30-bar trending series that ADX > 25
- `cumulative_rsi`: sum of two RSI(2) values matches manual calc
- `days_down`: [100, 99, 98, 99, 97, 96] with lookback=5 → last bar = 4

---

## Task 2: Upgraded Signal Engine (`strategies/mean_reversion/signal_engine.py`)

Replace `scan_mean_reversion()` with a new `scan_mr_v2()` that computes all indicators per symbol and applies configurable filter combinations. Keep the old function for baseline comparison.

```python
def scan_mr_v2(
    ohlcv: pl.DataFrame,
    *,
    # Indicator selection
    entry_indicator: str = "crsi",  # "rsi2", "crsi", "cum_rsi"
    entry_threshold: float = 10.0,
    
    # Trend filter
    sma_trend_period: int = 200,
    
    # Secondary filters (each can be None to disable)
    min_days_down: int | None = None,        # e.g., 4 of last 5
    days_down_lookback: int = 5,
    max_ibs: float | None = None,            # e.g., 0.2
    max_adx: float | None = None,            # e.g., 20
    max_vol_rank: float | None = None,       # e.g., 20 (volume dry-up)
    vol_rank_period: int = 20,
    
    # Existing filters
    min_adv_inr: float = 1e8,
    min_price: float = 50.0,
    stop_atr_mult: float = 2.0,
    
    # Signal ranking
    max_signals_per_day: int | None = None,  # e.g., 3 (take most oversold)
    
    # Cooldown
    cooldown_days: int = 0,                  # min days between signals on same symbol
    
    universe_symbols: set[str] | None = None,
) -> pl.DataFrame:
```

**Signal schema** — extend from v1:

```python
MR_V2_SIGNAL_SCHEMA = {
    "signal_date": pl.Date,
    "symbol": pl.String,
    "close": pl.Float64,
    "rsi2": pl.Float64,
    "crsi": pl.Float64,        # NEW
    "cum_rsi": pl.Float64,     # NEW
    "ibs": pl.Float64,         # NEW
    "adx14": pl.Float64,       # NEW
    "vol_rank20": pl.Float64,  # NEW
    "days_down_5": pl.Float64, # NEW
    "sma200": pl.Float64,
    "sma5": pl.Float64,
    "sma3": pl.Float64,        # NEW (needed for exit)
    "entry_ref": pl.Float64,
    "stop": pl.Float64,
    "atr14": pl.Float64,
    "adv50": pl.Float64,
}
```

**Implementation:**
- Precompute ALL indicators per symbol (rsi2, crsi, cum_rsi, ibs, adx, vol_rank, days_down, sma3, sma5, sma200, atr14, adv50)
- Apply filters in this order:
  1. `close > SMA(200)` (trend)
  2. Entry indicator < threshold (oversold)
  3. `close > min_price` and `ADV(50) > min_adv`
  4. Optional: `days_down >= min_days_down`
  5. Optional: `IBS < max_ibs`
  6. Optional: `ADX(14) < max_adx`
  7. Optional: `VolRank(20) < max_vol_rank`
- Apply cooldown (skip symbol if last signal was within cooldown_days)
- Apply daily ranking (sort by entry indicator ascending, keep top max_signals_per_day)
- Emit all indicator values in the signal row (for post-hoc analysis)

---

## Task 3: Upgraded Exit Engine (`strategies/mean_reversion/exit_engine.py`)

Add new exit modes. Keep existing modes working.

Add `cum_rsi_exit` parameter to `evaluate_mr_exit`:

```python
def evaluate_mr_exit(
    *,
    # ... existing params ...
    sma5: float,
    sma3: float,
    rsi2: float,
    cum_rsi2: float,          # NEW: 2-day cumulative RSI(2)
    config: MRExitConfig,
) -> MRExitAction:
```

New exit modes to add (in the mode switch):

- `"sma5"` — existing: close > SMA(5)
- `"sma3"` — existing: close > SMA(3)  
- `"rsi_50"` — existing: RSI(2) > 50
- `"rsi_70"` — existing: RSI(2) > 70
- `"cum_rsi_130"` — NEW: exit when 2-day cumulative RSI(2) > 130
- `"cum_rsi_150"` — NEW: exit when 2-day cumulative RSI(2) > 150
- `"combined_sma5_10d"` — NEW: exit on close > SMA(5) OR holding_days >= 10

Update `MRExitConfig` to add `max_hold_days: int = 20` as the safety net (already exists).

---

## Task 4: Update Simulation (`strategies/mean_reversion/simulation.py`)

### 4.1 Precompute additional indicators in `build_mr_symbol_data`

Add to the per-symbol dict:
```python
"crsi": connors_rsi(closes),
"cum_rsi": cumulative_rsi(closes, 2, 2),
"ibs_values": ibs(highs, lows, closes),
"adx14": adx(highs, lows, closes, 14),
```

These are needed so the exit engine can access `cum_rsi` at each bar.

### 4.2 Pass `cum_rsi2` to exit engine

In the simulation loop where `evaluate_mr_exit` is called, look up `cum_rsi` from `symbol_data` at the current bar index and pass it as `cum_rsi2=`.

### 4.3 Add cooldown tracking

Track `last_exit_date[symbol]` dict. When processing pending signals, skip if `today - last_exit_date[symbol] < cooldown_days`. Set `cooldown_days` as a parameter of `simulate_mr_portfolio`.

### 4.4 Add sector weighting

Add optional `sector_weights: dict[str, float] | None` parameter. If provided, multiply position size by `sector_weights.get(sector, 1.0)`. This allows testing research finding that Banks/Financials revert better (weight 1.2x) and IT reverts worse (weight 0.5x).

---

## Task 5: Structured Sweep (`strategies/mean_reversion/diagnostics.py`)

Replace the current 4-phase sweep with a new architecture that tests three research-backed configurations head-to-head against v1 baseline.

### 5.1 New sweep function

```python
def run_v2_sweep(config, ohlcv, universe, sector_map, start_date, end_date, output_dir, round_trip_cost_pct=0.0):
```

### 5.2 Test matrix

Run each configuration at THREE cost levels: 0.0%, 0.2%, 0.5%.

**Baseline (v1 best):**
```
entry_indicator="rsi2", entry_threshold=25, exit_mode="sma3", 
stop_atr_mult=3.0, risk=5%, no filters
```

**Version A: ConnorsRSI + light filters**
```
entry_indicator="crsi", entry_threshold=10, 
min_days_down=4, days_down_lookback=5,
exit_mode="sma5", stop_atr_mult=3.0, risk=5%,
max_signals_per_day=3, cooldown_days=5
```

**Version B: Cumulative RSI + strict filters (turnover killer)**
```
entry_indicator="cum_rsi", entry_threshold=5,
min_days_down=4, days_down_lookback=5,
max_vol_rank=20, vol_rank_period=20,
exit_mode="sma5", stop_atr_mult=3.0, risk=5%,
max_signals_per_day=2, cooldown_days=5
```

**Version C: ConnorsRSI + IBS + VolRank (capitulation filter)**
```
entry_indicator="crsi", entry_threshold=10,
max_ibs=0.2, max_vol_rank=20,
min_days_down=4, days_down_lookback=5,
exit_mode="combined_sma5_10d", stop_atr_mult=3.0, risk=5%,
max_signals_per_day=2, cooldown_days=5
```

**Version D: Version A + ADX regime filter**
```
Same as Version A, plus max_adx=20
```

**Version E: Version A + sector weights**
```
Same as Version A, plus sector_weights:
  "Financial Services": 1.2, "Capital Goods": 1.0,
  "Information Technology": 0.5, default: 1.0
```

### 5.3 After the 5-version comparison, run component isolation tests

These isolate the value of each filter independently, using Version A as the base:

| Test | Change from Version A |
|------|----------------------|
| A_no_cooldown | cooldown_days=0 |
| A_no_ranking | max_signals_per_day=None |
| A_no_days_down | min_days_down=None |
| A_strict_crsi | entry_threshold=5 instead of 10 |
| A_exit_sma3 | exit_mode="sma3" instead of "sma5" |
| A_exit_rsi50 | exit_mode="rsi_50" |
| A_exit_cum_rsi | exit_mode="cum_rsi_130" |
| A_tighter_stop | stop_atr_mult=1.5 instead of 3.0 |

### 5.4 Output format

Print a comparison table for each cost level:

```
=== COST: 0.2% ===
Version     Fills  CAGR    MaxDD    Calmar  WR     AvgR    Payoff  AvgHold  Signals
v1_base     5022   65.0%  -45.7%    1.42   66.3%  0.064R   0.82   2.8d     38897
version_a   ???    ???%   ???%      ???    ???%   ???R     ???    ???d     ???
version_b   ???    ???%   ???%      ???    ???%   ???R     ???    ???d     ???
version_c   ???    ???%   ???%      ???    ???%   ???R     ???    ???d     ???
version_d   ???    ???%   ???%      ???    ???%   ???R     ???    ???d     ???
version_e   ???    ???%   ???%      ???    ???%   ???R     ???    ???d     ???
```

Then component isolation table:

```
=== COMPONENT ISOLATION (at 0.2% cost) ===
Variant          Fills  CAGR    MaxDD    Calmar  Diff_vs_A
version_a        ???    ???%   ???%      ???     baseline
A_no_cooldown    ???    ???%   ???%      ???     +/-???
A_no_ranking     ???    ???%   ???%      ???     +/-???
...
```

### 5.5 Full output for best version

For whichever version has the best Calmar at 0.2% cost, generate full reports:
- summary.json, daily_equity.csv, trade_pnl.csv
- yearly_returns.csv, monthly_returns.csv
- r_distribution.csv, streak_distribution.csv
- signals_per_year.csv, best/worst 10 trades

### 5.6 CLI

```
uv run python strategies/mean_reversion/diagnostics.py --sweep-v2 --cost 0.002
```

Add `--sweep-v2` flag alongside existing `--sweep`. Keep old sweep working.

---

## Task 6: Add Nifty 50 index data for regime filter (future use)

In `build_mr_symbol_data`, also compute and return a `market_regime` dict:

```python
# After building per-symbol data, also build Nifty 50 / benchmark regime
nifty_data = symbol_data.get("NIFTY 50") or symbol_data.get("^NSEI")
if nifty_data:
    nifty_sma200 = sma(nifty_data["closes"], 200)
    market_above_200sma = {
        nifty_data["dates"][i]: nifty_data["closes"][i] > nifty_sma200[i]
        for i in range(len(nifty_data["dates"]))
        if not math.isnan(nifty_sma200[i])
    }
```

If the benchmark symbol is not in the OHLCV data, skip the regime filter gracefully (return all True). The OHLCV parquet has `^NSEI` or the config's `benchmark_symbol` — check which one is available.

This is prep for a future regime test but not part of the main A/B/C sweep. Include the data in the symbol_data return dict but don't filter on it unless Version D explicitly tests it.

---

## Output Structure

```
reports/mean_reversion/
├── sweep/                          # v1 sweep (existing, keep)
│   └── ...
├── sweep_v2/                       # v2 sweep (new)
│   ├── comparison_0.0pct.csv       # All versions at 0% cost
│   ├── comparison_0.2pct.csv       # All versions at 0.2% cost
│   ├── comparison_0.5pct.csv       # All versions at 0.5% cost
│   ├── component_isolation.csv     # Component isolation tests
│   └── best_version/               # Full reports for best version
│       ├── summary.json
│       ├── daily_equity.csv
│       ├── trade_pnl.csv
│       ├── yearly_returns.csv
│       ├── monthly_returns.csv
│       ├── r_distribution.csv
│       └── signals_per_year.csv
```

---

## Test Plan

### Unit tests (add to `strategies/mean_reversion/tests/test_indicators.py`)
- All new indicator functions have at least 2 test cases each (see Task 1)
- Edge cases: empty input, all-NaN input, single-value input

### Integration tests
- `uv run python -m py_compile strategies/mean_reversion/indicators.py`
- `uv run python -m py_compile strategies/mean_reversion/signal_engine.py`
- `uv run python -m py_compile strategies/mean_reversion/exit_engine.py`
- `uv run python -m py_compile strategies/mean_reversion/simulation.py`
- `uv run python -m py_compile strategies/mean_reversion/diagnostics.py`
- `uv run pytest strategies/mean_reversion/tests/` — all pass
- Existing `--sweep` still works unchanged

### Smoke test
- `uv run python strategies/mean_reversion/diagnostics.py --sweep-v2 --start 2024-01-01 --end 2025-12-31 --cost 0.002` completes without error on 2-year window

---

## Assumptions

- ConnorsRSI warmup needs ~100 bars (pct_rank_period). Signals before bar 200 will be sparse — that's expected.
- ADX warmup needs ~28 bars (2 × period). Also expected.
- The existing v1 `scan_mean_reversion()` function is kept unchanged for backward compatibility.
- `round_trip_cost_pct` parameter already wired through simulation (added in previous session).
- Nifty 50 index may or may not be in OHLCV parquet — handle gracefully.
- All 8 new indicator functions are pure Python operating on lists — same pattern as existing `rsi()`, `sma()`, `atr()`.
- No external data needed (India VIX not in parquet — skip for now, note in output).

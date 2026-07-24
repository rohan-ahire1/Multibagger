# Connors RSI2 Mean Reversion Strategy — Backtest Implementation

## Summary

Implement a Connors-style RSI(2) mean reversion backtest for Nifty 100 stocks, reusing the existing Parquet/DuckDB/Polars infrastructure. Separate strategy folder, own config, own signal engine, own diagnostics script. Shares OHLCV data, portfolio simulation loop, and settings framework with the SEPA scanner.

**Strategy in one line:** Buy when RSI(2) < 10 and price > 200SMA, sell when price reverts to mean (closes above 5SMA). Sweep holding periods 1-10 days.

## Output Philosophy

Data only. No opinions, no recommendations. Print every metric, every slice. The human decides.

---

## Strategy Specification

### Entry Rules (Connors RSI2)

All conditions must be true on signal_date:

1. **Trend filter:** `close > SMA(200)` — only buy in uptrends
2. **Oversold:** `RSI(2) < threshold` — extreme short-term oversold
   - Sweep thresholds: 5, 10, 15, 20, 25
3. **Liquidity:** `ADV(50) > 10 Cr INR` — Nifty 100 level liquidity
4. **Price:** `close > 50 INR` — no penny stocks

Entry price: next-day open (market order at open, same as SEPA backtest).
Stop: `ATR(14) * 2.0` below entry price (fixed initial stop, sweep multipliers 1.5, 2.0, 2.5, 3.0).

### Exit Rules

Test multiple exit variants independently:

1. **Mean revert exit (primary):** Close when `close > SMA(5)` — price reverted to short-term mean
2. **Mean revert SMA(3):** Close when `close > SMA(3)` — faster revert
3. **RSI exit:** Close when `RSI(2) > 70` — oversold → overbought flip
4. **RSI exit aggressive:** Close when `RSI(2) > 50` — just normalize
5. **Time stop:** Close after N days regardless (sweep N = 1, 2, 3, 5, 7, 10)
6. **Combined:** Mean revert OR time stop, whichever comes first

For each exit variant, also apply the initial stop (hard loss limit).

### Position Sizing

Same R-based sizing as SEPA:
```
risk_per_share = entry_price - stop
position_size = (capital * risk_per_trade_pct) / risk_per_share
```

Sweep `risk_per_trade_pct`: 1%, 2%, 3%, 5%.

### Portfolio Rules

- `max_positions`: 10 (Nifty 100 has fewer simultaneous signals than broad NSE)
- `max_sector_positions`: 3
- `initial_capital`: 1,000,000 INR
- No regime gate initially (MR strategies can work in range-bound markets)
- No pyramiding

### Universe

Nifty 100 constituents. Use `data/reference/nifty500_constituents.csv` filtered to top 100 by market cap, OR download `nifty100_constituents.csv` from NSE. For backtesting, use a static list of current Nifty 100 symbols (survivorship bias is minimal at Nifty 100 level — almost no delistings).

---

## Implementation Plan

### Folder Structure

```
strategies/
└── mean_reversion/
    ├── __init__.py
    ├── config.yaml                 # Strategy-specific settings
    ├── indicators.py               # RSI, SMA computations
    ├── signal_engine.py            # Generate MR entry signals
    ├── exit_engine.py              # MR-specific exit logic
    ├── simulation.py               # Portfolio simulation (thin wrapper)
    ├── diagnostics.py              # Main runner script (like portfolio_diagnostics.py)
    ├── universe.py                 # Nifty 100 symbol list management
    └── tests/
        ├── __init__.py
        ├── test_indicators.py
        └── test_signals.py
```

### Task 1: Indicators (`strategies/mean_reversion/indicators.py`)

Implement pure-Polars indicator functions. These operate on a single-symbol DataFrame with columns `[date, open, high, low, close, volume]`.

```python
def rsi(closes: list[float], period: int = 2) -> list[float]:
    """Wilder RSI. Returns list same length as closes, NaN-filled for warmup."""
    # Standard Wilder smoothing:
    # 1. Calculate price changes: delta = close[i] - close[i-1]
    # 2. Separate gains (delta > 0) and losses (delta < 0)
    # 3. First avg_gain = mean(gains[:period]), first avg_loss = mean(losses[:period])
    # 4. Subsequent: avg_gain = (prev_avg_gain * (period-1) + current_gain) / period
    # 5. RS = avg_gain / avg_loss
    # 6. RSI = 100 - (100 / (1 + RS))
    # Return NaN for indices 0..period (insufficient data)
    ...

def sma(values: list[float], period: int) -> list[float]:
    """Simple moving average. NaN-filled for warmup."""
    # Reuse _rolling_mean pattern from portfolio_diagnostics.py
    ...

def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Average True Range (Wilder smoothing)."""
    # TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    # ATR = Wilder EMA of TR over period
    ...

def adv(volumes: list[float], closes: list[float], period: int = 50) -> list[float]:
    """Average daily value (volume * close) over period."""
    ...
```

**Tests (`test_indicators.py`):**
- RSI(2) of a known 10-bar sequence matches manual calculation
- RSI(2) returns NaN for first 2 bars
- SMA(5) of [1,2,3,4,5,6,7] = [NaN,NaN,NaN,NaN,3.0,4.0,5.0]
- ATR(14) warmup produces NaN for first 14 bars
- Edge case: all closes identical → RSI = 50 (or handle division by zero)

### Task 2: Signal Engine (`strategies/mean_reversion/signal_engine.py`)

Generate entry signals from OHLCV data.

```python
@dataclass(slots=True)
class MRSignal:
    signal_date: date
    symbol: str
    close: float
    rsi2: float
    sma200: float
    sma5: float
    entry_ref: float        # next-day open (filled at simulation time)
    stop: float             # close - atr14 * stop_atr_mult
    atr14: float
    adv50: float

def scan_mean_reversion(
    ohlcv: pl.DataFrame,
    *,
    rsi_period: int = 2,
    rsi_threshold: float = 10.0,
    sma_trend_period: int = 200,
    min_adv_inr: float = 1e8,     # 10 Cr
    min_price: float = 50.0,
    stop_atr_mult: float = 2.0,
    universe_symbols: set[str] | None = None,
) -> pl.DataFrame:
    """
    Scan all symbols on all dates. Return DataFrame of MR signals.
    
    For each (date, symbol):
    1. Filter to universe_symbols if provided
    2. Compute RSI(2), SMA(200), SMA(5), ATR(14), ADV(50)
    3. Check: close > SMA(200) AND RSI(2) < rsi_threshold AND close > min_price AND ADV(50) > min_adv_inr
    4. If all pass, emit signal with stop = close - ATR(14) * stop_atr_mult
    
    Returns DataFrame with columns matching MRSignal fields.
    """
```

**Implementation approach:**
- Group OHLCV by symbol
- For each symbol, compute all indicators as lists (same pattern as `build_symbol_data()` in `portfolio_diagnostics.py`)
- Iterate over dates, check conditions, emit signals
- Combine all signals into a single Polars DataFrame
- Sort by `[signal_date, symbol]`

**Do NOT store signals in DuckDB.** Generate them fresh each run — the scan is fast (100 symbols × 4000 days = 400K rows) and avoids stale state.

### Task 3: Exit Engine (`strategies/mean_reversion/exit_engine.py`)

Simpler than the SEPA phased exit. No phases, no trailing, no partial profits.

```python
@dataclass(slots=True)
class MRExitConfig:
    exit_mode: str = "sma5"       # "sma5", "sma3", "rsi_70", "rsi_50", "time_stop", "combined"
    time_stop_days: int = 5       # for time_stop and combined modes
    stop_atr_mult: float = 2.0    # initial stop distance

@dataclass(slots=True)
class MRExitAction:
    action: str                    # "SELL_ALL" or "HOLD"
    reason: str                    # "mean_revert_sma5", "time_stop", "stopped", "max_hold"

def evaluate_mr_exit(
    *,
    entry_price: float,
    entry_date: date,
    active_stop: float,
    today: date,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    sma5: float,
    sma3: float,
    rsi2: float,
    config: MRExitConfig,
) -> MRExitAction:
    """
    Exit evaluation order:
    1. Gap-down stop: if open <= active_stop → SELL_ALL("stopped")
    2. Intraday stop: if low <= active_stop → SELL_ALL("stopped")
    3. Exit mode checks:
       - "sma5": if close > sma5 → SELL_ALL("mean_revert_sma5")
       - "sma3": if close > sma3 → SELL_ALL("mean_revert_sma3")
       - "rsi_70": if rsi2 > 70 → SELL_ALL("rsi_overbought")
       - "rsi_50": if rsi2 > 50 → SELL_ALL("rsi_normalized")
       - "time_stop": if holding_days >= time_stop_days → SELL_ALL("time_stop")
       - "combined": check mean_revert_sma5 first, then time_stop
    4. Max hold: if holding_days >= 20 → SELL_ALL("max_hold") — safety net
    5. Otherwise → HOLD
    """
```

### Task 4: Portfolio Simulation (`strategies/mean_reversion/simulation.py`)

Thin wrapper around the portfolio simulation loop. **Do NOT copy-paste the 500-line simulate_portfolio_python.** Instead, write a simplified version that handles MR-specific logic.

```python
@dataclass(slots=True)
class MRPendingSignal:
    symbol: str
    signal_date: date
    close_at_signal: float
    stop: float
    expiration_date: date       # signal_date + 1 day (MR entries are next-day-only)
    rsi2: float
    atr14: float

@dataclass(slots=True)
class MROpenPosition:
    symbol: str
    signal_date: date
    entry_date: date
    entry_price: float
    stop: float
    active_stop: float
    shares: float
    initial_risk: float
    cost_basis: float
    mae_price: float
    mfe_price: float
    rsi2_at_entry: float

def simulate_mr_portfolio(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    *,
    max_positions: int = 10,
    max_sector_positions: int = 3,
    initial_capital: float = 1_000_000.0,
    risk_per_trade_pct: float = 0.02,
    exit_config: MRExitConfig = MRExitConfig(),
    sector_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Returns:
    {
        "final_equity": float,
        "daily_equity": pl.DataFrame,  # date, equity, cash, invested, positions, drawdown_pct
        "trades": pl.DataFrame,        # all closed trades with full detail
    }
    """
```

**Simulation loop structure** (reuse the pattern from `simulate_portfolio_python`):

```
For each trading day:
    1. Process open positions:
       a. Look up today's OHLCV + indicators (SMA3, SMA5, RSI2 — precomputed)
       b. Check gap-down stop (open <= active_stop → sell at open)
       c. Check intraday stop (low <= active_stop → sell at stop price)
       d. Call evaluate_mr_exit() with today's bar + indicators
       e. If SELL_ALL → close position, record trade
       f. Update MAE/MFE
    
    2. Process pending signals:
       a. Pending signals expire after 1 day (MR = buy tomorrow or skip)
       b. Fill at open price if open > stop (don't buy into a gap below stop)
       c. Check max_positions, max_sector_positions, available cash
       d. Size position: shares = (equity * risk_per_trade_pct) / (entry_price - stop)
       e. Open position
    
    3. Collect new signals for today:
       a. Signals generated for today go into pending (fill tomorrow)
    
    4. Record daily equity row
```

**Precompute indicators per symbol** at start (same as `build_symbol_data()`):
- For each symbol: dates, opens, highs, lows, closes, volumes
- Plus: sma3, sma5, sma200, rsi2, atr14 (all as lists indexed by date)
- Store in `symbol_data[symbol]` dict with `date_to_idx` mapping

**Trade output columns:**
```
symbol, signal_date, entry_date, exit_date, entry_price, exit_price,
return_pct, r_multiple, holding_days, status, exit_reason,
mae_price, mfe_price, mae_r, mfe_r, rsi2_at_signal, atr14,
sector, shares, pnl
```

### Task 5: Universe Management (`strategies/mean_reversion/universe.py`)

```python
def load_nifty100_universe() -> set[str]:
    """
    Load Nifty 100 symbols.
    
    Approach:
    1. Check for data/reference/nifty100_constituents.csv (manual download from NSE)
    2. If not found, fall back to top 100 symbols from nifty500_constituents.csv
       sorted by ADV50 from parquet (proxy for market cap)
    3. Return set of symbol strings
    """

# Hardcode current Nifty 100 as fallback constant:
NIFTY_100_SYMBOLS = {
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA",
    "HCLTECH", "WIPRO", "ULTRACEMCO", "NESTLEIND", "NTPC",
    "POWERGRID", "TATAMOTORS", "M&M", "ONGC", "JSWSTEEL",
    "TATASTEEL", "ADANIPORTS", "COALINDIA", "BAJAJFINSV",
    "TECHM", "HDFCLIFE", "APOLLOHOSP", "DRREDDY", "CIPLA",
    "DIVISLAB", "EICHERMOT", "GRASIM", "HEROMOTOCO",
    "BPCL", "BRITANNIA", "INDUSINDBK", "SBILIFE", "TATACONSUM",
    "HINDALCO", "ADANIENT", "BAJAJ-AUTO", "VEDL", "GODREJCP",
    "DABUR", "PIDILITIND", "HAVELLS", "SIEMENS", "ABB",
    "AMBUJACEM", "ACC", "BANKBARODA", "PNB", "IOC",
    "GAIL", "DLF", "TRENT", "ATGL", "TORNTPHARM",
    "SHREECEM", "MOTHERSON", "INDIGO", "BHEL", "HAL",
    "BEL", "IRCTC", "ZOMATO", "JIOFIN", "MANKIND",
    "PERSISTENT", "LTIM", "MAXHEALTH", "CUMMINSIND", "PIIND",
    "POLYCAB", "TVSMOTOR", "VOLTAS", "COLPAL", "MARICO",
    "MCDOWELL-N", "NAUKRI", "PAYTM", "PEL", "SAIL",
    "SRF", "JUBLFOOD", "LUPIN", "PAGEIND", "MPHASIS",
    "IDFCFIRSTB", "CGPOWER", "LODHA", "JINDALSTEL", "RECLTD",
    # Fill to 100 from current NSE list
}
```

**Note:** For backtest accuracy, using a static current list introduces mild look-ahead bias (we know these stocks survived and grew into Nifty 100). This is acceptable for a first-pass evaluation. Flag it in output but don't try to solve it.

### Task 6: Configuration (`strategies/mean_reversion/config.yaml`)

```yaml
strategy:
  name: connors_rsi2_mean_reversion
  universe: nifty100

entry:
  rsi_period: 2
  rsi_threshold: 10
  sma_trend_period: 200
  min_adv_inr: 100000000    # 10 Cr
  min_price_inr: 50
  stop_atr_mult: 2.0
  pending_days: 1             # MR signals are 1-day-only

exit:
  mode: sma5                  # sma5, sma3, rsi_70, rsi_50, time_stop, combined
  time_stop_days: 5
  max_hold_days: 20           # safety net

portfolio:
  max_positions: 10
  max_sector_positions: 3
  initial_capital: 1000000
  risk_per_trade_pct: 0.02

data:
  ohlcv_path: data/yf_adjusted/ohlcv_all.parquet
  sector_map_path: data/reference/nifty500_constituents.csv
```

Load with a simple YAML reader — don't couple to `sepa_scanner.settings.load_settings()`. Keep it independent:

```python
def load_mr_config(path: Path = Path("strategies/mean_reversion/config.yaml")) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)
```

### Task 7: Diagnostics Runner (`strategies/mean_reversion/diagnostics.py`)

Main script that ties everything together. Equivalent to `scripts/portfolio_diagnostics.py` but for MR.

```python
"""
Usage:
    uv run python strategies/mean_reversion/diagnostics.py --start 2010-01-04 --end 2026-03-17
    uv run python strategies/mean_reversion/diagnostics.py --sweep
"""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2010-01-04")
    parser.add_argument("--end", default="2026-03-17")
    parser.add_argument("--output-dir", default="reports/mean_reversion")
    parser.add_argument("--sweep", action="store_true", help="Run full parameter sweep")
    return parser.parse_args()
```

#### Single Run Mode (`--start` / `--end`)

1. Load config from `strategies/mean_reversion/config.yaml`
2. Load OHLCV from ParquetOhlcvStore (import from `sepa_scanner.storage.parquet_store`)
3. Filter to universe symbols
4. Generate signals via `scan_mean_reversion()`
5. Run `simulate_mr_portfolio()`
6. Compute metrics and write output

#### Sweep Mode (`--sweep`)

Run a grid sweep over key parameters. Print results as a table.

**Sweep grid:**

| Parameter | Values |
|-----------|--------|
| `rsi_threshold` | 5, 10, 15, 20, 25 |
| `exit_mode` | sma5, sma3, rsi_70, rsi_50, time_stop_3, time_stop_5, time_stop_7, combined |
| `stop_atr_mult` | 1.5, 2.0, 2.5, 3.0 |
| `risk_per_trade_pct` | 0.01, 0.02, 0.03, 0.05 |

**Do NOT run full cartesian product** (5 × 8 × 4 × 4 = 640 combos). Instead:

1. **Phase 1 — Exit mode sweep** (fix rsi_threshold=10, stop_atr_mult=2.0, risk=0.02):
   Run all 8 exit modes. Print table. → Identifies best exit mode.

2. **Phase 2 — RSI threshold sweep** (fix best exit mode, stop_atr_mult=2.0, risk=0.02):
   Run all 5 RSI thresholds. Print table.

3. **Phase 3 — Stop distance sweep** (fix best exit + RSI, risk=0.02):
   Run all 4 stop_atr_mult values. Print table.

4. **Phase 4 — Position sizing sweep** (fix best exit + RSI + stop):
   Run all 4 risk_per_trade_pct values. Print table.

Each phase prints:

```
=== PHASE 1: EXIT MODE SWEEP (rsi_thresh=10, atr_mult=2.0, risk=2%) ===
Exit Mode      Fills   CAGR    MaxDD    Calmar   WR      AvgR    Payoff  AvgHold
sma5           1240    12.3%   -8.1%    1.52     68%     0.32R   2.1:1   3.2d
sma3           1180    14.1%   -7.2%    1.96     71%     0.28R   1.8:1   2.1d
rsi_70         890     9.8%    -11.2%   0.88     62%     0.41R   2.5:1   5.4d
...
```

**After all 4 phases, run the final combination and output:**
- Full metrics (CAGR, MaxDD, Calmar, WR, AvgR, Payoff, fills, avg holding days)
- Year-by-year CAGR
- Monthly returns heatmap data (CSV: year × month matrix)
- Trade-level PnL CSV
- Daily equity CSV
- R-multiple distribution (histogram: <-2R, -2R to -1R, -1R to 0, 0 to 1R, 1R to 2R, >2R)
- Win/loss streak distribution
- Avg holding days for winners vs losers
- Best/worst 10 trades
- Signals per year (are there enough trades?)
- Fill rate (signals generated vs filled)

### Task 8: Metrics Computation

Implement in `diagnostics.py` or a separate `metrics.py`:

```python
def compute_metrics(daily_equity: pl.DataFrame, trades: pl.DataFrame) -> dict[str, Any]:
    """
    Compute from daily equity curve:
    - CAGR
    - Max drawdown (%)
    - Calmar ratio
    - Sharpe ratio (using daily returns, annualized with sqrt(252))
    - Sortino ratio
    - Total return %
    - Trading days
    
    Compute from trade list:
    - Total fills
    - Win rate
    - Average R-multiple
    - Median R-multiple
    - Payoff ratio (avg winner / avg loser in R)
    - Expectancy (WR * avg_win - (1-WR) * avg_loss)
    - Max consecutive wins
    - Max consecutive losses
    - Average holding days (all, winners, losers)
    - Profit factor (gross profit / gross loss)
    
    Return all as dict for JSON serialization.
    """
```

**Year-by-year CAGR** — compute from daily equity:
```python
def yearly_returns(daily_equity: pl.DataFrame) -> pl.DataFrame:
    """
    For each calendar year, compute:
    - Start equity, end equity
    - Return %
    - Max drawdown within year
    - Number of fills
    - Win rate within year
    """
```

### Output Structure

```
reports/mean_reversion/
├── {start}_{end}/
│   ├── summary.json                # All metrics
│   ├── daily_equity.csv            # date, equity, cash, invested, positions, drawdown_pct
│   ├── trade_pnl.csv              # Full trade list
│   ├── yearly_returns.csv         # Year-by-year breakdown
│   ├── monthly_returns.csv        # Year × month return matrix
│   └── r_distribution.csv        # R-multiple histogram
├── sweep/
│   ├── phase1_exit_mode.csv
│   ├── phase2_rsi_threshold.csv
│   ├── phase3_stop_distance.csv
│   ├── phase4_position_sizing.csv
│   ├── final_best_combo/
│   │   ├── summary.json
│   │   ├── daily_equity.csv
│   │   ├── trade_pnl.csv
│   │   ├── yearly_returns.csv
│   │   └── monthly_returns.csv
│   └── sweep_summary.csv         # All variants in one table
```

---

## Shared Code — Import Map

| What to import | From | Used in |
|---------------|------|---------|
| `ParquetOhlcvStore` | `sepa_scanner.storage.parquet_store` | `diagnostics.py` — loading OHLCV data |
| `load_sector_map` | `sepa_scanner.strategy.ranking` | `diagnostics.py` — sector map for position caps |
| `_rolling_mean`, `_rolling_min`, `_ema` | `sepa_scanner.backtest.simulation` | `simulation.py` — SMA/EMA helpers for precomputing symbol data |

**Do NOT import:**
- `simulate_portfolio_python` — too coupled to SEPA exit engine. Write a fresh sim loop.
- `load_settings` / `AppSettings` — MR has its own config. Keep decoupled.
- `ExitConfig` / `evaluate_exit` — SEPA-specific phased exit. MR uses its own.
- `BacktestParams` / `BacktestTrade` — too many SEPA-specific fields. MR has simpler dataclasses.

---

## Test Plan

### Unit Tests (`strategies/mean_reversion/tests/test_indicators.py`)

- RSI(2) computation matches known values (use a 20-bar manually computed example)
- RSI edge case: all same price → RSI = 50 (or defined behavior for 0/0)
- RSI edge case: monotonically increasing → RSI approaches 100
- RSI edge case: monotonically decreasing → RSI approaches 0
- SMA(5) matches manual calculation
- SMA(200) returns NaN for first 199 bars
- ATR(14) matches known values

### Unit Tests (`strategies/mean_reversion/tests/test_signals.py`)

- Signal generated when RSI(2) < 10 AND close > SMA(200) AND ADV > threshold
- No signal when RSI(2) > threshold
- No signal when close < SMA(200)
- No signal when ADV below threshold
- Stop price = close - ATR(14) * mult
- Multiple signals on same day ranked by RSI (most oversold first)

### Integration Tests

- Full simulation on 1 year of real data produces non-zero trades
- `uv run python -m py_compile strategies/mean_reversion/diagnostics.py` — syntax check
- `uv run python -m py_compile strategies/mean_reversion/simulation.py` — syntax check
- Sweep mode completes without error

### Verification Checks (print in output, not assertions)

- Avg holding days should be 2-7 days (MR is short-term)
- Fill rate should be reasonable (not 0%, not 100%)
- Win rate should be >55% (MR strategies typically have high WR, low payoff)
- Payoff ratio should be <2:1 (opposite of momentum — many small wins, occasional big loss)

---

## Assumptions

- OHLCV data in `data/yf_adjusted/ohlcv_all.parquet` already covers Nifty 100 stocks from 2010-2026 (it has 2831 symbols total, Nifty 100 is a subset)
- yfinance adjustments are applied (splits, dividends) — same data the SEPA backtest uses
- The `strategies/` folder does not exist yet — create it
- Python path: run from project root with `uv run python strategies/mean_reversion/diagnostics.py` — add `sys.path.insert(0, "src")` at top of diagnostics.py to find `sepa_scanner` imports
- No Rust acceleration needed — 100 symbols × 4000 days is fast enough in pure Python

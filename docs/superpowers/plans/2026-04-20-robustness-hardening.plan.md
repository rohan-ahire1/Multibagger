# Robustness Hardening: Sector Cap, Corporate Actions, Walk-Forward Validation

## Summary

Three independent workstreams that improve confidence in the strategy without touching any signal or exit parameters. Each workstream is self-contained — implement in order but each is independently mergeable.

**Current baseline:** 27.0% CAGR, -12.5% MaxDD, Calmar 2.17, 393 fills (2010-01-04 to 2026-03-17).

## Output Philosophy

**Data only. No opinions, no recommendations, no "worth keeping" judgments.**

Every workstream outputs raw numbers, tables, and diagnostics. Do NOT include recommendation sections, "accept/reject" verdicts, or subjective commentary in any output. The human makes all decisions — the scripts produce evidence.

Specifically:
- Do NOT write "Recommendation:" sections in reports or stdout.
- Do NOT classify results as "pass" or "fail".
- Do NOT suggest config changes based on results.
- DO print every metric you compute — more data is always better.
- DO include breakdowns by year, by sector, by window — every slice available.
- DO include raw trade-level data in CSVs so the human can re-analyze independently.
- When in doubt, add another column to the output rather than summarizing.

---

## Workstream 1: Fix Sector Map → Re-Run Sector Cap

### Problem

The sector cap evaluation (2026-04-20) failed catastrophically because 42% of signal-generating symbols (221/524) have no sector data. All unmapped symbols land in "UNKNOWN", which instantly blows any percentage cap. With `max_sector_pct=0.30`, 334/393 entries were blocked — not because of real sector concentration, but because "UNKNOWN" is one mega-sector.

**Root cause:** `data/reference/nifty500_constituents.csv` only covers current Nifty 500 constituents (500 symbols). The signal universe has 524 unique symbols spanning 16 years, including stocks that were in Nifty 500 historically but rotated out, plus non-Nifty-500 stocks that passed filters.

### Implementation

#### Task 1.1: Build comprehensive sector map

Create `scripts/build_sector_map.py` that:

1. Start with `nifty500_constituents.csv` as base (303 symbols already covered).
2. For the remaining 221 uncovered signal-generating symbols, use the Yahoo Finance `info` endpoint via `yfinance`:
   ```python
   import yfinance as yf
   ticker = yf.Ticker(f"{symbol}.NS")
   info = ticker.info
   sector = info.get("sector", "UNKNOWN")
   industry = info.get("industry", "UNKNOWN")
   ```
3. Map Yahoo Finance sectors to the 21 NSE Industry categories used in `nifty500_constituents.csv` (create a static mapping dict — Yahoo uses "Technology", NSE uses "Information Technology", etc.).
4. Output: `data/reference/full_sector_map.csv` with columns: `Symbol, Industry, Source` (source = "nifty500" or "yfinance" for auditability).
5. Merge with existing nifty500 data — nifty500 takes precedence when both exist.
6. Handle ETFs/index funds (BANKBEES, AUTOBEES, AXISGOLD, BSLGOLDETF, etc.) — map to "ETF" sector and exclude from sector cap logic.

**Rate limiting:** yfinance has no official rate limit but throttle to 1 request/second. Cache results in `data/reference/yfinance_sector_cache.json` so reruns don't re-fetch.

**Expected result:** ≥95% coverage of the 524 signal-generating symbols.

#### Task 1.2: Update sector map loading

In `scripts/portfolio_diagnostics.py`, line 1763 currently loads:
```python
sector_map = load_sector_map(Path(args.reference_dir) / "nifty500_constituents.csv")
```

Change to load the new comprehensive map:
```python
full_map_path = Path(args.reference_dir) / "full_sector_map.csv"
fallback_path = Path(args.reference_dir) / "nifty500_constituents.csv"
sector_map = load_sector_map(full_map_path if full_map_path.exists() else fallback_path)
```

The `load_sector_map()` function in `src/sepa_scanner/strategy/ranking.py` (line 7) expects CSV with `Industry` and `Symbol` columns — the new file must match this format.

#### Task 1.3: Re-run sector cap sweep

With the fixed sector map, re-run the exact same sweep from the previous evaluation:

```
Variant        | max_sector_pct | Expected behavior
baseline       | 1.0            | No cap (current)
sector_cap_30  | 0.30           | Max 30% capital in any one sector
sector_cap_25  | 0.25           | Max 25%
sector_cap_20  | 0.20           | Max 20%
```

Pass `max_sector_pct` via the portfolio dict override to `simulate_portfolio_python()`.

**Report for each variant:**
- Fills, CAGR, MaxDD, Calmar, WR, AvgR, PayoffRatio, total PnL
- Year-by-year CAGR for every variant (side by side table)
- Sector distribution of filled trades (all sectors, fill count + avg_r + WR per sector)
- Number of entries blocked by sector cap (per sector, per year)
- Per-sector equity contribution (how much PnL came from each sector)
- Monthly return series per variant (CSV)
- Trade-level PnL CSV for every variant (so human can re-slice)

**No decision rule.** Print all numbers. The human decides.

#### Task 1.4: Sector concentration analysis (diagnostic only)

Even if the cap doesn't improve Calmar, output a sector concentration report:
- For each year, what % of capital was in the top sector?
- Were there any months where >50% of capital was in one sector?
- What was the max concurrent same-sector drawdown?

This informs whether the cap is needed for live trading even if backtest metrics don't change.

### Files to create/modify
- **Create:** `scripts/build_sector_map.py`
- **Create:** `data/reference/full_sector_map.csv` (output)
- **Create:** `data/reference/yfinance_sector_cache.json` (cache)
- **Modify:** `scripts/portfolio_diagnostics.py` (line 1763 — sector map path)
- **No changes** to `src/` code — `load_sector_map()` and `max_sector_pct` wiring already work.

---

## Workstream 2: Corporate Actions Validation

### Problem

OHLCV data comes from yfinance with `Adj Close` adjustment applied forward to OHLC prices. The question is: are these adjustments correct and complete? If yfinance misses a split or applies it on the wrong date, backtest signals trigger at wrong prices, stops are at wrong levels, and PnL is wrong.

**Existing state:**
- `src/sepa_scanner/data_sources/yfinance_adjusted.py` applies `Adj Close / Close` ratio to all OHLC prices
- `config/corporate_actions.csv` has 6 seed entries (IRCTC 5:1, BAJFINANCE 2:1, DMART 10:1, RELIANCE 1:1 bonus, TCS 1:1 bonus, HDFCBANK 2:1)
- DuckDB store has `upsert_corporate_actions()` and `fetch_corporate_actions()` already implemented
- Full implementation plan exists at `docs/superpowers/plans/2026-03-14-corporate-actions.md`

**This workstream is an audit, not a full implementation.** We verify yfinance adjustments are correct for stocks that generated signals. If they're reliable, no further work needed. If broken, we flag specific stocks and decide whether the full implementation plan is worth executing.

### Implementation

#### Task 2.1: Build corporate actions reference dataset

Create `scripts/audit_corporate_actions.py` that:

1. For each of the 524 signal-generating symbols, fetch split/bonus history from yfinance:
   ```python
   import yfinance as yf
   ticker = yf.Ticker(f"{symbol}.NS")
   splits = ticker.splits  # pandas Series indexed by date
   ```
2. Save to `data/reference/corporate_actions_audit.csv` with columns:
   `symbol, date, action_type, ratio, source`
3. Rate limit: 1 req/sec, cache in `data/reference/yfinance_splits_cache.json`.

#### Task 2.2: Validate yfinance adjustments

For each corporate action found in Task 2.1:

1. Load raw OHLCV from parquet for the symbol.
2. Check for price discontinuities around the action date:
   ```python
   # If there was a 2:1 split on date D:
   # close[D-1] should be ~2x close[D] (pre-adjustment)
   # After yfinance adjustment: close[D-1] should be ~= close[D] (smooth)
   ratio = close_before / close_after
   expected_ratio = split_ratio  # e.g., 2.0 for 2:1 split
   error = abs(ratio - 1.0)  # should be ~0 if adjusted correctly
   ```
3. Flag any action where adjustment error > 5% as "suspect".
4. Cross-check the 6 seed entries in `config/corporate_actions.csv` against yfinance data.

#### Task 2.3: Check signal overlap

For each corporate action:
1. Check if any BREAKOUT signal was generated within ±5 trading days of the action date for that symbol.
2. If yes, verify the signal's entry_ref and stop prices look reasonable (not pre-split or post-split artifacts).
3. Output: list of signals potentially affected by corporate actions.

#### Task 2.4: Report

Output `reports/corporate_actions_audit/audit_report.json`:
```json
{
  "total_symbols_audited": 524,
  "total_corporate_actions_found": N,
  "splits": N,
  "bonuses": N,
  "adjustment_errors": [
    {"symbol": "...", "date": "...", "expected_ratio": 2.0, "actual_ratio": 1.98, "error_pct": 1.0}
  ],
  "signals_near_corporate_actions": [
    {"symbol": "...", "signal_date": "...", "action_date": "...", "action_type": "...", "days_apart": 3}
  ]
}
```
Also output `reports/corporate_actions_audit/all_actions.csv` with every corporate action found, and `reports/corporate_actions_audit/suspect_details.csv` with full price series ±10 days around each suspect adjustment.

**No decision.** Output all suspect adjustments, all signal overlaps, all ratios. The human decides whether to pursue the full implementation plan.

### Files to create/modify
- **Create:** `scripts/audit_corporate_actions.py`
- **Create:** `data/reference/corporate_actions_audit.csv`
- **Create:** `data/reference/yfinance_splits_cache.json`
- **Create:** `reports/corporate_actions_audit/audit_report.json`
- **No changes** to `src/` code.

---

## Workstream 3: Walk-Forward Validation

### Problem

The train/test split (2010-2021 / 2022-2026) is a single cut. Train EV 0.74R → Test EV 0.82R is encouraging but one split proves little. Walk-forward validation runs the same strategy across 12+ overlapping windows with no parameter changes, measuring consistency of edge.

**Key constraint:** This is a validation exercise, not an optimization. No parameters change between windows. The same locked config runs on every window. We're measuring: does the edge persist, or was 2010-2026 a lucky period?

### Implementation

#### Task 3.1: Create walk-forward runner

Create `scripts/walkforward_validation.py` that:

1. Defines rolling test windows (each 2 years, stepping 1 year):
   ```
   Window  1: 2011-01-01 → 2012-12-31
   Window  2: 2012-01-01 → 2013-12-31
   Window  3: 2013-01-01 → 2014-12-31
   ...
   Window 14: 2024-01-01 → 2025-12-31
   ```
   
   Use 2-year windows to get enough trades per window for statistical significance (~50+ fills minimum). Step by 1 year for maximum overlap.

2. For each window, call the existing backtest pipeline. The script should import from `portfolio_diagnostics.py`:
   - `load_inputs(start_date, end_date)` → returns bundle, signals, ohlcv
   - `filter_signals(...)` → returns filtered signals
   - `build_entry_controls(...)` → returns gate, size_mult
   - `simulate_portfolio_python(...)` → returns results
   
   These are all importable functions. Call them directly — do NOT shell out or manipulate sys.argv.

3. Extract from each window's result:
   - `fills`, `cagr`, `max_drawdown`, `calmar`, `win_rate`, `avg_r`, `payoff_ratio`
   - Equity curve (daily)
   
4. Use the same config for all windows. Load settings once via `load_settings()`.

5. Pass all the same exit params that `portfolio_diagnostics.py` currently passes:
   ```python
   breakeven_activation_r=settings.exit_strategy.breakeven_activation_r,
   time_stop_days=settings.exit_strategy.time_stop_days,
   partial_profit_r=settings.exit_strategy.partial_profit_r,
   trail_pct_bull=settings.exit_strategy.trail_pct_bull,
   trail_pct_bear=settings.exit_strategy.trail_pct_bear,
   ```

#### Task 3.2: Aggregate and report

Output `reports/walkforward/walkforward_summary.json` and print a table:

```
Window       Period              Fills  CAGR    MaxDD    Calmar  WR     AvgR
window_01    2011-01 → 2012-12    32   18.5%   -8.2%    2.26   65%   0.91R
window_02    2012-01 → 2013-12    41   22.1%   -6.4%    3.45   62%   0.78R
...
window_14    2024-01 → 2025-12    38   31.2%  -10.1%    3.09   64%   0.95R
─────────────────────────────────────────────────────────────────────────────
MEDIAN                            35   22.0%   -9.5%    2.50   63%   0.85R
WORST                             22   -2.1%  -18.3%    0.11   48%   0.12R
BEST                              52   45.2%   -5.1%    8.86   71%   1.42R
```

Also compute and print (all raw numbers, no interpretation):
- % of windows with positive CAGR
- % of windows with Calmar > 1.0
- % of windows with Calmar > 2.0
- Median, mean, min, max of: CAGR, MaxDD, Calmar, WR, AvgR, fills
- Std dev of fills across windows
- Max of all window MaxDDs
- Per-window trade count distribution (histogram buckets)
- Per-window monthly return series (for each window, output monthly returns as columns in a CSV)
- Cross-window correlation of monthly returns (do different windows have similar return patterns?)

#### Task 3.3: Equity curve overlay

Generate a combined chart data CSV (`reports/walkforward/equity_curves.csv`) with columns:
`date, window_id, equity`

This allows plotting all 14 equity curves overlaid to visually inspect consistency.

#### Task 3.4: Statistical significance

For each window, compute the **t-statistic** of trade R-multiples:
```python
from scipy import stats
t_stat, p_value = stats.ttest_1samp(r_multiples, 0.0)
```

Report t-stat and p-value for every window. Also report the number of trades per window (sample size matters for interpreting p-values).

### Output structure
```
reports/walkforward/
├── walkforward_summary.json      # All aggregate metrics (raw numbers only)
├── walkforward_table.csv         # Per-window metrics table
├── equity_curves.csv             # Daily equity for all windows
├── monthly_returns.csv           # Monthly returns per window
├── r_multiples_all.csv           # Every trade R-multiple with window_id
├── statistical_tests.csv         # t-stat, p-value, N per window
└── window_01/                    # Per-window detail (NOT optional — always output)
│   ├── summary.json
│   └── trade_pnl.csv
├── window_02/
...
```

Per-window `trade_pnl.csv` must include all columns from the main backtest trade output (symbol, signal_date, entry_date, exit_date, entry_price, exit_price, r_multiple, return_pct, holding_days, status, sector, etc.).

### Files to create/modify
- **Create:** `scripts/walkforward_validation.py`
- **Create:** `reports/walkforward/` (output directory)
- **No changes** to `src/` code or `scripts/portfolio_diagnostics.py`.

---

## Execution Order

1. **Workstream 1** (sector map) — run first, ~30 min for yfinance fetches + sweep
2. **Workstream 2** (corporate actions audit) — independent, ~20 min for yfinance fetches + validation
3. **Workstream 3** (walk-forward) — independent, ~15 min for 14 backtest runs

Workstreams 1-3 are independent and can run in parallel. Each produces its own report. Commit each workstream separately.

## Test Plan

- `uv run python -m py_compile scripts/build_sector_map.py` — syntax check
- `uv run python -m py_compile scripts/audit_corporate_actions.py` — syntax check
- `uv run python -m py_compile scripts/walkforward_validation.py` — syntax check
- `uv run pytest tests/ --ignore-glob='tests/test_ui_*.py'` — no regressions (these scripts don't modify src/)
- Manual verification: check output files exist and contain expected structure

## Assumptions

- yfinance `.NS` suffix works for all 524 signal-generating symbols (some may need `.BO` for BSE — handle gracefully with fallback)
- The 6 known test failures in the existing test suite (from the previous evaluation) are pre-existing and not caused by this work
- DuckDB signals table has complete coverage for 2010-2026 date range
- Each walk-forward window takes ~60 seconds to run (14 windows ≈ 15 min total)

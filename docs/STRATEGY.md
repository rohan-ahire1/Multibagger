# SEPA Scanner NSE — Canonical Strategy Reference

> **This is the single source of truth for the SEPA Scanner strategy.**
> Every AI session, every contributor, every code change MUST conform to this document.
> If the code disagrees with this document, the code is wrong — fix the code.
> If you need to change the strategy, update THIS document FIRST, then change the code.

**Strategy:** Minervini / SEPA-Inspired VCP Breakout Scanner
**Market:** NSE Cash (India), EOD only
**Benchmark:** NIFTYBEES
**Purpose:** Alert-only swing-trading scanner — no auto-trading, no order placement

---

## Table of Contents

1. [Philosophy and Scope](#1-philosophy-and-scope)
2. [Product Constraints](#2-product-constraints)
3. [Data Layer](#3-data-layer)
4. [Market Filter](#4-market-filter)
5. [Universe Selection](#5-universe-selection)
6. [Trend Template — 9 Conditions](#6-trend-template--9-conditions)
7. [Relative Strength (RS)](#7-relative-strength-rs)
8. [VCP Setup — 6 Conditions](#8-vcp-setup--6-conditions)
9. [Pivot and Near-Pivot](#9-pivot-and-near-pivot)
10. [WATCH Signal](#10-watch-signal)
11. [BREAKOUT Signal](#11-breakout-signal)
12. [Risk Framing](#12-risk-framing)
13. [Signal Ranking](#13-signal-ranking)
14. [Watchlist](#14-watchlist)
15. [Backtest Model](#15-backtest-model)
16. [UI Behavior](#16-ui-behavior)
17. [Complete Configuration Reference](#17-complete-configuration-reference)
18. [Indicator Implementation Details](#18-indicator-implementation-details)
19. [Invariants — Things That Must Never Change Without Updating This Document](#19-invariants--things-that-must-never-change-without-updating-this-document)
20. [Change Log](#20-change-log)
21. [Research Basis — Parameter Validation Guide](#21-research-basis--parameter-validation-guide)

---

## 1. Philosophy and Scope

This scanner implements Mark Minervini's SEPA (Specific Entry Point Analysis) methodology adapted for the Indian NSE cash market. The core thesis:

- **Stage 2 uptrends** are identified via a strict 9-condition trend template
- **Volatility Contraction Patterns (VCP)** are detected through price tightening, volume dry-up, and ATR contraction
- **Relative Strength** ensures we focus on market leaders outperforming the benchmark
- **Breakouts** are confirmed by price clearing the pivot on above-average volume
- **Risk** is framed per-trade with a structural stop and a maximum loss cap

The scanner produces two signal types:
- **WATCH**: Stock is in a confirmed Stage 2 uptrend, in a VCP setup, and near its pivot — worth monitoring
- **BREAKOUT**: Stock has cleared its pivot on volume — the actual entry trigger

### What This Project Is

- An EOD alert scanner for the NSE cash market
- A tool to identify SEPA-style setups and breakouts
- A backtesting framework to evaluate BREAKOUT signal quality
- A Streamlit UI for inspecting signals, watchlists, and backtests

### What This Project Is NOT

- Not an auto-trading system
- Not a portfolio manager or position sizer
- Not an intraday scanner
- Not connected to any broker API
- Not a general-purpose stock screener (the strategy is specific and intentional)

---

## 2. Product Constraints

| Constraint | Value |
|---|---|
| Market | NSE cash market only |
| Timeframe | Daily EOD only |
| Data source | Official NSE CM-UDiFF bhavcopy |
| Output | Alerts only (console + optional Telegram) |
| Runtime target | A few seconds for ~1,000-1,500 symbols on MacBook Air M2 |
| Compute engine | Polars for hot path, DuckDB for persistence |
| Benchmark | NIFTYBEES (fixed for market filter and RS calculations) |
| Corporate actions | Adjustment disabled by default; raw history used |
| Reliability | Rerunnable downloads, partition-safe upserts, non-fatal Telegram fallback |
| Artifact integrity | Bhavcopy ZIPs are SHA-256 hashed; re-downloads archive the previous file if the hash differs |
| Calendar correctness | Scans are gated on the NSE CM trading calendar (holidays fetched from NSE API and cached locally); `--force` overrides |

---

## 3. Data Layer

### 3.1 Data Source: NSE CM-UDiFF Bhavcopy

The scanner uses the official NSE merged-daily-reports API to fetch the CM-UDiFF bhavcopy zip.

**Discovery flow:**
1. Create a `requests` session and warm NSE cookies by loading `https://www.nseindia.com/`
2. For current-day discovery, call `https://www.nseindia.com/api/merged-daily-reports?key=favCapital`
3. Select the item named `CM-UDiFF Common Bhavcopy Final (zip)` and use its archive link
4. For backfill or fallback, try direct archive URLs using `F` first and `P` second when provisional downloads are allowed

**Caching:** Raw archives cached as `data/raw/YYYY-MM-DD.zip`. Downloads use a temp-file + SHA-256 + archive workflow: the response is written to a temp file, hashed, and if a previous file exists with a different hash it is renamed to `{stem}__{sha[:8]}.zip` before the new file replaces it.

**Parsing:** The zipped CSV is parsed with Polars, columns normalized, filtered to allowed series (default `[EQ]`), invalid rows dropped, deduplicated on `(date, symbol)`, and sorted by `symbol, date`.

**Trading Calendar:** The scan command fetches NSE CM trading holidays from `https://www.nseindia.com/api/holiday-master?type=trading`, cached to `data/cache/nse_holidays_trading.json` (max age 24h). If the scan date is not a CM trading day (weekend or holiday), the scan inserts a run with `status='skipped_non_trading_day'` and exits cleanly. The `--force` flag overrides this check. Backfill does not use the CM calendar — it relies on 404 responses for non-trading dates.

### 3.2 Storage: Parquet (OHLCV)

- Root: `data/ohlcv_parquet/`
- Partitioning: `year=YYYY/month=MM/ohlcv.parquet`
- Compression: zstd
- Upsert: only affected year/month partitions are rewritten (replace rows for incoming dates, then rewrite the compact partition file)

**OHLCV Schema:**

| Column | Type | Description |
|---|---|---|
| date | Date | Trading date |
| symbol | String | NSE symbol |
| series | String | NSE series (e.g. "EQ") |
| open | Float64 | Open price |
| high | Float64 | High price |
| low | Float64 | Low price |
| close | Float64 | Close / last traded price |
| volume | Int64 | Shares traded |
| turnover | Float64 | Turnover in INR (if available) |

### 3.3 Storage: DuckDB (Runs, Signals, Watchlist)

- File: `data/db/sepa.duckdb`

**Tables:**

```sql
runs(
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    scan_date DATE,
    market_filter_on BOOLEAN,
    num_symbols INTEGER,
    num_eligible INTEGER,
    num_watch INTEGER,
    num_breakout INTEGER,
    status TEXT,              -- 'success', 'error', 'skipped_non_trading_day'
    error TEXT,
    num_trend_pass BIGINT,
    num_rs_pass BIGINT,
    num_setup_pass BIGINT,
    num_near_pivot BIGINT,
    bhavcopy_sha256 TEXT,     -- SHA-256 of the bhavcopy zip used for this run
    bhavcopy_url TEXT         -- URL the bhavcopy was fetched from
)

signals(
    signal_date DATE,
    symbol TEXT,
    signal_type TEXT,      -- 'WATCH' or 'BREAKOUT'
    close DOUBLE,
    pivot DOUBLE,
    near_pivot DOUBLE,
    volume BIGINT,
    vol50 DOUBLE,
    vol_mult DOUBLE,
    rs_rank DOUBLE,
    range10 DOUBLE,
    voldry DOUBLE,
    atr_pct DOUBLE,
    adv50 DOUBLE,
    entry_ref DOUBLE,
    stop DOUBLE,
    r_per_share DOUBLE,
    created_at TIMESTAMP,
    UNIQUE(signal_date, symbol, signal_type)
)

watchlist_candidates(
    watch_date DATE,
    symbol TEXT,
    close DOUBLE,
    pivot DOUBLE,
    near_pivot DOUBLE,
    rs_rank DOUBLE,
    range10 DOUBLE,
    voldry DOUBLE,
    atr_pct DOUBLE,
    adv50 DOUBLE,
    vol50 DOUBLE,
    created_at TIMESTAMP,
    UNIQUE(watch_date, symbol)
)

downloads(
    download_id TEXT PRIMARY KEY,
    trade_date DATE,
    report_name TEXT,
    url TEXT,
    source TEXT,
    downloaded_at TIMESTAMP,
    http_status INTEGER,
    content_length BIGINT,
    etag TEXT,
    last_modified TEXT,
    file_path TEXT,
    sha256_zip TEXT,
    rows_total INTEGER,
    rows_after_series_filter INTEGER,
    rows_after_dedupe INTEGER,
    duplicates_dropped INTEGER,
    parse_notes TEXT
)

daily_diagnostics(
    diag_date DATE,
    symbol TEXT,
    eligible BOOLEAN,
    tt_pass BOOLEAN,
    tt1..tt9 BOOLEAN,         -- individual trend template conditions
    rs_rank DOUBLE,
    rs_pass BOOLEAN,
    setup_pass BOOLEAN,
    vcp1..vcp6 BOOLEAN,       -- individual VCP setup conditions
    contraction_count INT,    -- successive narrowing contractions in base (0-5)
    contraction_quality DOUBLE, -- avg T(n+1)/T(n) ratio (lower = better; null if count < 2)
    pivot DOUBLE,
    near_pivot DOUBLE,
    setup_active_streak INT,  -- consecutive days setup_pass=True
    pivot_age_days INT,       -- bars since pivot last changed
    watch_fail_mask INT,      -- bitmask: 1=TT 2=RS 4=setup 8=above_pivot 16=too_far 32=is_breakout
    breakout_fail_mask INT,   -- bitmask: 1=TT 2=RS 4=setup_prev 8=mkt_off 16=below_pivot 32=vol 64=not_first 128=extended
    created_at TIMESTAMP,
    UNIQUE(diag_date, symbol)
)
```

### 3.4 Signal Exports

Daily signals exported to `data/signals/YYYY-MM-DD_signals.parquet` (and optionally CSV).

---

## 4. Market Filter

The market filter determines whether the overall market environment is favorable for BREAKOUT signals. It uses the benchmark symbol (NIFTYBEES).

### Conditions — 3 Layers (ALL must be true on day `D`)

| Layer | # | Condition | Meaning |
|---|---|---|---|
| 1 — Trend | 1 | `close(D) > sma200(D)` | Benchmark price above its 200-day SMA |
| 1 — Trend | 2 | `sma50(D) > sma200(D)` | Benchmark 50-day SMA above 200-day SMA |
| 1 — Trend | 3 | `sma200(D) > sma200(D-22)` | Benchmark 200-day SMA is rising (slope positive over 22 days) |
| 2 — Cycle | 4 | `close(D) > ema21(D)` | Benchmark close above its 21-day EMA (cycle confirmation) |
| 3 — Breadth | 5 | `breadth_pct(D) >= require_breadth_pct` | % of eligible universe with close > SMA50 >= 50% |

### Behavior

- When `require_market_filter` is `true` (default): BREAKOUT signals are **suppressed** when the filter is OFF
- WATCH signals are **always allowed** regardless of market filter state
- Missing benchmark data on the scan date is a **fatal error** — the scan cannot proceed

### Config Keys

| Parameter | Config Key | Default |
|---|---|---|
| Benchmark EMA period | `market_filter.benchmark_ema_period` | 21 |
| Require breadth % | `market_filter.require_breadth_pct` | 0.50 |

### Implementation (exact code)

```python
def market_filter_on(frame, benchmark_symbol, scan_date, eligible_symbols) -> bool:
    row = frame.filter(
        (col("symbol") == benchmark_symbol) & (col("date") == scan_date)
    ).select("close", "sma50", "sma200", "sma200_lag20", "ema21").to_dicts()

    if not row:
        raise ValueError(f"Missing benchmark data for {benchmark_symbol} on {scan_date}")

    bm = row[0]

    # Layer 1 — Trend (3 conditions)
    layer1 = (
        bm["close"] > bm["sma200"]
        and bm["sma50"] > bm["sma200"]
        and bm["sma200"] > bm["sma200_lag20"]
    )

    # Layer 2 — Cycle confirmation
    layer2 = bm["close"] > bm["ema21"]

    # Layer 3 — Breadth
    eligible = frame.filter(
        (col("date") == scan_date) & col("symbol").is_in(eligible_symbols)
    )
    breadth_pct = eligible.filter(col("close") > col("sma50")).height / max(eligible.height, 1)
    layer3 = breadth_pct >= require_breadth_pct

    return layer1 and layer2 and layer3
```

---

## 5. Universe Selection

The universe determines which symbols are eligible for signal generation on day `D`.

### Eligibility Conditions (ALL must be true)

| # | Condition | Default |
|---|---|---|
| 1 | `history_count >= min_history_days` | 320 bars |
| 2 | `close(D) >= min_price_inr` | ₹50 |
| 3 | `adv50(D) >= min_adv50_inr` | ₹50,000,000 |
| 4 | `vol50(D) >= min_avg_volume_shares` | 200,000 shares |

### Post-Eligibility

1. Exclude the benchmark symbol (NIFTYBEES) from candidates
2. Sort remaining symbols by `adv50` descending
3. Keep top `max_symbols` (default 1500)

### Key Definitions

- `history_count` = number of bars available for that symbol within the loaded scan frame (computed as `pl.len().over("symbol")`). This is bounded by how much history is loaded, not the full store.
- `adv50` = 50-day simple moving average of `traded_value` where `traded_value = coalesce(turnover, close * volume)`
- `vol50` = 50-day simple moving average of `volume`

---

## 6. Trend Template — 9 Conditions

The trend template confirms a stock is in a Stage 2 uptrend. ALL 9 conditions must be true simultaneously on day `D` for `trend_template_pass = True`.

### The 9 Conditions

| # | Condition | Formula | Meaning |
|---|---|---|---|
| 1 | Close above 150-SMA | `close > sma150` | Price above intermediate MA |
| 2 | Close above 200-SMA | `close > sma200` | Price above long-term MA |
| 3 | 150-SMA above 200-SMA | `sma150 > sma200` | MAs properly stacked |
| 4 | 200-SMA rising | `sma200 > sma200.shift(22)` | Long-term trend rising (22-day slope) |
| 5 | 50-SMA above 150-SMA | `sma50 > sma150` | Short MA above intermediate |
| 6 | 50-SMA above 200-SMA | `sma50 > sma200` | Short MA above long-term |
| 7 | Close above 50-SMA | `close > sma50` | Price above short-term MA |
| 8 | Close above 52-week low threshold | `close >= low52 * (1 + min_above_52w_low_pct)` | At least 30% above 52-week low |
| 9 | Close near 52-week high | `close >= high52 * (1 - max_below_52w_high_pct)` | Within 25% of 52-week high |

### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Fast SMA | `trend_template.sma_fast` | 50 |
| Mid SMA | `trend_template.sma_mid` | 150 |
| Slow SMA | `trend_template.sma_slow` | 200 |
| 52-week lookback | `trend_template.lookback_52w` | 252 trading days |
| 200-SMA slope lookback | `trend_template.sma200_slope_lookback` | 22 trading days |
| Min above 52w low | `trend_template.min_above_52w_low_pct` | 0.30 (30%) |
| Max below 52w high | `trend_template.max_below_52w_high_pct` | 0.25 (25%) |

### Mathematical Expansion

With defaults:
- Condition 8: `close >= low52 * 1.30` — stock must be at least 30% above its 52-week low
- Condition 9: `close >= high52 * 0.75` — stock must be within 25% of its 52-week high

### SMA Computation

All SMAs use a simple rolling mean with `min_samples = window_size` (no partial windows):

```python
sma_expr(column, window) = pl.col(column).rolling_mean(window_size=window, min_samples=window)
```

### 52-Week High/Low

```python
high52 = pl.col("high").rolling_max(window_size=252, min_samples=252).over("symbol")
low52  = pl.col("low").rolling_min(window_size=252, min_samples=252).over("symbol")
```

These are computed over the **lookback_52w** window (252 trading days) and include the current day.

### Implementation

The trend template is computed as individual boolean columns (`tt_close_above_sma150`, etc.) and then combined with `pl.all_horizontal(...)` into a single `trend_template_pass` column.

---

## 7. Relative Strength (RS)

RS measures how a stock performs relative to the benchmark over multiple timeframes, then ranks all eligible symbols cross-sectionally.

### Step 1: Compute Returns

For each lookback period `L` in `[63, 126, 189, 252]`:

```
Ret_sym_L  = (close_today / close_L_days_ago) - 1
Ret_bm_L   = (benchmark_close_today / benchmark_close_L_days_ago) - 1
```

### Step 2: Compute Relative Performance

For each lookback period `L`:

```
Rel_L = (1 + Ret_sym_L) / (1 + Ret_bm_L) - 1
```

### Step 3: Compute RS Score (Weighted Sum)

```
RS_score = w_1 * Rel_63 + w_2 * Rel_126 + w_3 * Rel_189 + w_4 * Rel_252
```

With default weights: `RS_score = 0.4 * Rel_63 + 0.2 * Rel_126 + 0.2 * Rel_189 + 0.2 * Rel_252`

**CRITICAL:** The weights MUST sum to exactly 1.0. This is validated at config load time.

### Step 4: Compute RS Rank (Cross-Sectional Percentile)

```
rs_rank = 100 * (rank - 1) / (N - 1)
```

Where:
- `rank` is the `average`-method rank of the RS score among all eligible symbols on day `D`
- `N` is the total number of eligible symbols
- If `N == 0`: `rs_rank = null`
- If `N == 1`: `rs_rank = 100.0`

### RS Pass Condition

```
rs_pass = (rs_rank >= rs_rank_min)
```

Default `rs_rank_min = 70` — only the top 30th percentile passes.

### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Lookback periods | `relative_strength.lookbacks` | [63, 126, 189, 252] |
| Weights | `relative_strength.weights` | [0.4, 0.2, 0.2, 0.2] |
| Minimum RS rank | `relative_strength.rs_rank_min` | 70 |

### Important Details

- Ranking is **descending** by RS score: highest RS score receives `rs_rank` closest to 100
- RS is computed only for symbols in the eligible universe on scan day
- The benchmark symbol itself is excluded from ranking
- Returns are joined to the benchmark via date, so the benchmark's returns are available for every symbol
- `rs_rank` uses Polars `rank(method="average")` — tied scores receive the average of their ranks

---

## 8. VCP Setup — 6 Conditions

The VCP (Volatility Contraction Pattern) setup detects that a stock has entered a tightening consolidation phase suitable for a breakout. ALL 6 conditions must be true simultaneously on day `D` for `setup_pass = True`.

### The 6 Conditions

| # | Condition | Formula | Meaning |
|---|---|---|---|
| 1 | Price range tight | `range10 <= max_range10_pct` | 10-day range is small relative to price |
| 2 | Volume dried up | `voldry <= max_vol_dryup_ratio` | Recent volume lower than average |
| 3 | ATR trend declining | `atr_pct_sma10 < atr_pct_sma20` | Short-term ATR% below medium-term ATR% |
| 4 | ATR contracted | `atr_pct_sma10 / atr_pct_sma30 <= atr_contraction_factor` | 10d/30d ATR ratio shows contraction |
| 5 | Not over-extended | `close <= sma50 * (1 + max_extension_from_50sma_pct)` | Price not too far above 50-SMA |
| 6 | Shallow base | `base_depth <= base_max_depth_pct` | Base depth within acceptable range |

### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Max 10-day range | `setup_vcp.max_range10_pct` | 0.10 (10%) |
| Max volume dry-up ratio | `setup_vcp.max_vol_dryup_ratio` | 0.60 |
| ATR period | `setup_vcp.atr_period` | 14 |
| ATR contraction factor | `setup_vcp.atr_contraction_factor` | 0.75 |
| Max extension from 50-SMA | `setup_vcp.max_extension_from_50sma_pct` | 0.10 (10%) |
| Max base depth | `setup_vcp.base_max_depth_pct` | 0.35 (35%) |
| Base lookback | `setup_vcp.base_lookback` | 65 trading days |

### Indicator Definitions

**Range10 (10-day price range relative to close):**
```
range10 = (rolling_max(high, 10) - rolling_min(low, 10)) / close
```

**Volume Dry-Up (recent volume vs average):**
```
vol10 = sma(volume, 10)
vol50 = sma(volume, 50)
voldry = vol10 / vol50
```

**ATR% and ATR Contraction:**
```
atr = wilder_atr(high, low, close, period=14)
atr_pct = atr / close
atr_pct_sma10 = sma(atr_pct, 10)
atr_pct_sma20 = sma(atr_pct, 20)
atr_pct_sma30 = sma(atr_pct, 30)
```

Condition 3: `atr_pct_sma10 < atr_pct_sma20` — short-term volatility declining
Condition 4: `atr_pct_sma10 / atr_pct_sma30 <= 0.75` — 10d/30d ATR ratio shows contraction

**Base Depth:**
```
base_high = rolling_max(high, base_lookback)    # includes current day
base_low  = rolling_min(low, base_lookback)     # includes current day
base_depth = (base_high - base_low) / base_high
```

### Implementation

The setup is computed as individual boolean columns (`range10_ok`, `voldry_ok`, `atr_trend_ok`, `atr_contract_ok`, `extension_ok`, `base_depth_ok`) and then combined with `pl.all_horizontal(...)` into a single `setup_pass` column.

### 8.1 Contraction Sequence Detection

The 6 conditions above detect a single-day tightening state. This subsection adds **contraction counting** — detecting successive narrowing cycles (T1 > T2 > T3) that are the hallmark of a true VCP pattern.

#### Algorithm

Operates on the `range10` time series within the `base_lookback` window (default 65 bars):

1. **Smooth** `range10` with a short SMA (default 5 bars) to filter daily noise
2. **Find local peaks** — bars where the smoothed range10 derivative goes from positive to negative (expansion maxima), filtered by a minimum height (`contraction_min_peak_pct`, default 0.02)
3. **Count successive narrowing peaks** — walk peaks chronologically; each peak lower than the previous counts as a contraction. Break the chain when a peak is equal or higher
4. **Compute quality** — average ratio of T(n+1)/T(n) across the chain (lower = better; null if count < 2)

#### New Columns

| Column | Type | Description |
|--------|------|-------------|
| `contraction_count` | Int32 | Number of successive narrowing contractions detected in the base window (0-5 typical) |
| `contraction_quality` | Float64 | Average ratio T(n+1)/T(n) across the contraction chain (lower = tighter; null if count < 2) |

#### Gating Behavior

- When `contraction_min_count = 0`: contraction columns are **informational only** — `setup_pass` is unchanged
- When `contraction_min_count > 0` (default 2): a new condition `contraction_ok = (contraction_count >= contraction_min_count)` is added to the `setup_pass` AND gate

#### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Smooth window | `setup_vcp.contraction_smooth_window` | 5 |
| Min peak height | `setup_vcp.contraction_min_peak_pct` | 0.02 (2%) |
| Min contraction count | `setup_vcp.contraction_min_count` | 2 (hard gate) |

#### Implementation

Uses `map_groups` per symbol over the last `base_lookback` bars of `range10`. The inner function processes ~65 floats per call — trivial cost for ~1500 symbols.

---

## 9. Pivot and Near-Pivot

### Pivot

The pivot is the highest high over the `base_lookback` window, **excluding the current day**:

```
pivot(D) = rolling_max(high, base_lookback).shift(1)
```

The `.shift(1)` is critical — it ensures the current day's high cannot define its own pivot. This means the pivot represents the resistance level from the prior base that price must clear.

`base_lookback` default: 65 trading days.

### Near-Pivot

```
near_pivot(D) = (pivot(D) - close(D)) / pivot(D)
```

This represents how far below the pivot the current close is, as a fraction of the pivot price.

- `near_pivot = 0` means close is exactly at the pivot
- `near_pivot < 0` means close is above the pivot (has broken out)
- `near_pivot > 0` means close is below the pivot

### Structural Stop

```
struct_stop = rolling_min(low, 10)
```

The lowest low over the last 10 trading days (includes current day). Used as the initial structural stop level.

---

## 10. WATCH Signal

A WATCH signal indicates a stock is in a confirmed Stage 2 uptrend, in a VCP setup, and approaching its pivot — worth monitoring for a potential breakout.

### Conditions (ALL must be true on day `D`)

| # | Condition | Details |
|---|---|---|
| 1 | Trend template pass | `trend_template_pass(D) == True` — all 9 trend conditions met |
| 2 | RS pass | `rs_rank(D) >= rs_rank_min` — in the top RS percentile |
| 3 | Setup pass | `setup_pass(D) == True` — all 6 VCP conditions met **on day D** |
| 4 | Below pivot | `near_pivot(D) >= 0` — close must be at or below the pivot |
| 5 | Near pivot | `near_pivot(D) <= pre_alert_proximity_pct` — within 3% of pivot |
| 6 | Not a breakout | The same symbol/date is NOT flagged as a BREAKOUT |

**Default proximity:** `pre_alert_proximity_pct = 0.03` (3%)

### Key Difference from BREAKOUT

- WATCH uses `setup_pass(D)` — the setup must be active on the signal day itself
- BREAKOUT uses `setup_pass(D-1)` — the setup must have been active the day before

### Exclusion Rule

If a symbol qualifies for both WATCH and BREAKOUT on the same day, it is classified as BREAKOUT only. The WATCH mask explicitly excludes breakout signals: `~breakout_mask`.

---

## 11. BREAKOUT Signal

A BREAKOUT signal indicates a stock has cleared its pivot on volume — the actual entry trigger.

### Conditions (ALL must be true on day `D`)

| # | Condition | Formula | Details |
|---|---|---|---|
| 1 | Trend template pass | `trend_template_pass(D)` | All 9 trend conditions met |
| 2 | RS pass | `rs_rank(D) >= rs_rank_min` | Top RS percentile |
| 3 | Setup pass **previous day** | `setup_pass(D-1)` | VCP conditions met **yesterday** |
| 4 | Market filter | `market_on` or `not require_market_filter` | Market environment favorable |
| 5 | Price above pivot | `close(D) >= pivot(D) * (1 + breakout_buffer_pct)` | Close cleared the pivot |
| 6 | Volume surge | `volume(D) >= vol50(D) * breakout_vol_mult` | Volume at least 1.5x average |
| 7 | First-day breakout | `close(D-1) < pivot(D) * (1 + breakout_buffer_pct)` | Yesterday's close was below pivot |
| 8 | Not over-extended | `close(D) <= sma50(D) * (1 + max_extension_breakout_pct)` | Price not too far above 50-SMA on breakout day |

### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Require market filter | `breakout.require_market_filter` | true |
| Breakout buffer | `breakout.breakout_buffer_pct` | 0.003 (0.3%) |
| Volume multiple | `breakout.breakout_vol_mult` | 1.5 |
| Max breakout extension | `breakout.max_extension_breakout_pct` | 0.15 (15%) |

### Critical Design Decisions

1. **Setup uses D-1:** The setup gate intentionally checks the *previous day's* setup state. This is because on the breakout day itself, the range expansion and volume surge would likely invalidate the VCP tightening conditions. We want to confirm the stock *was* in a tight pattern before the breakout.

2. **First-day only:** Condition 7 ensures the signal fires only on the initial breakout day. If the stock continues above the pivot the next day, it does NOT generate a second BREAKOUT signal. This is implemented by checking `prev_close < pivot * breakout_buffer`.

3. **Market filter gating:** When `require_market_filter = true` (default), breakout signals are suppressed in unfavorable market environments. This prevents entries during adverse conditions.

### BREAKOUT Signal Metadata

For BREAKOUT signals, the `range10`, `voldry`, and `atr_pct` columns are populated with the **previous day's values** (D-1), not the breakout day's values. This is intentional — the setup metrics should reflect the pre-breakout contraction, not the breakout-day expansion.

```python
breakout_signals = enriched.filter(breakout_mask).with_columns(
    pl.col("range10_prev").alias("range10"),
    pl.col("voldry_prev").alias("voldry"),
    pl.col("atr_pct_prev").alias("atr_pct"),
)
```

---

## 12. Risk Framing

Every signal (WATCH and BREAKOUT) includes risk framing for position sizing reference.

### Formulas

```
entry_ref      = pivot * (1 + entry_buffer_pct)
struct_stop    = rolling_min(low, 10)          # 10-day lowest low
max_loss_stop  = entry_ref * (1 - max_stop_pct)
stop           = max(struct_stop, max_loss_stop)
r_per_share    = entry_ref - stop
```

### Default Parameters

| Parameter | Config Key | Default |
|---|---|---|
| Entry buffer | `risk.entry_buffer_pct` | 0.003 (0.3%) |
| Max stop loss | `risk.max_stop_pct` | 0.08 (8%) |
| Profit target | `risk.target_r` | null (no fixed target) |

### Stop Logic

- `struct_stop` is the structural/technical stop based on the 10-day low
- `max_loss_stop` is the maximum acceptable loss stop (8% below entry)
- The actual stop is the **higher** of the two — this ensures the stop is never looser than the max allowed loss, but uses a tighter structural level when available
- If `struct_stop` is null (insufficient data), `max_loss_stop` is used alone via `pl.coalesce`

**BREAKOUT vs WATCH stop difference:**
- For **WATCH** signals, `struct_stop` uses the 10-day lowest low including the current day (`rolling_min(low, 10)`)
- For **BREAKOUT** signals, `struct_stop` uses the 10-day lowest low as of D-1 (`rolling_min(low, 10).shift(1)`), excluding the breakout day. This avoids breakout-day gap/noise from distorting the stop level.

### Discard Rule

Signals with `r_per_share <= 0` are **discarded**. This means the stop would be at or above the entry price, making the trade invalid.

---

## 13. Signal Ranking

Signals are ranked separately by type (WATCH and BREAKOUT) and truncated.

### Sort Order

```
1. signal_type     ASC    (groups BREAKOUT before WATCH alphabetically)
2. rs_rank         DESC   (strongest relative strength first)
3. vol_mult        DESC   (highest volume multiple first)
4. range10         ASC    (tightest consolidation first)
```

### Truncation

Within each `signal_type`, keep only the top `max_alerts_per_day` (default 10).

### Implementation

```python
frame.sort(
    by=["signal_type", "rs_rank", "vol_mult", "range10"],
    descending=[False, True, True, False],
).group_by("signal_type", maintain_order=True).head(max_alerts_per_day)
```

---

## 14. Watchlist

The watchlist captures all stocks that pass the core filters on a given day, regardless of whether they are near the pivot or generating signals.

### Watchlist Candidate Conditions (ALL on day `D`)

1. `trend_template_pass(D) == True`
2. `rs_pass(D) == True`
3. `setup_pass(D) == True`

### Ranking

```
1. near_pivot    ASC    (closest to pivot first)
2. rs_rank       DESC   (strongest RS first)
3. adv50         DESC   (most liquid first)
4. symbol        ASC    (alphabetical tiebreaker)
```

### Truncation

Keep top `max_candidates` (default 300).

### Columns

| Column | Type | Description |
|---|---|---|
| watch_date | Date | Scan date |
| symbol | String | NSE symbol |
| close | Float64 | Close price |
| pivot | Float64 | Pivot level |
| near_pivot | Float64 | Distance from pivot (fraction) |
| rs_rank | Float64 | RS percentile rank |
| range10 | Float64 | 10-day range |
| voldry | Float64 | Volume dry-up ratio |
| atr_pct | Float64 | ATR as % of price |
| adv50 | Float64 | 50-day average daily value |
| vol50 | Float64 | 50-day average volume |

---

## 15. Backtest Model

> For the complete backtesting specification including execution modeling, transaction costs, portfolio simulation, and statistical validation, see `docs/BACKTESTING.md`. This section is the summary only.

The backtester simulates trades from persisted BREAKOUT signals to evaluate signal quality.

### 15.1 Scope

- **V1 backtests BREAKOUT signals only** — WATCH signals are not backtested
- Consumes persisted signals from DuckDB and OHLCV from Parquet
- On-demand computation (no persisted backtest runs)
- Independent trade evaluation (no portfolio simulation or capital allocation)

### 15.2 Entry Logic

A BREAKOUT signal on date `D` becomes actionable starting on trading day `D+1`.

**Entry rules (evaluated sequentially over pending window):**

1. **Gap-through entry:** If next-day `open >= entry_ref` → fill at `open` price
2. **Intraday trigger:** If any pending day's `high >= entry_ref` → fill at `entry_ref`
3. **Expiration:** If the pending window expires without either trigger → mark as `not_filled`

**Default pending window:** 5 trading days

### 15.3 Exit Logic

Once a trade is filled:

| Exit Type | Condition | Exit Price |
|---|---|---|
| Gap-below stop | `open <= stop` (checked before intraday) | `open` price |
| Gap-above target | `open >= target_price` (checked before intraday) | `open` price |
| Stop hit | `low <= stop` | `stop` price |
| Target hit | `high >= target_price` | `target_price` |
| Same-day stop + target | Both stop and target touched on same candle | `stop` price (assume worse outcome) |
| Time stop | `holding_days == max_hold_days` | `close` price |

**Gap checks are not performed on the entry day itself** (only from the next day onward).
For gap-open exits, MAE/MFE may include the open price itself, but not the later intraday high/low from that exit bar.

### 15.4 Trade Metrics

For each closed trade:

```
return_pct = ((exit_price - entry_price) / entry_price) * 100
r_multiple = (exit_price - entry_price) / (entry_price - stop)
```

If OHLCV history ends after entry but before a normal exit, the trade is force-closed at the last available close, keeps status `incomplete_data`, and still participates in closed-trade metrics.

### 15.5 Overlap Rule

If a later BREAKOUT signal appears for a symbol while a prior trade in the same symbol is still open, the later signal is **skipped** (marked `skipped_overlap`). A signal on the prior trade's exit date remains eligible, because the earlier trade has already closed by the time the next session begins. This keeps V1 behavior deterministic.

### 15.6 Default Parameters

| Parameter | Default |
|---|---|
| Pending entry window | 5 trading days |
| Max holding period | 90 trading days |
| Profit target | None (no fixed target; from `risk.target_r` in config) |
| Exit mode | V2_TRAIL_21EMA (trailing 21-EMA stop after +1R) |
| Optional target R-multiple | CLI `--target-r` overrides config default |

### 15.7 Summary Metrics

| Metric | Formula |
|---|---|
| total_signals | Count of all signals in range |
| filled_trades | Count of trades with an entry |
| not_filled_signals | Count with status `not_filled` |
| stopped_trades | Count with status `stopped` |
| target_exits | Count with status `target` |
| time_stop_exits | Count with status `time_stop` |
| win_rate | winners / closed_trades |
| average_return_pct | mean(return_pct) of closed trades |
| average_r | mean(r_multiple) of closed trades |
| median_r | median(r_multiple) of closed trades |
| expectancy | Same as average_r in V1 |
| payoff_ratio | avg_win_r / abs(avg_loss_r) |
| cumulative_r | sum(r_multiple) of closed trades |
| max_drawdown | min(drawdown) on equity curve |
| longest_losing_streak | max consecutive losing trades (r < 0) |

### 15.8 Equity Curve

The equity curve tracks cumulative R on a fixed 1R-per-trade basis:

```
cumulative_r(i) = cumulative_r(i-1) + r_multiple(i)
running_peak(i) = max(running_peak(i-1), cumulative_r(i))
drawdown(i)     = cumulative_r(i) - running_peak(i)
```

Trades are sorted by `(exit_date, symbol, signal_date)` before computing the curve.

### 15.9 Trade Statuses

| Status | Meaning |
|---|---|
| `not_filled` | Pending window expired without entry trigger |
| `stopped` | Hard stop hit |
| `target` | Profit target hit |
| `time_stop` | Max holding period reached |
| `skipped_overlap` | Same-symbol trade already open |
| `incomplete_data` | Insufficient OHLCV data after signal; if entry already occurred, the trade is force-closed at the last available close |

### 15.10 Backtest V2 Exit Models (Optional)

V2 exit models add trailing stop discipline to the backtest without changing signal generation. The entry logic, overlap rule, and signal selection remain identical to V1 — only the exit path differs.

**Exit modes:**

| Mode | Key | Description |
|---|---|---|
| V1 Fixed | `v1_fixed` | Original V1 behavior: fixed initial stop, time stop, optional target |
| V2 Trail 10D Low | `v2_trail_10d` | After +1R profit, trail stop to 10-day rolling low |
| V2 Trail 21-EMA | `v2_trail_21ema` | After +1R profit, trail stop to 21-day EMA of close |

**Trail activation:**
- The trailing stop activates when `high >= entry_price + trail_activation_r * initial_risk` (default `trail_activation_r = 1.0`)
- Once activated, the stop immediately moves to at least break-even (`stop = max(stop, entry_price)`)
- Activation is permanent for the duration of the trade

**Trailing stop update rules (critical for avoiding lookahead bias):**
- The stop used for day `t` exit checks was computed from data through day `t-1`
- After processing day `t`'s bar (checking exits, tracking MAE/MFE), the trailing stop is updated using day `t`'s data
- This updated stop becomes active on day `t+1`
- The stop never decreases — `active_stop = max(active_stop, trail_value)`

**V2_TRAIL_10D specifics:**
```
trail_value = min(low[t], low[t-1], ..., low[t-9])  # 10-day rolling low
active_stop = max(active_stop, trail_value)           # ratchet up only
```
The low buffer accumulates from entry; before 10 bars are available, uses all available lows.

**V2_TRAIL_21EMA specifics:**
```
ema21(0) = close(entry_day)                                    # seed
ema21(t) = alpha * close(t) + (1 - alpha) * ema21(t-1)        # alpha = 2/22
active_stop = max(active_stop, ema21(t))                        # ratchet up only
```
The EMA is seeded from entry-day close and warms up over the holding period.

**Pre-activation indicator tracking:**
- Both V2 modes maintain their indicator state (low buffer / EMA) from entry day onward, even before trail activation
- This ensures the indicator has warm-up data when activation occurs

### 15.11 MAE/MFE Metrics

Every closed trade includes excursion metrics regardless of exit mode:

```
MAE_price = min(low) during holding period
MFE_price = max(high) during holding period
MAE_R     = (entry_price - MAE_price) / initial_risk    # positive = adverse
MFE_R     = (MFE_price - entry_price) / initial_risk    # positive = favorable
giveback  = (MFE_R - exit_R) / MFE_R                    # fraction of MFE given back
```

These metrics measure entry quality (low MAE = good entries) and exit quality (low giveback = good exits) independently of R-multiple outcomes.

### 15.12 Metric Categories

**Trade-quality metrics** (valid without portfolio simulation):
- Expectancy (avg R), win rate, payoff ratio, cumulative R, max drawdown (R), longest losing streak
- MAE/MFE averages, average giveback

**Portfolio metrics** (require a portfolio simulation with position sizing):
- Equity curve in ₹, CAGR, exposure, max concurrent positions, turnover

CAGR and capital curves are **not shown** unless a portfolio simulation is explicitly enabled, because serially compounding overlapping trades produces misleading results.

---

## 16. UI Behavior

React + Vite terminal app ("SEPA Terminal") served locally. FastAPI backend wraps the existing Python scanner and backtest engines.

### Workspaces

| Key | Workspace | Purpose |
|-----|-----------|---------|
| 1 | **Market Scanner** | Daily signal triage — sector heatmap, breadth panel, signal/watchlist table, setup detail card with chart |
| 2 | **Analysis** | Deep-dive on a single symbol — multi-pane chart (price + RS + volume, all vertically resizable), trend template, VCP metrics, sector context, fundamentals, backtest history, trade thesis |
| 3 | **Portfolio** | Position tracker (shortlisted → watching → open → managing → closed) with portfolio stats |
| 4 | **Backtest** | Full backtest dashboard — param bar with date range (data from 2010) and quick presets (1Y/3Y/5Y/10Y/All), stats summary, equity curve + drawdown, sortable trade list, per-trade chart with markers |

### Signal Markers on Charts

Six color-coded markers rendered below candles via Lightweight Charts v5 `createSeriesMarkers()`. Present on **all** chart instances (W1, W2, W4):

| Marker | Color | Text | Meaning |
|--------|-------|------|---------|
| Watch | #f59e0b (amber) | W | WATCH signal date |
| Breakout | #22c55e (green) | B | BREAKOUT signal date |
| Entry | #3b82f6 (blue) | E | Trade entry filled |
| Win Exit | #10b981 (emerald) | X+ | Closed with positive R |
| Loss Exit | #ef4444 (red) | X- | Closed with negative R |
| Not Filled | #6b7280 (gray) | NF | Signal expired unfilled |

On weekly/monthly charts, markers are deduplicated — one per type per candle. Charts auto-zoom to ±2 years around the latest signal when markers exist.

### Chart Panels

All chart panes are vertically resizable via drag separators (`react-resizable-panels`). Price charts auto-fill their container using a ResizeObserver that tracks both width and height.

### Data Range

OHLCV data is available from **2010-01-01**. The OHLCV API returns all available data by default (no date filter). Backtest defaults to the full range; quick presets (1Y/3Y/5Y/10Y/All) are provided in the param bar.

### Cross-Navigation

- W1 → W2: selecting a setup and pressing `2` opens deep analysis
- W2 → W4: clicking a trade in the BacktestHistory card navigates to W4 with that trade selected
- W4 → W2: `Escape` returns to previous workspace

### API Layer

FastAPI backend mounted at `/api`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/scanner/dates` | GET | Available scan dates |
| `/api/scanner/{date}/signals` | GET | Signals for a scan date |
| `/api/scanner/{date}/watchlist` | GET | Watchlist candidates |
| `/api/analysis/{symbol}/signal-history` | GET | Symbol's signals + backtest trades |
| `/api/ohlcv/{symbol}` | GET | Full OHLCV history (from 2010) |
| `/api/backtest/run` | POST | Run backtest with params |
| `/api/backtest/last` | GET | Last cached backtest result |

---

## 17. Complete Configuration Reference

### 17.1 `config/config.yaml`

```yaml
market:
  timezone: "Asia/Kolkata"
  benchmark_symbol: "NIFTYBEES"
  scan_time_ist: "16:10"

data:
  redownload_days: 5
  min_history_days: 320
  bhavcopy_api_key: "favCapital"
  http_timeout_sec: 30
  max_retries: 5
  apply_corporate_actions_adjustment: false

universe:
  allowed_series: ["EQ"]
  min_price_inr: 50
  min_adv50_inr: 50000000
  min_avg_volume_shares: 200000
  max_symbols: 1500

market_filter:
  benchmark_ema_period: 21
  require_breadth_pct: 0.50

trend_template:
  sma_fast: 50
  sma_mid: 150
  sma_slow: 200
  lookback_52w: 252
  sma200_slope_lookback: 22
  min_above_52w_low_pct: 0.30
  max_below_52w_high_pct: 0.25

relative_strength:
  lookbacks: [63, 126, 189, 252]
  weights: [0.4, 0.2, 0.2, 0.2]
  rs_rank_min: 70

setup_vcp:
  base_lookback: 65
  max_range10_pct: 0.10
  max_vol_dryup_ratio: 0.60
  atr_period: 14
  atr_contraction_factor: 0.75
  max_extension_from_50sma_pct: 0.10
  base_max_depth_pct: 0.35
  pre_alert_proximity_pct: 0.03
  contraction_smooth_window: 5
  contraction_min_peak_pct: 0.02
  contraction_min_count: 2

breakout:
  require_market_filter: true
  breakout_buffer_pct: 0.003
  breakout_vol_mult: 1.5
  max_extension_breakout_pct: 0.15

risk:
  entry_buffer_pct: 0.003
  max_stop_pct: 0.08
  target_r: null

alerts:
  max_alerts_per_day: 10
  send_watch_alerts: true
  send_breakout_alerts: true
  channels: ["telegram", "console"]

watchlist:
  max_candidates: 300
```

### 17.2 Full Parameter Reference Table

| Section | Parameter | Type | Default | Description |
|---|---|---|---|---|
| **market** | timezone | string | "Asia/Kolkata" | Market timezone |
| | benchmark_symbol | string | "NIFTYBEES" | Benchmark for RS and market filter |
| | scan_time_ist | string | "16:10" | Expected scan time (IST) |
| **data** | redownload_days | int | 5 | Days to refresh on update |
| | min_history_days | int | 320 | Min bars required for a symbol |
| | bhavcopy_api_key | string | "favCapital" | NSE API key for bhavcopy discovery |
| | http_timeout_sec | int | 30 | HTTP request timeout |
| | max_retries | int | 5 | Max download retries |
| | apply_corporate_actions_adjustment | bool | false | Enable split/bonus adjustment |
| **universe** | allowed_series | list[str] | ["EQ"] | Allowed NSE series codes |
| | min_price_inr | float | 50 | Minimum price in ₹ |
| | min_adv50_inr | float | 50,000,000 | Min 50-day avg daily value in ₹ |
| | min_avg_volume_shares | int | 200,000 | Min 50-day avg volume |
| | max_symbols | int | 1500 | Max universe size |
| **market_filter** | benchmark_ema_period | int | 21 | EMA period for benchmark cycle confirmation |
| | require_breadth_pct | float | 0.50 | Min % of universe with close > SMA50 |
| **trend_template** | sma_fast | int | 50 | Fast SMA period |
| | sma_mid | int | 150 | Mid SMA period |
| | sma_slow | int | 200 | Slow SMA period |
| | lookback_52w | int | 252 | 52-week lookback in trading days |
| | sma200_slope_lookback | int | 22 | 200-SMA slope lookback |
| | min_above_52w_low_pct | float | 0.30 | Min % above 52-week low |
| | max_below_52w_high_pct | float | 0.25 | Max % below 52-week high |
| **relative_strength** | lookbacks | list[int] | [63, 126, 189, 252] | RS return periods |
| | weights | list[float] | [0.4, 0.2, 0.2, 0.2] | RS period weights (must sum to 1.0) |
| | rs_rank_min | float | 70 | Min RS rank percentile |
| **setup_vcp** | base_lookback | int | 65 | Pivot and base lookback window |
| | max_range10_pct | float | 0.10 | Max 10-day range (10%) |
| | max_vol_dryup_ratio | float | 0.60 | Max vol dry-up ratio |
| | atr_period | int | 14 | ATR period for Wilder smoothing |
| | atr_contraction_factor | float | 0.75 | ATR contraction threshold (10d/30d ratio) |
| | max_extension_from_50sma_pct | float | 0.10 | Max extension above 50-SMA (10%) |
| | base_max_depth_pct | float | 0.35 | Max base depth (35%) |
| | pre_alert_proximity_pct | float | 0.03 | Max near_pivot for WATCH (3%) |
| | contraction_min_count | int | 2 | Min successive contractions required |
| **breakout** | require_market_filter | bool | true | Gate BREAKOUTs on market filter |
| | breakout_buffer_pct | float | 0.003 | Breakout buffer above pivot (0.3%) |
| | breakout_vol_mult | float | 1.5 | Min volume multiple for breakout |
| | max_extension_breakout_pct | float | 0.15 | Max extension above 50-SMA on breakout day (15%) |
| **risk** | entry_buffer_pct | float | 0.003 | Entry buffer above pivot (0.3%) |
| | max_stop_pct | float | 0.08 | Max stop loss (8%) |
| | target_r | float \| None | null | Default profit target in R-multiples; CLI `--target-r` overrides; null = no fixed target |
| **alerts** | max_alerts_per_day | int | 10 | Max alerts per signal type |
| | send_watch_alerts | bool | true | Enable WATCH alerts |
| | send_breakout_alerts | bool | true | Enable BREAKOUT alerts |
| | channels | list[str] | ["telegram", "console"] | Alert channels |
| **watchlist** | max_candidates | int | 300 | Max watchlist candidates |

### 17.3 Environment Variables

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (optional) |
| `POLARS_MAX_THREADS` | Polars thread count (default 4) |
| `SEPA_DATA_DIR` | Override data directory |

---

## 18. Indicator Implementation Details

This section documents the exact mathematical implementation of each indicator as it exists in the code. Any change to these formulas is a strategy change and must be reflected here.

### 18.1 SMA (Simple Moving Average)

```python
sma(column, window) = rolling_mean(column, window_size=window, min_samples=window)
```

- No partial windows — returns null until `window` bars are available
- Used for: sma50, sma150, sma200, vol10, vol50, adv50, atr_pct_sma10, atr_pct_sma20, atr_pct_sma30

### 18.2 ATR (Wilder's Average True Range)

**True Range:**
```
TR(0) = high(0) - low(0)                              # first bar
TR(i) = max(
    high(i) - low(i),
    abs(high(i) - close(i-1)),
    abs(low(i) - close(i-1))
)                                                       # subsequent bars
```

**Wilder Smoothing:**
```
ATR(period-1) = mean(TR[0:period])                     # initial: simple average of first `period` TRs
ATR(i) = ((ATR(i-1) * (period - 1)) + TR(i)) / period  # subsequent: EMA-like smoothing
```

- Returns `null` for the first `period - 1` bars
- Period default: 14
- Implemented as pure Polars `ewm_mean(alpha=1/period, adjust=False)` per symbol (same recurrence, EWM-seeded instead of SMA-seeded; divergence < 0.01% after warmup)

**ATR%:**
```
atr_pct = atr / close
```

**ATR% Smoothed (30-day):**
```
atr_pct_sma30 = sma(atr_pct, 30)
```

30-day SMA of ATR percentage. Used in the VCP ATR contraction condition as the denominator of the 10d/30d ATR ratio (`atr_pct_sma10 / atr_pct_sma30 <= factor`).

### 18.7 EMA21 (Benchmark)

**21-day EMA of benchmark close:**
```
ema21 = ewm_mean(close, span=21, adjust=False)    # alpha = 2/22
```

Used in the market filter Layer 2 (cycle confirmation): benchmark close must be above its 21-day EMA.

### 18.3 Volume Indicators

**Traded Value:**
```
traded_value = coalesce(turnover, close * volume)
```

**Volume SMAs:**
```
vol10 = sma(volume, 10)
vol50 = sma(volume, 50)
adv50 = sma(traded_value, 50)
```

**Volume Dry-Up:**
```
voldry = vol10 / vol50
```

**Volume Multiple (for breakout day):**
```
vol_mult = volume / vol50
```

### 18.4 Tightness Indicators

**Range10:**
```
range10 = (rolling_max(high, 10) - rolling_min(low, 10)) / close
```
Both rolling max and min use `min_samples=10` and are computed over `symbol`.

**Base High/Low/Depth:**
```
base_high  = rolling_max(high, base_lookback)      # includes current day
base_low   = rolling_min(low, base_lookback)       # includes current day
base_depth = (base_high - base_low) / base_high
```
`base_lookback` default: 65.

### 18.5 Highs and Lows

**52-Week High/Low:**
```
high52 = rolling_max(high, lookback_52w)   # default 252, includes current day
low52  = rolling_min(low, lookback_52w)    # default 252, includes current day
```

**Pivot:**
```
pivot = rolling_max(high, base_lookback).shift(1)   # excludes current day
```

**Near-Pivot:**
```
near_pivot = (pivot - close) / pivot
```

### 18.6 Relative Strength

See [Section 7](#7-relative-strength-rs) for the full RS computation.

---

## 19. Invariants — Things That Must Never Change Without Updating This Document

These are hard constraints. Any AI session or contributor that changes these MUST update this strategy document first.

### Mathematical Invariants

1. **Trend template is exactly 9 conditions** — no more, no less
2. **VCP setup is exactly 6 conditions** (plus contraction gate when `contraction_min_count > 0`, default 2). ATR contraction uses 10d/30d ATR ratio: `atr_pct_sma10 / atr_pct_sma30 <= factor`
3. **Market filter is exactly 3 layers (5 conditions)** on the benchmark: Layer 1 (trend: close>SMA200, SMA50>SMA200, SMA200 rising), Layer 2 (cycle: close>EMA21), Layer 3 (breadth: % close>SMA50 >= 50%)
4. **RS weights must sum to exactly 1.0**
5. **RS rank uses `average` method ranking with the formula `100 * (rank - 1) / (N - 1)`**
6. **Pivot excludes the current day** via `.shift(1)`
7. **BREAKOUT uses `setup_pass(D-1)`**, not `setup_pass(D)`
8. **BREAKOUT metadata (range10, voldry, atr_pct) uses previous day's values**
9. **WATCH uses `setup_pass(D)`** and excludes symbols that are also BREAKOUTs
10. **Signals with `r_per_share <= 0` are discarded**
11. **Stop = max(struct_stop, max_loss_stop)** — always the tighter stop
12. **First-day-only breakout**: `prev_close < pivot * breakout_buffer` prevents repeat signals
13. **Wilder ATR uses the specific recurrence** `((prev * (period-1)) + TR) / period`
14. **Backtest assumes worse outcome** when both stop and target are hit on the same candle
15. **Backtest overlap rule**: later same-symbol signals are skipped while a prior trade is open
16. **V2 trailing stops use D-1 data** — stop used for day t decisions was computed from data ≤ day t-1
17. **V2 trailing stops only ratchet up** — `active_stop = max(active_stop, trail_value)`
18. **MAE/MFE use initial risk denominator** — R-unit excursion metrics always reference the original entry-to-stop risk

### Architectural Invariants

1. **Polars for hot path**, DuckDB for persistence only
2. **Alert-only** — no order placement, no broker integration
3. **EOD only** — no intraday data or scanning
4. **NSE cash market only** — no derivatives
5. **NIFTYBEES is the benchmark** — used for market filter AND relative strength
6. **Backtest engine consumes persisted signals** — it does NOT recompute strategy logic
7. **Corporate actions adjustment is disabled by default**
8. **Telegram failure is non-fatal** — scan continues in console-only mode

### Config Invariants

1. Config is loaded from `config/config.yaml` + environment variables
2. `.env` values are overridden by actual environment variables
3. All Pydantic models validate at load time
4. The `allowed_series` filter is applied at data ingestion, not at scan time
5. Backtest specification lives in `docs/BACKTESTING.md`; Section 15 here is the summary only

---

## 20. Change Log

Track every strategy change here. Include date, what changed, why, and who/what made the change.

| Date | Change | Reason | Author |
|---|---|---|---|
| 2026-03-07 | Initial strategy design | Project creation | AI-assisted |
| 2026-03-07 | Added Streamlit UI | Visual inspection of signals | AI-assisted |
| 2026-03-08 | Added backtest engine | Evaluate BREAKOUT signal quality | AI-assisted |
| 2026-03-08 | Created this strategy document | Canonical reference to prevent AI drift | AI-assisted |
| 2026-03-08 | ATR: replaced Python UDF with Polars ewm_mean | Eliminate GIL bottleneck; same recurrence, EWM-seeded | AI-assisted |
| 2026-03-08 | WATCH restricted to near_pivot >= 0 | Math audit clarification 1.3: WATCH means "approaching from below" | AI-assisted |
| 2026-03-08 | BREAKOUT structural stop uses D-1 | Math audit tweak 2.2: exclude breakout day noise from stop | AI-assisted |
| 2026-03-08 | Added max_extension_breakout_pct filter (condition 8) | Math audit tweak 2.3: prevent late entries on extended breakouts | AI-assisted |
| 2026-03-08 | Doc clarifications: RS rank ordering, history_count definition | Math audit 1.1, 1.4 | AI-assisted |
| 2026-03-08 | P0 Robustness: NSE CM trading calendar gating, bhavcopy SHA-256 integrity, daily diagnostics table | Calendar correctness, reproducibility, debuggability — zero strategy math changes | AI-assisted |
| 2026-03-09 | Added Backtest V2 exit models (trail 10D low, trail 21-EMA) | Fix broken exit strategy: V1 fixed stop gives back all open profit on trend moves | AI-assisted |
| 2026-03-09 | Added MAE/MFE/giveback metrics to all backtest trades | Quantify entry quality and exit quality independently | AI-assisted |
| 2026-03-09 | Removed misleading CAGR/capital curve from backtest | Serial compounding of overlapping trades is invalid; CAGR requires portfolio simulation | AI-assisted |
| 2026-03-09 | Split backtest metrics into trade-quality vs portfolio categories | Prevent misinterpretation of R-based metrics as portfolio returns | AI-assisted |
| 2026-03-08 | Added VCP contraction sequence detection (Section 8.1) | Detect successive narrowing contractions (T1 > T2 > T3) — the true VCP signature; informational by default, optional hard gate | AI-assisted |
| 2026-03-09 | Tuned scanner parameters for signal count | min_adv50_inr 250M→50M, max_range10_pct 0.08→0.12, atr_contraction_factor 0.90→0.95, require_market_filter true→false, max_hold_days 20→60, added target_r 3.0 default | AI-assisted |
| 2026-03-14 | Parameter corrections — Minervini methodology validation | Comprehensive parameter update validated against published works. Summary below. | AI-assisted |
| 2026-03-14 | Replaced Streamlit UI with React SEPA Terminal (4 workspaces) | Full rewrite: Vite + React + Lightweight Charts v5 + Zustand + TanStack | AI-assisted |
| 2026-03-14 | Added FastAPI backend wrapping scanner + backtest engines | REST API layer for terminal frontend | AI-assisted |
| 2026-03-14 | Added 6 color-coded signal markers on all charts | Visual lifecycle: Watch, Breakout, Entry, Win Exit, Loss Exit, Not Filled | AI-assisted |
| 2026-03-14 | W4 Backtest Dashboard with full data range (2010-present) | Leverage complete OHLCV history; 1Y/3Y/5Y/10Y/All presets | AI-assisted |
| 2026-03-14 | Updated Section 16 (UI Behavior) to reflect new terminal | Strategy doc was still describing old Streamlit UI | AI-assisted |

### 2026-03-14 Parameter Correction Summary

| Parameter | Old Value | New Value | Reason |
|---|---|---|---|
| Market filter layers | 1 layer (3 conditions) | 3 layers (5 conditions) | Added EMA21 cycle confirmation and breadth filter |
| `require_market_filter` | false | true | Suppress breakouts in adverse environments |
| `benchmark_ema_period` | (new) | 21 | Cycle confirmation layer |
| `require_breadth_pct` | (new) | 0.50 | Breadth participation threshold |
| `sma200_slope_lookback` | 20 | 22 | Better alignment with monthly cycle |
| RS `lookbacks` | [63, 126, 252] | [63, 126, 189, 252] | Added 9-month window for intermediate strength |
| RS `weights` | [0.4, 0.3, 0.3] | [0.4, 0.2, 0.2, 0.2] | Distribute weight across 4 periods |
| `base_lookback` | 40 | 65 | Wider base window for proper VCP detection |
| `max_range10_pct` | 0.12 | 0.10 | Tighter contraction required |
| `max_vol_dryup_ratio` | 0.80 | 0.60 | Stricter volume dry-up threshold |
| `atr_contraction_factor` | 0.95 | 0.75 | Stricter ATR contraction via new 10d/30d ratio |
| ATR contraction formula | `atr_pct <= atr_pct_lag20 * factor` | `atr_pct_sma10 / atr_pct_sma30 <= factor` | 10d/30d ATR ratio more robust |
| `base_max_depth_pct` | 0.30 | 0.35 | Slightly wider base depth allowed |
| `contraction_min_count` | 0 (informational) | 2 (hard gate) | Require at least 2 successive contractions |
| `breakout_buffer_pct` | 0.001 | 0.003 | Wider buffer reduces false breakouts |
| `entry_buffer_pct` | 0.001 | 0.003 | Consistent with breakout buffer |
| `target_r` | 3.0 | null | No fixed target; rely on trailing stops |
| `max_hold_days` | 60 | 90 | Longer holding period for trend-following |
| `exit_mode` | V1_FIXED | V2_TRAIL_21EMA | Trailing 21-EMA stop for better trend capture |

---

## 21. Research Basis — Parameter Validation Guide

> **Note:** The parameters in this strategy document were validated against Mark Minervini's published works and academic research on momentum and volatility contraction patterns.

### Primary Sources

1. **Mark Minervini, *Trade Like a Stock Market Wizard* (2013)** — Core SEPA methodology: trend template conditions, VCP pattern recognition, pivot/breakout mechanics, and risk framing principles.
2. **Mark Minervini, *Think & Trade Like a Champion* (2017)** — Refinements to entry timing, market environment filters, and the importance of successive volatility contractions.
3. **Mark Minervini, *Mindset Secrets for Winning* (2021)** — Position management philosophy and trailing stop discipline.

### Parameter Rationale

- **Market filter (3 layers):** Minervini emphasizes trading only when the general market is in a confirmed uptrend. The 3-layer approach (trend + cycle + breadth) provides graduated confirmation rather than relying on a single indicator.
- **RS lookbacks [63, 126, 189, 252]:** Multi-timeframe relative strength captures leaders across quarterly, semi-annual, 9-month, and annual windows — consistent with Minervini's focus on stocks outperforming over multiple horizons.
- **VCP contraction (min count 2):** Minervini specifically describes the hallmark VCP as having "at least two to three contractions" with successively lower volatility. Setting `contraction_min_count = 2` enforces this minimum.
- **ATR ratio (10d/30d):** Using a ratio of smoothed ATR windows is more robust than comparing a point value to a lagged point value, reducing sensitivity to single-day spikes.
- **Base lookback (65 days):** Approximately 3 calendar months, accommodating the typical 5-to-15 week base formation described in Minervini's works.
- **Trailing 21-EMA exit:** Aligns with the institutional practice of using the 21-day EMA as a cycle reference for trend-following exits, allowing winners to run while protecting gains.

### Validation Note

These parameters represent the best-effort alignment with published methodology. They are not curve-fitted to historical NSE data. Practitioners should treat them as a starting point and monitor signal quality through the backtest framework before adjusting.

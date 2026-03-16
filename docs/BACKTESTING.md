# SEPA Scanner NSE — Canonical Backtesting Methodology

> **This is the single source of truth for the SEPA Scanner backtesting methodology.**
> Every AI session, every contributor, every code change MUST conform to this document.
> If the code disagrees with this document, the code is wrong — fix the code.
> If you need to change the methodology, update THIS document FIRST, then change the code.

**Strategy:** Minervini / SEPA-Inspired VCP Breakout Scanner
**Market:** NSE Cash (India), EOD only
**Purpose:** Evaluate BREAKOUT signal quality through rigorous backtesting and statistical validation

> Current supported runtime path: FastAPI + React terminal + `minervini_phased` as the default backtest exit engine. Legacy exit modes remain available for internal comparison and regression checks, not as the default product path.

---

## Table of Contents

1.  [Philosophy and Scope](#1-philosophy-and-scope) `[V1]`
2.  [Version Roadmap](#2-version-roadmap) `[V1]`
3.  [Backtest-to-Live Divergence Taxonomy](#3-backtest-to-live-divergence-taxonomy) `[V2+]`
4.  [Execution Modeling](#4-execution-modeling) `[V1 partial, V2 full]`
5.  [Exit Models](#5-exit-models) `[V1/V2]`
6.  [Transaction Cost Model](#6-transaction-cost-model) `[V2]`
7.  [Portfolio Simulation](#7-portfolio-simulation) `[V3]`
8.  [Metrics Reference](#8-metrics-reference) `[V1 partial, V2 full]`
9.  [Overfitting Detection and Statistical Validation](#9-overfitting-detection-and-statistical-validation) `[V4]`
10. [Benchmarks and Null Hypotheses](#10-benchmarks-and-null-hypotheses) `[V4]`
11. [Regime-Aware Testing](#11-regime-aware-testing) `[V3/V4]`
12. [Implementation Architecture](#12-implementation-architecture) `[V1+]`
13. [Validation Pipeline](#13-validation-pipeline) `[V4]`
14. [Configuration Reference](#14-configuration-reference) `[V1+]`
15. [Data Schemas](#15-data-schemas) `[V1+]`
16. [Invariants](#16-invariants) `[V1+]`
17. [Change Log](#17-change-log)

---

## 1. Philosophy and Scope

`STRATEGY.md` defines *what* signals to generate; this document defines *how to evaluate them*.

STRATEGY.md Section 15 provides a summary of the backtest model sufficient for understanding signal generation context. This document is the full specification — covering execution modeling, exit mechanics, portfolio simulation, statistical validation, and the complete path from a single-signal backtest to a deployable trading system.

**Scope.** The backtesting framework evaluates BREAKOUT signals produced by the SEPA Scanner. It progresses through four versioned stages:

1. **Single-signal evaluation** — Does each signal, in isolation, produce a positive R-expectancy?
2. **Realistic execution modeling** — What happens when we add slippage, transaction costs, gap filters, and circuit breaker handling?
3. **Portfolio simulation** — How do concurrent positions, capital allocation, and correlation risk affect the equity curve?
4. **Statistical validation** — Is the observed edge genuine, or an artifact of overfitting and data-mining?

**Core tension.** Breakout systems produce right-skewed, fat-tailed return distributions. A small number of large winners carry the system. Standard metrics (Sharpe, average return, win rate) systematically understate the quality of such systems while obscuring fragility. Every metric and test in this document is chosen to handle — or explicitly account for — non-normal, autocorrelated, regime-dependent trade sequences.

**Relationship to live trading.** A backtest is a *hypothesis test*, not a profit forecast. Budget for 30-50% alpha decay from backtest to live execution. The validation pipeline (Section 13) gates every transition from in-sample to out-of-sample to paper trading to live capital.

---

## 2. Version Roadmap

| Version | Scope | Status | Key Deliverables |
|---------|-------|--------|------------------|
| **V1** | Single-signal backtest | Implemented | 3-case fill model, Minervini phased default exit engine, legacy comparison modes, overlap rule, basic metrics (win rate, avg R, cumulative R, max drawdown), MAE/MFE |
| **V2** | Realistic execution | Planned | Max-gap filter, square-root slippage model, circuit breaker detection, T2T/BE series check, full transaction cost model, enhanced metrics (bootstrap CI, Sortino, profit factor, Monte Carlo) |
| **V3** | Portfolio simulation | Planned | Day-by-day event loop, fixed-R position sizing, capital allocation, correlation risk, drawdown circuit breakers, regime-conditional exposure |
| **V4** | Statistical validation | Planned | Deflated Sharpe Ratio, Probability of Backtest Overfitting (CSCV), walk-forward analysis, parameter stability (Morris/Sobol), null hypothesis hierarchy, factor decomposition, strategy graveyard |

Each version is additive. V2 does not modify V1 single-signal behavior — it layers realistic costs on top. V3 wraps the V1/V2 trade engine in a portfolio simulator. V4 wraps the entire pipeline in a statistical validation harness.

---

## 3. Backtest-to-Live Divergence Taxonomy

`[V2+]`

Every backtest overstates live performance. The question is *by how much* and *why*. This section catalogs the six primary sources of divergence with NSE-specific considerations.

### 3.1 Divergence Categories

| # | Category | Description | Typical Impact | Mitigation |
|---|----------|-------------|----------------|------------|
| 1 | **Look-ahead bias** | Using future data in current decisions | Fatal — invalidates all results | Time-frontier architecture (Section 12.2); shift direction audits |
| 2 | **Survivorship bias** | Testing only on stocks that survived the full period | 0.9-1.5% annual overstatement (equities) | ISIN as permanent identifier; track delistings; Bhavcopy includes delisted stocks until delist date |
| 3 | **Fill assumption bias** | Assuming fills at limit/trigger price when real fills slip | 0.1-0.5% per trade | Max-gap filter (Section 4.2); square-root impact model (Section 4.3) |
| 4 | **Slippage underestimation** | Ignoring price impact, especially in small-caps | 0.2-2.0% per trade for small-caps | Market-cap-segmented impact model calibrated to NSE data (Section 4.3) |
| 5 | **Transaction cost omission** | Ignoring STT, brokerage, exchange fees, stamp duty | 0.3-0.7% round-trip for delivery trades | Full NSE cost model (Section 6) |
| 6 | **Regime dependency** | Optimizing on a favorable regime, failing in others | Unbounded — can turn winners into losers | Regime-conditional metrics (Section 11); walk-forward validation (Section 9.5) |

### 3.2 NSE-Specific Landmines

**Bhavcopy format change (July 2024).** NSE changed its daily Bhavcopy CSV format — column names, date format, and field ordering changed. Any historical data pipeline must handle both formats or risk corrupted price histories. The data layer normalizes this at ingestion time (see STRATEGY.md Section 3).

**Circuit limits.** NSE imposes daily price bands of 2%, 5%, 10%, or 20% depending on the stock. F&O stocks have no circuit limits. A stock hitting upper circuit cannot be bought; a stock hitting lower circuit cannot be sold. Backtests that assume fills at circuit prices overstate both entries and exits.

**T2T/BE series.** Stocks in the Trade-to-Trade (T2T) or BE series require compulsory delivery — no intraday squaring. This affects liquidity and practical tradability. The `series` column in Bhavcopy identifies these stocks.

**Yahoo Finance data quality.** Yahoo Finance is the canonical runtime OHLCV source in the current product, normalized before storage. Bhavcopy is retained only as an optional validation source for same-day raw-price checks.

### 3.3 Survivorship Bias Treatment

Survivorship bias in Indian equities is less studied than in US markets but no less real.

| Study | Market | Finding |
|-------|--------|---------|
| Elton, Gruber & Blake (1996) | US mutual funds | 0.9% annual bias in survivorship-free vs survivorship-biased samples |
| Luo et al. (2014) "Seven Sins of Quantitative Investing" | General | Catalogs look-ahead, survivorship, and other systematic biases |
| Harris (2019, 2020) | US equities | Reconstitution bias — backtesting on current index members overstates returns |

**Our mitigation:** Use ISIN as the permanent identifier for each security. Track delistings from Bhavcopy. Never filter the backtest universe to current-day members of an index. When a stock is delisted, its trades are still counted in the backtest — the last available price is used for any open position.

### 3.4 Data Integrity Checks

Before any backtest run, the data pipeline verifies:

1. **No future dates** — all OHLCV dates are <= the current run date
2. **No duplicate bars** — unique (symbol, date) per record
3. **Price sanity** — `low <= open <= high` and `low <= close <= high`
4. **Volume non-negative** — `volume >= 0` for all bars
5. **SHA-256 hash** — Bhavcopy files are content-addressed for reproducibility

---

## 4. Execution Modeling

### 4.1 Three-Case Fill Model `[V1]`

A BREAKOUT signal on date `D` becomes actionable starting on trading day `D+1`. The engine evaluates each bar in the pending window sequentially:

```python
# Pseudocode matching engine.py _simulate_signal
entry_ref = signal["entry_ref"]
pending_bars = future_bars[:pending_days]     # default pending_days = 5

for bar in pending_bars:
    if bar["open"] >= entry_ref:              # Case 1: gap-through
        entry_price = bar["open"]             # fill at open (slippage-free in V1)
        break
    if bar["high"] >= entry_ref:              # Case 2: intraday trigger
        entry_price = entry_ref               # fill at exact trigger level
        break
# Case 3: no trigger within pending window → status = "not_filled"
```

| Case | Condition | Fill Price | Interpretation |
|------|-----------|------------|----------------|
| 1 — Gap-through | `open[D+k] >= entry_ref` | `open[D+k]` | Market gaps above entry; fill at the opening auction price |
| 2 — Intraday trigger | `open[D+k] < entry_ref AND high[D+k] >= entry_ref` | `entry_ref` | Price touches the trigger level intraday; fill at the trigger |
| 3 — Expiration | No trigger in `pending_days` bars | No fill | Signal expires; trade is marked `not_filled` |

**V1 simplification.** Case 1 (gap-through) fills at the open price with zero slippage. In reality, gap-through entries may slip further due to the opening auction mechanism. This is addressed in V2 (Section 4.2).

**Insufficient data before entry.** If fewer than `pending_days` bars are available after the signal date before any entry trigger occurs, the trade is marked `incomplete_data` instead of `not_filled`.

**Insufficient data after entry.** If a trade has already entered and OHLCV history ends before any normal exit condition occurs, the engine force-closes the trade at the last available close, keeps status `incomplete_data`, and includes it in closed-trade metrics.

### 4.2 Max-Gap Filter `[V2]`

Gap-through entries (Case 1) can produce fills far above the intended entry, degrading risk-reward. V2 introduces a maximum gap filter.

**Gap percentage:**

```python
gap_pct = (open_price - entry_ref) / entry_ref
```

**Default threshold:** 5% (configurable via `backtest.max_gap_pct`).

**Behavior when gap exceeds threshold:**

1. Compute `new_risk = fill_price - stop`
2. Compute `new_rr = target_distance / new_risk` (if target is defined)
3. If `new_rr < 2.0` → reject the fill; mark trade as `gap_rejected`
4. If `new_rr >= 2.0` → accept the fill at the degraded R:R

**New trade status:** `gap_rejected` — signal was valid, but the gap-through entry price destroyed the risk-reward.

**Sensitivity analysis.** Run backtest sweeps across gap thresholds `[1%, 2%, 3%, 4%, 5%, 7%, 10%, unlimited]` to measure fill-rate vs. quality trade-off. Report the threshold that maximizes expectunity (expectancy x trades/year).

**Citation:** Minervini (2013) warns explicitly against chasing breakouts that gap more than 5% above the pivot, as the risk-reward becomes unfavorable and the probability of a pullback increases.

### 4.3 Slippage Model `[V2]`

Real market impact follows a concave (square-root) function of trade size relative to liquidity.

**Formula:**

```python
slippage_bps = k * sigma_daily * sqrt(Q / ADV50)
```

Where:
- `k` = calibration constant (default 0.75)
- `sigma_daily` = trailing 20-day realized volatility of the stock (annualized / sqrt(252))
- `Q` = order quantity in shares
- `ADV50` = 50-day average daily volume in shares

**NSE calibration table by market-cap segment:**

| Segment | Typical ADV50 (shares) | sigma_daily (bps) | Estimated Slippage at 1% ADV | Estimated Slippage at 5% ADV |
|---------|----------------------|-------------------|------------------------------|------------------------------|
| Large-cap (Nifty 50) | > 5M | 120-180 | 1-2 bps | 3-5 bps |
| Mid-cap (Nifty Midcap 150) | 500K-5M | 150-250 | 3-8 bps | 8-18 bps |
| Small-cap (Nifty Smallcap 250) | 50K-500K | 200-400 | 8-25 bps | 25-60 bps |
| Micro-cap (below Smallcap 250) | < 50K | 300-600 | 25-80 bps | 80-200 bps |

**Citation:** The empirical square-root market impact law is one of the most robust findings in market microstructure, documented across markets from NYSE to LSE to TSE (Bouchaud et al., 2009; Almgren et al., 2005). The concave shape arises from the informational content of order flow.

**Implementation.** Slippage is applied as a fill-price adjustment:

```python
adjusted_entry = entry_price * (1 + slippage_bps / 10_000)
adjusted_exit  = exit_price  * (1 - slippage_bps / 10_000)
```

### 4.4 Circuit Breaker Detection `[V2]`

NSE imposes daily price movement limits on most securities (F&O stocks are exempt).

**Detection heuristic:**

```python
locked_circuit = (open == close) and (high == low)   # price locked at limit
```

**Circuit limit bands by category:**

| Band | Applied To | Example |
|------|-----------|---------|
| 2% | Recently volatile stocks | Post-IPO period |
| 5% | Stocks with elevated volatility | Micro-caps, recently news-driven |
| 10% | Standard band for most stocks | Default for non-F&O |
| 20% | Stocks meeting specific criteria | Large-caps, high-liquidity |
| No limit | F&O stocks | Nifty 50 components, etc. |

**Impact on backtest:**

| Scenario | Effect | V2 Treatment |
|----------|--------|-------------|
| Upper circuit on entry day | Cannot buy; entry is impossible | Mark as `circuit_unfillable`; do not fill |
| Lower circuit on exit day | Cannot sell; exit is impossible | Defer exit to next tradeable day; mark `circuit_delayed_exit` |
| Upper circuit during hold | No action needed (favorable move) | Track in MFE but no action |
| Lower circuit during hold | Cannot exit at stop | Exit at next tradeable open; actual loss may exceed stop |

### 4.5 T2T/BE Series Check `[V2]`

Stocks in the Trade-to-Trade (T2T) or BE series require compulsory delivery for every trade. There is no intraday netting.

**Detection:**

```python
# Check 'series' column in Bhavcopy
is_t2t = series in ("BE", "BZ", "BT")
```

**Impact:** T2T stocks often have very low liquidity and wide bid-ask spreads. Swing entries that assume intraday triggers (Case 2) are unreliable for T2T stocks.

**V2 treatment options:**
1. **Exclude T2T stocks entirely** from backtest universe (conservative)
2. **Allow T2T with higher slippage** — multiply slippage by 2-3x (moderate)
3. **Flag T2T trades** in output for manual review (permissive)

Default: Option 1 (exclude). Configurable via `backtest.allow_t2t: bool`.

---

## 5. Exit Models

`[V1/V2]`

All exit models share the same priority order, evaluated on each bar after entry:

1. Gap-below stop (open check, not on entry day)
2. Gap-above target (open check, not on entry day)
3. Same-day stop + target conflict (assume worse outcome — exit at stop)
4. Intraday stop hit
5. Intraday target hit
6. Time stop
7. Trailing stop update (applied to NEXT bar's decisions)

### 5.1 V1_FIXED — Fixed Stop, Optional Target, Time Stop `[V1]`

The simplest exit model. The initial stop never moves.

```python
# ExitMode.V1_FIXED ("v1_fixed")
# active_stop = initial_stop (never changes)
# target_price = entry_price + (risk * target_r) if target_r is set, else None
# time_stop at max_hold_days → exit at close
```

| Parameter | Default | Effect |
|-----------|---------|--------|
| `target_r` | `None` | If set, defines profit target as entry + target_r * initial_risk |
| `max_hold_days` | `90` | Force exit at close on the 90th holding day |

### 5.2 V2_TRAIL_10D — 10-Day Rolling Low Trail `[V1]`

After the trailing stop activates, the stop ratchets up to the minimum of the last 10 daily lows.

```python
# ExitMode.V2_TRAIL_10D ("v2_trail_10d")
# After activation:
#   trail_value = min(low_buffer[-10:])    # 10-day rolling low
#   active_stop = max(active_stop, trail_value)
```

The low buffer accumulates from entry day onward. Before 10 bars are available, the minimum of all available lows is used.

### 5.3 V2_TRAIL_21EMA — 21-EMA Trail `[V1]`

After the trailing stop activates, the stop ratchets up to the 21-period exponential moving average of close prices.

```python
# ExitMode.V2_TRAIL_21EMA ("v2_trail_21ema") — LEGACY COMPARISON MODE
# EMA parameters:
#   alpha = 2 / (21 + 1)  = 2/22 ≈ 0.0909
#   ema21(0) = close(entry_day)                              # seed
#   ema21(t) = alpha * close(t) + (1 - alpha) * ema21(t-1)  # update
# After activation:
#   active_stop = max(active_stop, ema21)
```

This is a **legacy comparison mode** (`ExitMode.V2_TRAIL_21EMA`). The EMA is seeded from the entry-day close and warms up over the holding period. The supported runtime default is `ExitMode.MINERVINI_PHASED`.

### 5.4 Trail Activation and Ratchet Rules `[V1]`

Trail activation is common to both V2 exit modes:

```python
# Activation condition (checked on each bar after entry):
trail_activated = high >= entry_price + trail_activation_r * initial_risk
# Default trail_activation_r = 1.0 (i.e., +1R profit)

# On activation:
active_stop = max(active_stop, entry_price)   # immediate break-even
```

**Key rules:**
- Activation is **permanent** — once triggered, it cannot be deactivated
- The stop **only ratchets up** — `active_stop = max(active_stop, trail_value)`
- Trail updates happen **after** exit checks — today's trail value is used for *tomorrow's* exit decisions
- **Pre-activation indicator tracking** — both V2 modes maintain their indicator state (low buffer for 10D, EMA for 21EMA) from entry day onward, even before trail activation. This ensures the indicator has warm-up data when activation occurs.

### 5.5 Gap-Below-Stop Exit Logic `[V1]`

```python
# Checked on every bar EXCEPT the entry day (offset > 0):
if not is_entry_day and open_price <= active_stop:
    exit_price = open_price       # fill at the gap-down open
    status = "stopped"
```

Gap-below stop takes priority over all other exit checks. The exit price is the actual open, which may be significantly below the stop level. This accurately models gap risk.

For MAE/MFE, the open price itself counts as part of the trade path on a gap exit, but the later intraday high/low from that same bar does not, because the position is already closed.

### 5.6 Same-Day Stop + Target Conflict `[V1]`

When both the stop and target are hit on the same daily candle (intraday), the backtest **assumes the worse outcome**:

```python
if low <= active_stop and high >= target_price:
    exit_price = active_stop       # assume stopped out
    status = "stopped"
```

**Rationale.** With daily bars, we cannot determine the intrabar sequence of events. Assuming the worse outcome is the conservative choice and avoids upward bias.

---

## 6. Transaction Cost Model

`[V2]`

### 6.1 NSE Delivery Trade Cost Components

All SEPA Scanner trades are delivery trades (hold overnight). The following costs apply:

| Component | Buy Side | Sell Side | Notes |
|-----------|----------|-----------|-------|
| **Brokerage** | 0.00% (flat-fee brokers) | 0.00% | Zerodha, Groww etc. charge zero on delivery |
| **STT (Securities Transaction Tax)** | 0.10% | 0.10% | On transaction value |
| **Exchange Transaction Charges** | 0.00345% | 0.00345% | NSE rate |
| **SEBI Turnover Fee** | 0.0001% | 0.0001% | Regulatory fee |
| **Stamp Duty** | 0.015% (varies by state) | 0.00% | Only on buy side; capped by state |
| **GST (18%)** | On brokerage + exchange charges | On brokerage + exchange charges | 18% of (brokerage + exchange + SEBI fees) |
| **DP Charges** | — | ~₹15.93 per sell transaction | Depository participant charge; fixed per scrip per day |

### 6.2 Round-Trip Cost Formula

```python
# For a flat-fee broker (zero brokerage on delivery):
buy_side_pct  = 0.10 + 0.00345 + 0.0001 + 0.015 + 0.18 * (0.00345 + 0.0001)
              = 0.11917%

sell_side_pct = 0.10 + 0.00345 + 0.0001 + 0.18 * (0.00345 + 0.0001)
              = 0.10419%

round_trip_pct = buy_side_pct + sell_side_pct
               ≈ 0.224%

# DP charges (fixed):
dp_charge_per_sell = 15.93   # INR, approximate

# Total round-trip for a trade of value V:
total_cost = V * round_trip_pct / 100 + dp_charge_per_sell
```

### 6.3 Impact Cost by Market-Cap Segment

Impact cost measures the actual cost of executing a trade at the current bid-ask spread and depth. NSE publishes impact cost data for index reconstitution.

| Segment | Median Impact Cost (one-way) | Range | Source |
|---------|------------------------------|-------|--------|
| Large-cap (Nifty 50) | 0.14-0.17% | 0.05-0.30% | NSE index methodology |
| Mid-cap (Nifty Midcap 150) | 0.22-0.37% | 0.10-0.75% | Estimated from bid-ask data |
| Small-cap (Nifty Smallcap 250) | 0.37-1.12% | 0.20-2.25% | Estimated from bid-ask data |
| Micro-cap (below Smallcap 250) | 1.12-3.00% | 0.50-5.00% | Estimated from bid-ask data |

**Total effective round-trip cost (transaction + impact):**

| Segment | Estimated Round-Trip | Range |
|---------|---------------------|-------|
| Large-cap | 0.29-0.34% | 0.22-0.50% |
| Mid-cap | 0.44-0.74% | 0.30-1.20% |
| Small-cap | 0.74-2.24% | 0.50-4.00% |
| Micro-cap | 2.24-6.00% | 1.00-10.00% |

### 6.4 Annual Cost Drag

```python
annual_cost_drag = trades_per_year * avg_round_trip_cost_pct
```

**Example:** At 75 trades/year and 0.50% average round-trip cost, the annual drag is **37.5 basis points of portfolio value per trade, or 37.5% of capital** turned over at 0.5% per turn. This is a material headwind for high-frequency rebalancing.

For the SEPA Scanner with ~50-100 signals/year, ~60% fill rate, and mid/small-cap concentration:
- Estimated fills: 30-60 trades/year
- Estimated avg round-trip: 0.50-1.50%
- **Annual cost drag: 15-90 basis points of portfolio notional per trade cycle**

### 6.5 Configuration

| Config Key | Type | Default | Notes |
|------------|------|---------|-------|
| `backtest.costs_enabled` | `bool` | `false` | Off by default for V1 backward compatibility |
| `backtest.round_trip_cost_pct` | `float` | `0.50` | Flat percentage override (if costs_enabled) |
| `backtest.dp_charge_inr` | `float` | `15.93` | DP charge per sell transaction |
| `backtest.use_impact_cost_model` | `bool` | `false` | If true, use segment-based impact cost instead of flat override |

---

## 7. Portfolio Simulation

`[V3]`

### 7.1 Event-Loop Architecture

The portfolio simulator processes the market day-by-day, in strict chronological order. Each day follows this sequence:

```
for each trading_day in simulation_period:
    1. Mark-to-market all open positions (using day's close)
    2. Evaluate exit conditions on all open positions
    3. Process exits (update capital, record closed trades)
    4. Collect new BREAKOUT signals from this day
    5. Filter signals (overlap, circuit, T2T, gap)
    6. Prioritize remaining signals (RS rank → R:R → sector diversity)
    7. Size positions (fixed-R model)
    8. Execute entries (subject to available capital and position limits)
    9. Record portfolio state snapshot
```

This architecture eliminates look-ahead bias by construction — each step uses only data available at the time of the decision.

### 7.2 Fixed-R Position Sizing

Every trade risks a fixed percentage of current portfolio equity.

```python
risk_pct = 0.01                            # default: 1% of equity per trade
risk_per_share = entry_price - stop        # initial risk
shares = floor((equity * risk_pct) / risk_per_share)
position_value = shares * entry_price
position_pct = position_value / equity     # actual position size
```

**Relationship between trade risk and position size:**

| Entry-to-Stop Risk | Position Size at 1% Portfolio Risk |
|--------------------|-----------------------------------|
| 3% | 33.3% of equity |
| 5% | 20.0% of equity |
| 7% | 14.3% of equity |
| 8% | 12.5% of equity |
| 10% | 10.0% of equity |
| 12% | 8.3% of equity |

**Citation:** Minervini recommends 5-8% maximum entry-to-stop distance. At 1% portfolio risk, this produces position sizes of 12.5-20% of equity — sufficient concentration for meaningful impact without catastrophic single-stock risk.

**Position size cap.** No single position may exceed `max_position_pct` (default 25%) of equity, regardless of the risk calculation. If the R-based sizing produces a position larger than this cap, the position is capped and the actual risk per trade is less than `risk_pct`.

### 7.3 Capital Allocation

| Parameter | Default | Description |
|-----------|---------|-------------|
| `portfolio.max_positions` | `15` | Maximum concurrent open positions |
| `portfolio.risk_pct` | `0.01` | Risk per trade as fraction of equity |
| `portfolio.max_position_pct` | `0.25` | Maximum single position as fraction of equity |
| `portfolio.initial_capital` | `1_000_000` | Starting equity in INR |

**Signal priority when capital is constrained:**

1. **RS rank** — higher RS rank first (strongest relative strength)
2. **R:R ratio** — higher reward-to-risk ratio first
3. **Sector diversification** — prefer signals from underrepresented sectors

When more signals than slots are available on a single day, prioritize by the above hierarchy. Rejected signals are marked `capital_constrained` (not `skipped_overlap`).

### 7.4 Correlation Risk

Concentrated breakout portfolios can have high intra-portfolio correlation, especially when the market is trending strongly.

**Effective risk formula:**

```python
effective_risk = nominal_risk * sqrt(N * avg_pairwise_corr)
```

Where:
- `nominal_risk` = sum of individual position risks (N * risk_pct)
- `N` = number of concurrent positions
- `avg_pairwise_corr` = average pairwise correlation of held positions (estimated from trailing 60-day returns)

**Example:** 10 positions, 1% risk each, average correlation 0.4:

```
nominal_risk = 10 * 1% = 10%
effective_risk = 10% * sqrt(10 * 0.4) = 10% * 2.0 = 20%
```

**Sector exposure cap:** No single GICS sector may represent more than 25-35% of portfolio notional value (configurable via `portfolio.max_sector_pct`, default 30%).

**Portfolio heat cap:** Total portfolio risk (sum of all open position risks) must not exceed `portfolio.max_heat_pct` (default 15%) of equity. If adding a new position would breach this cap, the position is deferred.

### 7.5 Drawdown Circuit Breakers

Automatic position-sizing adjustments based on portfolio drawdown from equity peak:

| Drawdown from Peak | Action |
|-------------------|--------|
| 0% to -5% | Normal operation — full sizing |
| -5% to -10% | Warning — reduce `risk_pct` to 75% of normal |
| -10% to -15% | Defensive — reduce `risk_pct` to 50% of normal |
| -15% to -20% | Mostly cash — reduce `risk_pct` to 25% of normal; no new entries |
| > -20% | Circuit breaker — close all positions at next open; pause for 10 trading days |

These thresholds are configurable. The goal is to prevent a losing streak from compounding into an unrecoverable drawdown.

---

## 8. Metrics Reference

### 8.1 V1 Metrics (Current Implementation) `[V1]`

These metrics are computed by `metrics.py` and match the current codebase exactly.

**Trade-level metrics:**

```python
return_pct = ((exit_price - entry_price) / entry_price) * 100.0
r_multiple = (exit_price - entry_price) / (entry_price - stop)
```

**Summary metrics:**

| Metric | Formula | Code Reference |
|--------|---------|----------------|
| `total_signals` | Count of all signals in backtest range | `len(trades)` |
| `filled_trades` | Count of trades with an entry date | `sum(1 for t in trades if t.entry_date is not None)` |
| `not_filled_signals` | Count with status `not_filled` | `sum(1 for t in trades if t.status == "not_filled")` |
| `stopped_trades` | Count with status `stopped` | `sum(1 for t in trades if t.status == "stopped")` |
| `target_exits` | Count with status `target` | `sum(1 for t in trades if t.status == "target")` |
| `time_stop_exits` | Count with status `time_stop` | `sum(1 for t in trades if t.status == "time_stop")` |
| `win_rate` | `len(winners) / len(closed_trades)` | Winners have `r_multiple > 0` |
| `average_return_pct` | `mean(return_pct)` of closed trades | — |
| `average_r` | `mean(r_multiple)` of closed trades | — |
| `median_r` | `median(r_multiple)` of closed trades | Uses `statistics.median` |
| `expectancy` | Same as `average_r` in V1 | Identical; V2 adds bootstrap CI |
| `payoff_ratio` | `avg_win_r / abs(avg_loss_r)` | `(sum(winners)/len(winners)) / abs(sum(losers)/len(losers))` |
| `cumulative_r` | `sum(r_multiple)` of closed trades | — |
| `max_drawdown` | `min(drawdown)` on equity curve | Drawdown = cumulative_r - running_peak |
| `longest_losing_streak` | Max consecutive trades with `r_multiple < 0` | Sorted by (exit_date, symbol, signal_date) |

**MAE/MFE metrics:**

| Metric | Formula | Scope |
|--------|---------|-------|
| `mae_price` | `min(low)` during holding period | Per-trade |
| `mfe_price` | `max(high)` during holding period | Per-trade |
| `mae_r` | `(entry_price - mae_price) / initial_risk` | Per-trade; positive = adverse excursion |
| `mfe_r` | `(mfe_price - entry_price) / initial_risk` | Per-trade; positive = favorable excursion |
| `giveback_pct` | `(mfe_r - r_multiple) / mfe_r` | Per-trade; only meaningful for winners (mfe_r > 0) |
| `avg_mfe_r` | `mean(mfe_r)` of closed trades with non-null mfe_r | Summary |
| `avg_mae_r` | `mean(mae_r)` of closed trades with non-null mae_r | Summary |
| `avg_giveback_pct` | `mean(giveback_pct)` of winning trades | Summary; winners only |

**Equity curve schema:**

| Column | Type | Description |
|--------|------|-------------|
| `exit_date` | `Date` | Exit date of the trade |
| `symbol` | `String` | Stock symbol |
| `r_multiple` | `Float64` | R-multiple of the individual trade |
| `cumulative_r` | `Float64` | Running sum of R-multiples |
| `running_peak` | `Float64` | High-water mark of cumulative_r |
| `drawdown` | `Float64` | `cumulative_r - running_peak` (always <= 0) |

Trades are sorted by `(exit_date, symbol, signal_date)` before computing the equity curve.

### 8.2 V2 Enhanced Metrics `[V2]`

**Expectancy with bootstrap confidence interval:**

```python
# 10,000 bootstrap resamples of the R-multiple distribution
boot_means = [mean(resample(r_values, n=len(r_values))) for _ in range(10_000)]
ci_lower, ci_upper = percentile(boot_means, [2.5, 97.5])
# Report: "Expectancy: 0.28R [95% CI: 0.12R – 0.47R]"
```

**Expectunity:**

```python
expectunity = expectancy * trades_per_year
# Measures total R expected per year — combines edge size with opportunity frequency
```

**Profit factor:**

```python
profit_factor = sum(r for r in r_values if r > 0) / abs(sum(r for r in r_values if r < 0))
# > 1.0 = profitable system; > 2.0 = strong; > 3.0 = exceptional
```

**Sortino ratio (R-based):**

```python
mean_r = mean(r_values)
downside_returns = [r for r in r_values if r < 0]
downside_deviation = sqrt(mean([r**2 for r in downside_returns]))
sortino_r = mean_r / downside_deviation
```

**Tail ratio:**

```python
tail_ratio = percentile(r_values, 95) / abs(percentile(r_values, 5))
# > 1.0 = right-skewed (desirable for breakout systems)
# Breakout systems should target tail_ratio > 2.0
```

**R-distribution percentiles:**

```
P5, P10, P25 (Q1), P50 (median), P75 (Q3), P90, P95
```

Report as a table in every backtest summary. This reveals the shape of the distribution far better than mean/median alone.

**Monte Carlo equity curve simulation:**

```python
# 5,000 reshuffles of the trade sequence
for trial in range(5_000):
    shuffled = random.shuffle(r_values)
    equity_curve = cumsum(shuffled)
    record max_drawdown, final_equity, max_consecutive_loss
# Report: P5, P25, P50, P75, P95 of max drawdown and terminal wealth
```

**Citation:** The Quantopian tearsheet study (2019) found that in-sample Sharpe ratio has R-squared < 0.025 with out-of-sample Sharpe — it is essentially unpredictive. However, risk metrics are far more stable: realized volatility R-squared ~ 0.67, max drawdown R-squared ~ 0.34. This motivates our emphasis on drawdown and risk metrics over return metrics.

### 8.3 MAE/MFE Analysis `[V2]`

**Edge ratio:**

```python
# Measured at T=5 bars post-entry
edge_ratio = median(mfe_at_T5) / median(mae_at_T5)
# > 1.0 = entries tend to move favorably before moving adversely
# Breakout systems should target edge_ratio > 1.5 at T=5
```

**MAE scatter plot interpretation:**
- X-axis: MAE_R (how far the trade went against you)
- Y-axis: R-multiple (final outcome)
- Trades in the upper-left quadrant (low MAE, high R) are ideal entries
- A cluster of losers with MAE > 2R suggests the stop is too wide
- Winners with MAE > 1R suggests entries could be timed better

**Stop optimization from MAE distribution:**
- Plot the CDF of MAE_R for all trades
- Find the MAE threshold where tightening the stop would eliminate more losers than winners
- If 80% of winners have MAE < 1.5R but 50% of losers also have MAE < 1.5R, the stop at 1.5R is not discriminating

**Exit optimization from MFE distribution:**
- Plot MFE_R for winners that eventually give back significant profit (giveback > 50%)
- If the median MFE_R of high-giveback trades is 3R, a trailing stop that locks in profit at 2R would capture more of the move

### 8.4 Confidence Interval Reporting Format `[V2]`

All V2+ metrics are reported with confidence intervals:

```
Expectancy:       0.28R  [95% CI: 0.12R – 0.47R]
Win Rate:         42.3%  [95% CI: 36.1% – 48.8%]
Profit Factor:    1.85   [95% CI: 1.32 – 2.61]
Max Drawdown:    -8.4R   [MC P95: -12.1R]
Sortino Ratio:    1.42   [95% CI: 0.89 – 2.12]
```

The Monte Carlo P95 max drawdown is reported alongside the realized drawdown. If the realized drawdown is close to the Monte Carlo P95, the system is operating near the edge of expected behavior.

---

## 9. Overfitting Detection and Statistical Validation

`[V4]`

### 9.1 Degrees of Freedom

The SEPA Scanner has approximately 25-30 nominal parameters (trend template thresholds, VCP conditions, RS weights, breakout triggers, backtest exit parameters). However, many parameters are correlated — changing `sma50_above_sma200` affects the same stocks as `sma200_rising`.

**Effective degrees of freedom** are estimated via:
1. PCA on the parameter sensitivity matrix — retain components explaining 95% of variance
2. Optimal Number of Clusters (ONC) on parameter correlation matrix

Estimated effective parameters: 8-15 (depending on regime and universe).

**Citation:** Harvey, Liu & Zhu (2016) argue that with hundreds of factors tested across the finance literature, a t-statistic of 2.0 is insufficient. They recommend requiring t-stat > 3.0 (corresponding to p < 0.003) to account for multiple testing. For a system with 8-15 effective parameters, this threshold is appropriate.

### 9.2 Parameter Stability Analysis

A robust strategy should perform well across a *range* of parameter values, not just at a single optimum. If moving a parameter by 10% destroys profitability, the strategy is likely overfit.

**Phase 1 — Morris method global screening:**

```
Evaluations: ~620 for 30 parameters (20 trajectories × 31 steps)
Output: μ* (importance) and σ (interaction) for each parameter
Purpose: Identify which parameters matter and which are inert
```

**Phase 2 — 2D heatmap analysis:**

For the top 5-10 most important parameters (from Morris screening), generate pairwise heatmaps of expectancy across the parameter space. Look for:
- **Plateaus** — broad regions of similar performance (good)
- **Ridges** — narrow paths of high performance (bad — overfit)
- **Cliffs** — sharp performance drops at parameter boundaries (bad — fragile)

**Phase 3 — Sobol sensitivity indices:**

```
Evaluations: ~62,000 for 30 parameters (2048 base samples × (30+2) columns)
Output: S1 (first-order), ST (total-order) for each parameter
Purpose: Decompose variance into individual and interaction effects
```

**Pass criterion:** A parameter is considered stable if >20% of its tested range produces metrics at >80% of peak performance. If fewer than half of all parameters pass this test, the strategy is likely overfit.

### 9.3 Deflated Sharpe Ratio

The Deflated Sharpe Ratio (DSR) adjusts the observed Sharpe Ratio for the number of trials (strategies tested) and the statistical properties of returns.

**Formula:**

```python
DSR = Phi((SR_hat - SR_0) / sigma_hat_SR_0)
```

Where:
- `SR_hat` = observed Sharpe Ratio of the selected strategy
- `SR_0` = expected maximum Sharpe Ratio under the null hypothesis (no skill)
- `sigma_hat_SR_0` = standard error of SR_0
- `Phi` = standard normal CDF

**False Strategy Theorem for SR_0:**

```python
SR_0 = sqrt(V(SR)) * ((1 - gamma) * Phi_inv(1 - 1/N) + gamma * Phi_inv(1 - 1/N * exp(-1)))
```

Where:
- `V(SR)` = variance of individual strategy Sharpe Ratios
- `N` = number of strategies tested (from the strategy graveyard)
- `gamma` = Euler-Mascheroni constant ≈ 0.5772

**Threshold:** Require DSR > 95% confidence (i.e., `DSR > 0.95`). A strategy that fails this test has a >5% probability of being a false positive.

**Citation:** Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality."

### 9.4 Probability of Backtest Overfitting (PBO)

PBO uses Combinatorially Symmetric Cross-Validation (CSCV) to estimate the probability that a backtest-optimized strategy is overfit.

**Procedure:**

```
1. Partition the return series into S = 16 contiguous blocks
2. For each combination C(16, 8) = 12,870:
   a. Use 8 blocks as in-sample (IS), 8 as out-of-sample (OOS)
   b. Select the best strategy on IS data
   c. Measure its rank on OOS data
3. PBO = fraction of combinations where the IS-optimal strategy
   ranks below median on OOS data
```

**Interpretation:**

| PBO | Assessment |
|-----|-----------|
| < 10% | Genuine edge — low probability of overfitting |
| 10-30% | Moderate concern — further validation needed |
| 30-50% | High concern — strategy likely partially overfit |
| > 50% | Severe overfitting — IS performance is not predictive of OOS |

**Citation:** Bailey, Borwein, Lopez de Prado & Zhu (2014/2016), "Pseudomathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance."

### 9.5 Walk-Forward Analysis

Walk-forward analysis simulates the real-world process of periodic re-optimization.

**Configuration:**

| Parameter | Value |
|-----------|-------|
| Window type | Anchored (expanding IS window) |
| IS/OOS split | 70% / 30% |
| Minimum steps | 4 |
| Step size | 6 months |

**Walk-Forward Efficiency (WFE):**

```python
WFE = mean(OOS_metric) / mean(IS_metric) * 100
# > 50% = acceptable; target > 60%
# < 50% = strategy loses too much performance out of sample
```

**Triple parietal split (for final validation):**

```
50% Training — optimize parameters
25% Validation — select among candidate strategies
25% Test — final unbiased performance estimate
```

The test set is touched exactly once. If the test performance is unsatisfactory, the strategy is rejected — not re-optimized.

### 9.6 Strategy Graveyard

Every backtest configuration ever tested is recorded in a DuckDB table to feed DSR and PBO calculations.

**Schema: `backtest_runs`**

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | `UUID` | Unique identifier |
| `timestamp` | `TIMESTAMP` | When the run was executed |
| `code_hash` | `VARCHAR` | SHA-256 of the backtest engine source |
| `data_hash` | `VARCHAR` | SHA-256 of the input data (signals + OHLCV) |
| `params_json` | `JSON` | Full parameter set as JSON |
| `metrics_json` | `JSON` | Full summary metrics as JSON |
| `is_production` | `BOOLEAN` | Whether this was a production run or exploratory |
| `notes` | `VARCHAR` | Free-form annotation |

**Content-addressable hashing.** The `code_hash` and `data_hash` together form a reproducibility key. Any run with the same hashes and parameters must produce identical results.

### 9.7 Cross-Market Validation

The ultimate test of robustness: apply NSE-optimized parameters to a different market without re-optimization.

**Procedure:**
1. Optimize parameters on NSE data (2010-2023)
2. Apply identical parameters to US data (S&P 500 universe, same period)
3. Compare metrics — expect degradation but not complete failure

**Interpretation:**
- OOS expectancy > 50% of IS expectancy → parameters capture a real cross-market effect
- OOS expectancy < 0 → parameters are NSE-specific artifacts
- Similar drawdown profiles → risk characteristics are robust

This test does not require identical performance — market microstructure differences alone explain significant divergence. The test validates that the *direction* of the edge transfers.

---

## 10. Benchmarks and Null Hypotheses

`[V4]`

### 10.1 Null Hypothesis Hierarchy

A profitable backtest is meaningless without comparison to appropriate null models. We define four levels of null hypothesis, each isolating a different component of the system's edge.

| Level | Null Model | What It Tests | Construction |
|-------|-----------|---------------|--------------|
| **L1** | Random entries, same exits | Value of ALL entry logic | Random dates from the universe, same exit rules and parameters |
| **L2** | Trend template only (no VCP, no RS, no breakout timing) | Marginal value of VCP + RS + breakout timing | Enter on random days when trend template passes |
| **L3** | Trend template + RS filter, random timing | Pure timing value of the VCP breakout trigger | Enter on random days when trend + RS pass |
| **L4** | Full system minus volume confirmation | Marginal value of volume confirmation | Remove the volume surge condition from BREAKOUT |

**Marginal decomposition.** The Sharpe Ratio (or expectancy) difference between successive levels decomposes the total edge:

```
Edge(total)   = SR(full system) - SR(L1)
Edge(trend)   = SR(L2) - SR(L1)          # value of trend filtering
Edge(VCP+RS)  = SR(L3) - SR(L2)          # value of stock selection
Edge(timing)  = SR(L4) - SR(L3)          # value of breakout timing
Edge(volume)  = SR(full) - SR(L4)        # value of volume confirmation
```

If any component's marginal Sharpe is not statistically significant (t-stat < 2.0), that component may not contribute real value.

### 10.2 Factor Decomposition

Breakout momentum strategies load on known factors. We decompose returns to isolate pure alpha.

**4-factor regression (Carhart model):**

```python
R_p - R_f = alpha + beta_1 * MKT + beta_2 * SMB + beta_3 * HML + beta_4 * WML + epsilon
```

Where:
- `R_p` = portfolio return (from V3 simulation)
- `R_f` = risk-free rate (91-day T-bill)
- `MKT` = market excess return (Nifty 50 - R_f)
- `SMB` = size factor (small minus big)
- `HML` = value factor (high book-to-market minus low)
- `WML` = momentum factor (winners minus losers)
- `alpha` = pure breakout timing alpha — the residual after removing all factor exposures

**Data source:** IIM Ahmedabad publishes Indian factor data (Fama-French + momentum) for academic research.

**Citation:** Agarwalla, Jacob & Varma (2013), "Four Factor Model in Indian Equities Market," IIM Ahmedabad Working Paper. This is the standard reference for Indian factor data.

**Interpretation:**
- `alpha > 0` and statistically significant (t > 2.0) → genuine stock-picking / timing skill
- `alpha ≈ 0` but `beta_4 (WML) >> 0` → strategy is just a momentum proxy
- `alpha ≈ 0` and `beta_2 (SMB) >> 0` → strategy is just a small-cap proxy

### 10.3 Permutation Tests

Non-parametric significance testing that makes no distributional assumptions.

**Signal-date shuffling:**

```python
# 1,000-10,000 permutations
for trial in range(10_000):
    shuffled_dates = random.shuffle(signal_dates)
    reassign signals to shuffled dates (preserving symbol mapping)
    run backtest
    record metric
p_value = fraction of permuted metrics >= observed metric
```

**Stationary bootstrap:**

For time-series metrics (Sharpe, drawdown) where autocorrelation matters, use the stationary bootstrap with automatic block-length selection (Politis & Romano, 1994; Politis & White, 2004).

```python
# Automatic block length: optimal_block_length(returns)
# BCa (bias-corrected and accelerated) confidence intervals
# Report: "Sharpe: 1.42 [BCa 95% CI: 0.78 – 2.31]"
```

BCa intervals are preferred over percentile intervals because they correct for bias and skewness in the bootstrap distribution.

---

## 11. Regime-Aware Testing

`[V3/V4]`

### 11.1 Four-Layer Regime Detection

Breakout systems are regime-dependent by nature. A system optimized during a bull market will underperform or fail during bear markets, corrections, or range-bound environments.

| Layer | Indicator | Bullish | Neutral | Bearish |
|-------|----------|---------|---------|---------|
| **Primary** | Nifty 50 vs 200 DMA | Close > 200 DMA | Within 2% of 200 DMA | Close < 200 DMA |
| **Secondary** | Breadth (% Nifty 500 > 200 DMA) | > 60% | 40-60% | < 40% |
| **Tertiary** | India VIX | < 15 | 15-25 | > 25 |
| **Optional** | HMM (2-3 states on Nifty daily returns) | Low-vol uptrend | Transition | High-vol downtrend |

**Composite regime score:**

```python
regime_score = w1 * primary + w2 * secondary + w3 * tertiary
# w1=0.50, w2=0.30, w3=0.20 (defaults)
# Score range: -1.0 (maximum bearish) to +1.0 (maximum bullish)
```

**Citation:** Minervini documents that 90.77% of successful breakouts occurred when the major indices were trading above their monthly 10-period exponential moving average. This is the empirical basis for regime-conditional exposure.

### 11.2 NSE Regime History (2003-2025)

| Period | Regime | Nifty 50 Move | Key Event |
|--------|--------|---------------|-----------|
| 2003-2007 | Bull | +500% | Post-dot-com recovery, global liquidity boom |
| Jan 2008 - Mar 2009 | Bear | -60% | Global Financial Crisis |
| Mar 2009 - Nov 2010 | Bull | +150% | QE-driven recovery |
| Nov 2010 - Dec 2013 | Range | -10% to +10% | Policy paralysis, taper tantrum |
| 2014 - Jan 2018 | Bull | +80% | Modi election, reform narrative |
| Feb 2018 - Sep 2018 | Correction | -15% | IL&FS crisis, NBFC contagion |
| Oct 2018 - Jan 2020 | Recovery | +20% | Selective recovery, narrow breadth |
| Feb 2020 - Mar 2020 | Crash | -38% | COVID-19 pandemic crash |
| Apr 2020 - Oct 2021 | Bull | +130% | Massive liquidity injection, retail participation boom |
| Oct 2021 - Jun 2022 | Correction | -17% | Fed tightening, FII outflows |
| Jul 2022 - Sep 2024 | Bull | +45% | India outperformance, domestic flows |
| Oct 2024 - Mar 2025 | Correction | -14% | FII outflows, global tariff uncertainty |

### 11.3 Regime-Conditional Exposure Scaling

| Regime Score | Exposure Level | Max Positions | Risk per Trade |
|-------------|----------------|---------------|----------------|
| > +0.5 (Strong bull) | 100% | 15-20 | 1.0% |
| +0.2 to +0.5 (Bull) | 80% | 12-15 | 0.8% |
| -0.2 to +0.2 (Neutral) | 50% | 8-10 | 0.5% |
| -0.5 to -0.2 (Bear) | 25% | 3-5 | 0.3% |
| < -0.5 (Strong bear) | 0% | 0 | 0.0% — fully in cash |

**Implementation:** The portfolio simulator (Section 7) reads the regime score at the start of each trading day and adjusts position sizing and slot limits accordingly. Existing positions are NOT force-closed by regime changes — only new entries are affected.

### 11.4 Stress Testing Protocol

**Historical crisis replay.** Re-run the backtest with the portfolio simulator through each historical crisis period:

| Crisis | Period | Test Focus |
|--------|--------|------------|
| GFC 2008 | Jan 2008 - Mar 2009 | Maximum drawdown; gap risk; correlated sell-off |
| IL&FS 2018 | Sep 2018 - Nov 2018 | NBFC contagion; sector concentration risk |
| COVID 2020 | Feb 2020 - Apr 2020 | Limit-down days; circuit breaker cascade; V-shaped recovery |
| 2022 Correction | Oct 2021 - Jun 2022 | Slow bleed; FII outflows; sector rotation |

**Synthetic stress scenarios:**

| Scenario | Construction | Metric |
|----------|-------------|--------|
| Concentrated gap-down | 10 open positions × 10% overnight gap-down | Portfolio loss (should be < 15% of equity) |
| Correlated sell-off | All positions correlated at 0.9, 2-sigma daily move | Portfolio loss vs. nominal risk |
| Liquidity crisis | ADV drops to 20% of normal for 5 days | Slippage cost; unfilled exits |

**Pass criteria:** No synthetic scenario should produce a portfolio loss exceeding 25% of equity from peak. If it does, reduce `max_positions` or `risk_pct` until the constraint is met.

---

## 12. Implementation Architecture

`[V1+]`

### 12.1 Five-Layer Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 5: Analytics                                                 │
│  ├── Metrics computation (metrics.py)                              │
│  ├── Equity curve construction                                     │
│  ├── Bootstrap / Monte Carlo [V2]                                  │
│  └── Statistical validation suite [V4]                             │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4: Portfolio Simulation [V3]                                 │
│  ├── Day-by-day event loop                                         │
│  ├── Position sizing and capital allocation                        │
│  ├── Correlation and regime modules                                │
│  └── Drawdown circuit breakers                                     │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: Execution Modeling                                        │
│  ├── 3-case fill model (engine.py _simulate_signal) [V1]          │
│  ├── Exit state machine (V1_FIXED, V2_TRAIL_10D, V2_TRAIL_21EMA)  │
│  ├── Max-gap filter, slippage, circuit breakers [V2]               │
│  └── Overlap rule (_simulate_symbol_group) [V1]                    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: Signal Ingestion                                          │
│  ├── BREAKOUT signals from DuckDB                                  │
│  └── OHLCV data from Parquet                                       │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: Data                                                      │
│  ├── NSE Bhavcopy (primary)                                        │
│  ├── Yahoo Finance (fallback)                                      │
│  └── Corporate actions (disabled by default)                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 12.2 Lookahead Prevention by Construction

**Time-frontier pattern.** The single-signal engine processes bars in strict chronological order. The `for offset, bar in enumerate(exit_bars)` loop in `engine.py` ensures that each bar's decisions use only the current bar and prior state.

**Shift direction checks.** Signals use `setup_pass(D-1)` and `shift(1)` to ensure no current-day data leaks into signal generation (see STRATEGY.md Invariants 7 and 8).

**Rolling window data-boundary rules.** Trailing indicators (10-day low, 21-EMA) are computed from the accumulating buffer, never from a pre-computed full-series indicator. The low buffer and EMA state are maintained incrementally within the simulation loop.

**The delay test.** A diagnostic that shifts all signal dates forward by +1 bar and re-runs the backtest. If performance improves significantly, look-ahead bias is present. Performance should degrade slightly (loss of immediate breakout timing) but not catastrophically.

### 12.3 Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Signal processing | Polars | Vectorized filtering and sorting of BREAKOUT signals |
| Trade simulation | Python event loop | Sequential bar-by-bar processing with mutable state |
| Parallel execution | `ProcessPoolExecutor` | Symbol groups are independent; parallelized when >= 8 symbols |
| Data storage | DuckDB (signals), Parquet (OHLCV) | DuckDB for queryable signal history; Parquet for columnar price data |
| Metrics | Python stdlib + Polars | `statistics.median`, list comprehensions, Polars DataFrame for equity curve |

### 12.4 File Layout

```
src/sepa_scanner/backtest/
├── __init__.py          # Package docstring
├── models.py            # ExitMode, BacktestParams, BacktestTrade, BacktestResult,
│                        #   BacktestSummary, BacktestReport
├── engine.py            # _simulate_signal, _simulate_symbol_group,
│                        #   simulate_breakout_backtest
├── metrics.py           # build_equity_curve, summarize_backtest
├── portfolio.py         # [V3] Day-by-day portfolio simulator
├── costs.py             # [V2] Transaction cost and slippage models
├── validation.py        # [V4] DSR, PBO, walk-forward, permutation tests
└── graveyard.py         # [V4] Strategy graveyard (DuckDB persistence)
```

### 12.5 Logging and Reproducibility

**Content-addressable hashing.** Every backtest run is identified by:

```python
run_key = sha256(code_hash + data_hash + params_json)
```

Where:
- `code_hash` = SHA-256 of the backtest engine source files
- `data_hash` = SHA-256 of the input signal DataFrame and OHLCV DataFrame
- `params_json` = canonical JSON serialization of BacktestParams

**Logging.** The backtest engine logs at `INFO` level:
- Run parameters
- Signal count, fill rate
- Summary metrics
- Execution time

At `DEBUG` level:
- Per-trade entry/exit decisions
- Trail activation events
- Overlap skip events

---

## 13. Validation Pipeline

`[V4]`

### 13.1 Four-Phase Validation

Each phase has a pass/fail gate. A strategy must pass all phases before live deployment.

```
Phase 1: In-Sample Development
    ↓ (pass gate 1)
Phase 2: Out-of-Sample Validation
    ↓ (pass gate 2)
Phase 3: Statistical Stress Testing
    ↓ (pass gate 3)
Phase 4: Live Validation (Paper Trading)
    ↓ (pass gate 4)
Live Deployment (graduated)
```

### 13.2 Minimum Thresholds

| Metric | Minimum | Target | Phase |
|--------|---------|--------|-------|
| Deflated Sharpe Ratio (DSR) | > 0.95 (95% confidence) | > 0.99 | Phase 3 |
| Probability of Backtest Overfitting (PBO) | < 30% | < 10% | Phase 3 |
| Walk-Forward Efficiency (WFE) | > 50% | > 60% | Phase 2 |
| t-statistic of alpha | > 3.0 | > 4.0 | Phase 3 |
| OOS Sharpe decay vs IS | < 50% | < 30% | Phase 2 |
| Parameter plateau width | > 20% of range | > 30% | Phase 3 |
| Bootstrap 5th percentile Sharpe | > 0.0 | > 0.3 | Phase 2 |
| Monte Carlo P95 max drawdown | < 2x realized | < 1.5x realized | Phase 2 |

### 13.3 Live Validation Protocol

**Paper trading period:** Minimum 6 months (or 30 completed trades, whichever is longer).

**Graduated deployment:**

| Stage | Duration | Capital Allocation | Purpose |
|-------|----------|-------------------|---------|
| Paper trade | 6 months | 0% real capital | Validate signal-to-execution pipeline |
| Stage 1 | 3 months | 25% of target allocation | Test with real fills and slippage |
| Stage 2 | 3 months | 50% of target allocation | Validate position sizing and correlation |
| Stage 3 | 3 months | 100% of target allocation | Full deployment |

**At each stage transition, verify:**
- Realized metrics are within 50% of backtest metrics (budget for alpha decay)
- Fill rate is within 20% of backtest fill rate
- Slippage is within 2x of the V2 slippage model estimate
- No unexpected regime changes since backtest period

### 13.4 Alpha Decay Budget

Budget for 30-50% alpha decay from backtest to live execution. Sources of decay:

| Source | Estimated Impact | Mitigation |
|--------|-----------------|------------|
| Slippage underestimation | 10-20% | V2 slippage model; calibrate to real fills |
| Transaction costs | 5-15% | V2 cost model; choose liquid universe |
| Signal delay (human reaction time) | 5-10% | Automate alert-to-order pipeline |
| Regime shift | 0-30% | Regime-conditional sizing; drawdown breakers |
| Data-mining bias | 0-20% | DSR, PBO, walk-forward validation |

---

## 14. Configuration Reference

`[V1+]`

### 14.1 BacktestParams (V1 — Current)

| Field | Config Key | Type | Default | Version | Description |
|-------|-----------|------|---------|---------|-------------|
| `pending_days` | `backtest.pending_days` | `int` | `5` | V1 | Trading days to wait for entry trigger |
| `max_hold_days` | `backtest.max_hold_days` | `int` | `90` | V1 | Maximum holding period before time stop |
| `target_r` | `risk.target_r` | `float \| None` | `None` | V1 | Profit target as R-multiple; None = no fixed target |
| `exit_mode` | `backtest.exit_mode` | `ExitMode` | `MINERVINI_PHASED` | V1 | Supported default exit strategy; legacy comparison modes remain available internally |
| `trail_activation_r` | `backtest.trail_activation_r` | `float` | `2.0` | V1 | R-multiple profit required to activate trailing stop |

**Validation rules (enforced in `__post_init__`):**
- `pending_days > 0`
- `max_hold_days > 0`
- `target_r > 0` when provided (None is valid)
- `trail_activation_r > 0`

### 14.2 V2 Configuration Additions

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `backtest.max_gap_pct` | `float` | `0.05` | Maximum gap-through percentage before rejection |
| `backtest.min_rr_after_gap` | `float` | `2.0` | Minimum R:R ratio after gap-through re-evaluation |
| `backtest.slippage_k` | `float` | `0.75` | Square-root impact model calibration constant |
| `backtest.costs_enabled` | `bool` | `false` | Enable transaction cost deduction |
| `backtest.round_trip_cost_pct` | `float` | `0.50` | Flat round-trip cost percentage |
| `backtest.dp_charge_inr` | `float` | `15.93` | DP charge per sell transaction (INR) |
| `backtest.use_impact_cost_model` | `bool` | `false` | Use segment-based impact cost model |
| `backtest.allow_t2t` | `bool` | `false` | Allow T2T/BE series stocks in backtest |

### 14.3 V3 Portfolio Configuration

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `portfolio.initial_capital` | `float` | `1_000_000` | Starting equity in INR |
| `portfolio.risk_pct` | `float` | `0.01` | Risk per trade as fraction of equity |
| `portfolio.max_positions` | `int` | `15` | Maximum concurrent open positions |
| `portfolio.max_position_pct` | `float` | `0.25` | Maximum single position as fraction of equity |
| `portfolio.max_sector_pct` | `float` | `0.30` | Maximum sector concentration |
| `portfolio.max_heat_pct` | `float` | `0.15` | Maximum total portfolio risk |
| `portfolio.regime_enabled` | `bool` | `true` | Enable regime-conditional exposure |
| `portfolio.drawdown_breakers` | `bool` | `true` | Enable drawdown circuit breakers |

### 14.4 V4 Validation Configuration

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `validation.bootstrap_n` | `int` | `10_000` | Number of bootstrap resamples |
| `validation.monte_carlo_n` | `int` | `5_000` | Number of Monte Carlo reshuffles |
| `validation.cscv_blocks` | `int` | `16` | Number of blocks for CSCV (PBO) |
| `validation.wf_steps` | `int` | `4` | Minimum walk-forward steps |
| `validation.wf_is_pct` | `float` | `0.70` | In-sample fraction for walk-forward |
| `validation.permutation_n` | `int` | `10_000` | Number of permutation test trials |
| `validation.min_dsr` | `float` | `0.95` | Minimum Deflated Sharpe Ratio |
| `validation.max_pbo` | `float` | `0.30` | Maximum Probability of Backtest Overfitting |
| `validation.min_wfe` | `float` | `0.50` | Minimum Walk-Forward Efficiency |
| `validation.min_t_stat` | `float` | `3.0` | Minimum t-statistic for alpha |

---

## 15. Data Schemas

`[V1+]`

### 15.1 BacktestTrade `[V1]`

Current dataclass matching `backtest/models.py`:

| Field | Type | Description | Version |
|-------|------|-------------|---------|
| `symbol` | `str` | Stock symbol | V1 |
| `signal_date` | `date` | Date the BREAKOUT signal was generated | V1 |
| `status` | `str` | Trade status (see Section 15.9 of STRATEGY.md) | V1 |
| `entry_ref` | `float` | Reference entry price from the signal | V1 |
| `stop` | `float` | Initial stop loss price | V1 |
| `pivot` | `float \| None` | Pivot price from the signal (if available) | V1 |
| `entry_date` | `date \| None` | Actual entry date (None if not filled) | V1 |
| `entry_price` | `float \| None` | Actual entry price (None if not filled) | V1 |
| `exit_date` | `date \| None` | Exit date (None if not closed) | V1 |
| `exit_price` | `float \| None` | Exit price (None if not closed) | V1 |
| `holding_days` | `int \| None` | Number of bars held | V1 |
| `return_pct` | `float \| None` | Percentage return | V1 |
| `r_multiple` | `float \| None` | R-multiple of the trade | V1 |
| `mae_price` | `float \| None` | Maximum Adverse Excursion price while the trade was still open | V1 |
| `mfe_price` | `float \| None` | Maximum Favorable Excursion price while the trade was still open | V1 |
| `mae_r` | `float \| None` | MAE in R-units | V1 |
| `mfe_r` | `float \| None` | MFE in R-units | V1 |
| `giveback_pct` | `float \| None` | Fraction of MFE given back (computed for all trades with mfe_r > 0; most meaningful for winners) | V1 |

**V2 additions (planned):**

| Field | Type | Description | Version |
|-------|------|-------------|---------|
| `slippage_bps` | `float \| None` | Estimated slippage in basis points | V2 |
| `transaction_cost` | `float \| None` | Total transaction cost in INR | V2 |
| `gap_pct` | `float \| None` | Gap percentage for gap-through entries | V2 |
| `circuit_delayed` | `bool` | Whether exit was delayed by circuit limit | V2 |

### 15.2 BacktestSummary `[V1]`

Current dataclass matching `backtest/models.py`:

| Field | Type | Default | Description | Version |
|-------|------|---------|-------------|---------|
| `total_signals` | `int` | — | Count of all signals | V1 |
| `filled_trades` | `int` | — | Count with entry_date | V1 |
| `not_filled_signals` | `int` | — | Count with status `not_filled` | V1 |
| `stopped_trades` | `int` | — | Count with status `stopped` | V1 |
| `target_exits` | `int` | — | Count with status `target` | V1 |
| `time_stop_exits` | `int` | — | Count with status `time_stop` | V1 |
| `win_rate` | `float` | — | Winners / closed trades | V1 |
| `average_return_pct` | `float \| None` | — | Mean return percentage | V1 |
| `average_r` | `float \| None` | — | Mean R-multiple | V1 |
| `median_r` | `float \| None` | — | Median R-multiple | V1 |
| `expectancy` | `float \| None` | — | Same as average_r in V1 | V1 |
| `payoff_ratio` | `float \| None` | — | avg_win_r / abs(avg_loss_r) | V1 |
| `cumulative_r` | `float` | — | Sum of R-multiples | V1 |
| `max_drawdown` | `float` | — | Min drawdown on equity curve | V1 |
| `longest_losing_streak` | `int` | — | Max consecutive losses (r < 0) | V1 |
| `avg_mfe_r` | `float \| None` | `None` | Mean MFE in R-units | V1 |
| `avg_mae_r` | `float \| None` | `None` | Mean MAE in R-units | V1 |
| `avg_giveback_pct` | `float \| None` | `None` | Mean giveback (winners) | V1 |
| `exit_mode` | `str` | `"v2_trail_21ema"` | Exit mode used | V1 |

**V2 additions (planned):**

| Field | Type | Description | Version |
|-------|------|-------------|---------|
| `expectancy_ci_lower` | `float \| None` | Bootstrap 95% CI lower bound | V2 |
| `expectancy_ci_upper` | `float \| None` | Bootstrap 95% CI upper bound | V2 |
| `expectunity` | `float \| None` | Expectancy x trades/year | V2 |
| `profit_factor` | `float \| None` | Gross profit / gross loss | V2 |
| `sortino_r` | `float \| None` | Sortino ratio (R-based) | V2 |
| `tail_ratio` | `float \| None` | P95 / abs(P5) | V2 |
| `total_cost_inr` | `float \| None` | Total transaction costs | V2 |
| `mc_p95_drawdown` | `float \| None` | Monte Carlo P95 max drawdown | V2 |

### 15.3 BacktestReport `[V1]`

| Field | Type | Description | Version |
|-------|------|-------------|---------|
| `params` | `BacktestParams` | Parameters used for the run | V1 |
| `summary` | `BacktestSummary` | Aggregated summary metrics | V1 |
| `trades` | `pl.DataFrame` | All trades as a Polars DataFrame | V1 |
| `equity_curve` | `pl.DataFrame` | Equity curve DataFrame | V1 |

### 15.4 Equity Curve DataFrame Schema `[V1]`

| Column | Polars Type | Description |
|--------|------------|-------------|
| `exit_date` | `pl.Date` | Trade exit date |
| `symbol` | `pl.String` | Stock symbol |
| `r_multiple` | `pl.Float64` | Individual trade R-multiple |
| `cumulative_r` | `pl.Float64` | Running cumulative R |
| `running_peak` | `pl.Float64` | High-water mark of cumulative R |
| `drawdown` | `pl.Float64` | `cumulative_r - running_peak` (always <= 0) |

### 15.5 Strategy Graveyard Table Schema `[V4]`

DuckDB table `backtest_runs`:

| Column | DuckDB Type | Description |
|--------|------------|-------------|
| `run_id` | `UUID` | Unique run identifier |
| `timestamp` | `TIMESTAMP` | Execution timestamp |
| `code_hash` | `VARCHAR` | SHA-256 of backtest engine source |
| `data_hash` | `VARCHAR` | SHA-256 of input data |
| `params_json` | `JSON` | Full parameter set |
| `metrics_json` | `JSON` | Full summary metrics |
| `is_production` | `BOOLEAN` | Production vs exploratory flag |
| `notes` | `VARCHAR` | Free-form annotation |

---

## 16. Invariants

`[V1+]`

### Backtest-Specific Invariants

These are hard constraints. Any change to these invariants MUST be reflected in this document first.

**Carried forward from STRATEGY.md (Invariants 14-18):**

- **Invariant 14:** Backtest assumes worse outcome when both stop and target are hit on the same candle
- **Invariant 15:** Backtest overlap rule — later same-symbol signals are skipped only while a prior trade is still open; signals on the exit date are allowed
- **Invariant 16:** V2 trailing stops use D-1 data — stop used for day t decisions was computed from data <= day t-1
- **Invariant 17:** V2 trailing stops only ratchet up — `active_stop = max(active_stop, trail_value)`
- **Invariant 18:** MAE/MFE use initial risk denominator — R-unit excursion metrics always reference the original entry-to-stop risk

**New backtest-specific invariants:**

19. **R-unit is always computed from initial entry-to-stop risk.** The denominator `(entry_price - stop)` is fixed at entry and never recalculated, even if the trailing stop moves. All R-multiples, MAE_R, MFE_R, and giveback_pct use this initial risk.

20. **Gap-below-stop exit is checked before intraday exit checks, but not on the entry day.** The `is_entry_day` guard prevents false stops from gap-down opens on the fill day.

21. **Gap-open exits cannot observe post-exit intraday range.** On a gap-open stop or gap-open target, MAE/MFE may use the open price itself, but not the later high/low of that exit bar.

22. **Transaction costs are opt-in.** Default is `costs_enabled = false` for V1 backward compatibility. Enabling costs does not change the V1 single-signal engine — costs are applied as a post-processing adjustment.

23. **Portfolio simulation is a separate engine.** The V3 portfolio simulator wraps the V1/V2 trade engine but does not modify its behavior. A single-signal backtest must produce identical results whether or not the portfolio simulator is active.

24. **All new BacktestParams fields preserve explicit mode behavior.** Adding a V2/V3/V4 parameter must not change the output of an existing exit mode when that exit mode is explicitly selected.

25. **Equity curve sort order is deterministic.** Trades are sorted by `(exit_date, symbol, signal_date)` before computing cumulative R and drawdown. This ensures reproducible equity curves.

26. **Closed trades are defined as trades with both `exit_date is not None` and `r_multiple is not None`.** All summary metrics are computed only over closed trades, including end-of-data force-closures with status `incomplete_data`.

27. **The EMA-21 alpha is exactly `2 / (21 + 1) = 2/22`.** This matches the standard EMA formula and the constant `_EMA21_ALPHA` in `engine.py`.

---

## 17. Change Log

Track every backtesting methodology change here. Include date, what changed, why, and who/what made the change.

| Date | Change | Reason | Author |
|------|--------|--------|--------|
| 2026-03-14 | Initial creation of BACKTESTING.md | Full backtesting methodology specification — V1 through V4 roadmap | AI-assisted |

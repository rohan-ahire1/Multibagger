# Stocks on the Move — Agent-Ready Implementation Specification

**Source:** Andreas F. Clenow, *Stocks on the Move* (2015)  
**Purpose:** Convert the book into an exact, testable implementation contract for a systematic equity momentum bot.  
**Important:** This is a medium-term, long-only momentum portfolio strategy. It is not a conventional 2–10 day swing-entry system.

---

## 1. Executive decision

Implement this strategy, but in three separate layers:

1. **Book-faithful baseline** — reproduce the rules without optimisation.
2. **Execution-realistic NSE version** — add point-in-time Indian data, costs, liquidity, price-band and fill constraints.
3. **Research variants** — test robustness only after the baseline is locked.

Do not directly deploy the strategy extracted from the book. The book’s reported result is a historical US large-cap simulation ending around 2015, with important execution and cost details unspecified.

---

## 2. What the strategy is exploiting

The strategy assumes:

- Stocks with strong medium-term momentum tend to continue outperforming for some time.
- Smooth momentum is preferable to momentum produced by a single shock or highly erratic movement.
- Equity portfolios remain heavily exposed to the broad market.
- A broad-market trend filter reduces catastrophic bear-market exposure.
- Risk-balanced sizing is superior to equal cash allocation.
- Weak holdings should be replaced by stronger peers rather than managed with static stop-losses.

The system makes money from:

1. Cross-sectional stock momentum.
2. Continually replacing deteriorating stocks.
3. Risk-parity-like position sizing.
4. Gradually reducing exposure during bear markets.
5. Avoiding large drawdowns rather than predicting exact market bottoms.

---

## 3. Canonical parameters from the book

| Parameter | Canonical value |
|---|---:|
| Data frequency | Daily |
| Decision frequency | Weekly |
| Decision weekday | Wednesday; arbitrary |
| Momentum window | 90 trading sessions |
| Momentum measure | Annualised exponential-regression slope × R² |
| Stock trend filter | Close > SMA100 |
| Broad-market filter | Index close > SMA200 |
| Gap window | 90 sessions |
| Gap exclusion | Any gap/move > 15% |
| Volatility measure | ATR20 |
| Position risk factor | 0.001 = 10 basis points |
| Hold rank requirement | Top 20% of universe |
| Portfolio replacement | Weekly |
| Position-size rebalance | Every second weekly cycle |
| Rebalance tolerance | Book later suggests 5% |
| Stop-loss | None |
| Shorting | None |
| Leverage | None in canonical strategy |

---

## 4. Data contract

### 4.1 Required stock data

For each security and session:

- Security ID stable across ticker renames, preferably ISIN.
- Date.
- Raw open, high, low, close.
- Adjusted open, high, low, close.
- Volume.
- Corporate-action adjustment factor.
- Dividend.
- Split/bonus/consolidation event.
- Listing date.
- Delisting date and reason.
- Tradability status.
- Historical index membership.
- Sector and industry.
- Price-band/circuit status where available.

### 4.2 Required index data

For the chosen universe index:

- Daily price index for market-regime calculation.
- Total-return index for performance comparison.
- Point-in-time constituents with effective dates.

For India, the closest book-faithful mapping is:

- **Universe:** historical Nifty 500 membership.
- **Regime filter:** Nifty 500 price index vs SMA200.
- **Benchmark:** Nifty 500 TRI.

Do not use current Nifty 500 constituents retrospectively.

### 4.3 Corporate-action treatment

The ranking must use a continuous total-return-adjusted price series. Raw prices must remain separately available for execution simulation.

Use:

- Adjusted series for momentum, moving averages and historical return calculations.
- Raw tradable prices for order fills.
- Explicit dividend cashflows or a consistently adjusted total-return series.
- No incremental adjustment process that leaves old bars on an earlier adjustment basis.

### 4.4 Mandatory bias controls

The backtest must include:

- Historical constituents.
- Delisted and bankrupt securities.
- Mergers and ticker changes.
- No future membership information.
- No future corporate-action information.
- No signal before all indicator windows are complete.
- No forward-filled price bars interpreted as trading sessions.
- No fills on zero-volume, suspended or circuit-locked sessions.

---

## 5. Exact indicator formulas

### 5.1 Exponential-regression momentum

For the latest `N = 90` adjusted closing prices:

```text
x_t = 0, 1, ..., N - 1
y_t = ln(adjusted_close_t)
```

Fit ordinary least squares:

```text
y_t = alpha + beta * x_t + error_t
```

Daily exponential slope:

```text
daily_growth = exp(beta) - 1
```

Annualised slope:

```text
annualised_slope = exp(beta * 250) - 1
```

Equivalent:

```text
annualised_slope = (exp(beta) ** 250) - 1
```

Calculate the regression coefficient of determination:

```text
r_squared = R²(y, fitted_y)
```

Final ranking score:

```text
momentum_score = annualised_slope * r_squared
```

Sort descending. Higher is better.

Implementation safeguards:

- Reject non-positive adjusted prices before taking logs.
- Require exactly 90 valid prior sessions.
- Use only information available at signal timestamp.
- Use deterministic tie-breaking: score descending, then stable security ID ascending.

### 5.2 Stock trend filter

```text
stock_eligible_trend = adjusted_close > SMA(adjusted_close, 100)
```

Canonical interpretation uses a simple moving average.

### 5.3 Broad-market trend filter

```text
market_allows_new_buys = index_close > SMA(index_close, 200)
```

Critical behaviour:

- This filter blocks **new purchases**.
- It does **not** immediately liquidate all current holdings.
- Existing positions leave only through normal stock-level exit rules.
- This creates gradual exposure reduction during a bear market.
- When the index becomes positive again, rebuild toward a full portfolio immediately.

### 5.4 ATR20

True range:

```text
TR_t = max(
    high_t - low_t,
    abs(high_t - close_{t-1}),
    abs(low_t - close_{t-1})
)
```

Book-faithful ATR interpretation:

```text
ATR20 = mean(TR over previous 20 sessions)
```

The book says “average” and does not explicitly specify Wilder smoothing. Keep simple ATR as the baseline; test Wilder ATR only as a robustness variant.

### 5.5 Position size

```text
target_shares = floor(account_equity * risk_factor / ATR20)
risk_factor = 0.001
```

This targets an average daily position impact of approximately 0.1% of portfolio equity per stock.

Target market value:

```text
target_value = target_shares * execution_reference_price
```

Target portfolio weight:

```text
target_weight = target_value / account_equity
```

Expected portfolio size is normally about 20–30 stocks under the book’s US large-cap assumptions.

### 5.6 Gap filter — unresolved book ambiguity

The book repeatedly says both:

- “move larger than 15%”
- “gap larger than 15%”

It does not give an exact mathematical formula.

Implement and report both:

**Variant A — close-to-close shock**

```text
gap_metric_t = abs(adjusted_close_t / adjusted_close_{t-1} - 1)
```

**Variant B — overnight opening gap**

```text
gap_metric_t = abs(raw_open_t / raw_close_{t-1} - 1)
```

Candidate is disqualified when:

```text
max(gap_metric over last 90 sessions) > 0.15
```

Recommended baseline for replication: Variant A, because the book describes the rule as the largest daily “move” and builds the model entirely from end-of-day data. Preserve Variant B as a mandatory sensitivity test.

Do not silently combine the definitions. The final report must show the performance impact of both.

---

## 6. Universe and ranking

On each decision date:

1. Determine the exact point-in-time universe.
2. Require sufficient history for all indicators.
3. Calculate the momentum score for every eligible stock.
4. Sort all universe members descending by score.
5. Calculate percentile rank using the complete point-in-time universe.
6. Top 20% means:

```text
rank <= ceil(universe_size * 0.20)
```

For a 500-stock universe, this means rank 1–100.

A stock may be highly ranked but still ineligible because:

- Close <= SMA100.
- Gap/shock > 15% in prior 90 sessions.
- It is no longer in the index.
- It is not tradable.
- It lacks sufficient data.

---

## 7. Weekly portfolio algorithm

### 7.1 Timing

Book logic:

- Indicators are calculated on daily data.
- Rules are evaluated once weekly.
- Wednesday is arbitrary.

Look-ahead-safe implementation:

- Calculate signals using completed session `T`.
- Submit orders for session `T+1`.
- Baseline execution price: next-session raw open.
- Also test next-session VWAP or open plus adverse slippage.

Holiday rule:

- Choose one consistent weekly anchor.
- When the anchor is a holiday, evaluate on the next available session.
- Never use future data to decide that session.

### 7.2 Processing order

Use this order every weekly decision:

1. Build point-in-time universe and ranking.
2. Generate sell orders.
3. Execute/allocate proceeds according to execution timing.
4. On biweekly position-rebalance cycles, resize surviving holdings.
5. If market filter permits new buys, buy replacements from highest rank downward.
6. Stop when remaining cash cannot fund another valid target position.

The book’s printed flowchart appears inconsistent because one branch can be read as skipping weekly replacement buys on non-position-rebalance weeks. The prose in multiple chapters clearly states that portfolio replacement occurs every week and position-size rebalancing is merely an additional biweekly task. Follow the prose.

---

## 8. Sell rules

Sell the complete position when any condition is true:

```text
not current_index_member
OR rank_percentile > 20%
OR adjusted_close <= SMA100
OR max_gap_90 > 15%
OR security becomes permanently untradable/delisted
```

Important:

- No fixed stop-loss.
- No trailing stop-loss.
- No take-profit.
- No maximum holding period.
- A profitable position is sold if it becomes relatively weak.
- A losing position can remain if it remains highly ranked and above SMA100.
- A stock is sold after a large favourable gap as well as after a harmful gap.

### Delisting handling

Do not close a vanished ticker at its last known favourable price.

Model one of:

- Actual delisting cash consideration.
- Merger conversion terms.
- Last executable price before suspension.
- Recovery value.
- Total loss where appropriate.

Flag uncertain terminal values separately.

---

## 9. Buy rules

A new position may be opened only when:

```text
index_close > index_SMA200
AND candidate is in top 20%
AND adjusted_close > stock_SMA100
AND max_gap_90 <= 15%
AND candidate is a point-in-time universe member
AND candidate is tradable
AND candidate is not already held
```

Selection process:

1. Start at rank 1.
2. Skip held or disqualified securities.
3. Calculate target shares.
4. Check liquidity and available cash.
5. Buy full target size.
6. Continue until no further full target position can be funded.

Book ambiguity: treatment of the final partially fundable position is unspecified.

Canonical baseline:

```text
Do not open a position unless its full target size can be funded.
```

Sensitivity variant:

```text
Allow the final position to be reduced to available cash.
```

---

## 10. Position-size rebalancing

Every second weekly cycle:

1. Recalculate account equity.
2. Recalculate ATR20 for each surviving holding.
3. Recalculate target shares.
4. Compare actual vs target.
5. Adjust up or down.

Suggested threshold from the book’s simulation chapter:

```text
abs(target_value - current_value) / target_value >= 5%
```

Only then rebalance.

This threshold is not part of the initial “exact rules” list; it is later presented as a way to reduce turnover. Therefore:

- Baseline A: rebalance any non-zero whole-share difference.
- Realistic baseline B: use the 5% tolerance.
- Compare turnover and after-cost performance.

Position resizing is risk maintenance, not a market opinion.

---

## 11. What should not be added to the baseline

Do not initially add:

- RSI.
- MACD.
- Breakout entries.
- Fixed stop-loss.
- Trailing stop.
- Profit target.
- Earnings filter.
- Fundamental score.
- Sector rotation.
- Machine learning.
- Optimised weekday.
- Optimised parameter values.
- Short positions.
- Leverage.

First reproduce the simple concept. Add improvements only in isolated experiments.

---

## 12. Book-reported historical results

Approximate book simulation period: 1999–2015, S&P 500 historical constituents.

| Model | Annualised return | Maximum drawdown |
|---|---:|---:|
| Canonical momentum strategy | 12.3% | -24.0% to -24.3% |
| S&P 500 Total Return | 5.2% | -55.3% |
| Momentum without index trend filter | 9.1% | -50.9% |

### Momentum-window stability

| Window | Annualised return | Maximum drawdown |
|---|---:|---:|
| 60 sessions | 10.8% | -28.3% |
| 90 sessions | 12.3% | -24.0% |
| 120 sessions | 12.5% | -24.6% |
| 240 sessions | 11.6% | -26.3% |

### Ranking-method comparison

| Ranking method | Annualised return | Maximum drawdown |
|---|---:|---:|
| Annualised regression × R² | 12.4% | -24.3% |
| Raw 90-day percentage return | 12.7% | -26.2% |
| Pure annualised regression | 12.9% | -24.1% |

Book conclusion: ranking formula is not responsible for most of the total edge. The R²-adjusted version mainly avoids uncomfortable one-off shock situations.

### Position-risk comparison

| Risk factor | Typical breadth | Annualised return | Maximum drawdown |
|---|---:|---:|---:|
| 5 bp | 40–50+ stocks | 9.0% | -20.4% |
| 10 bp | 20–30 stocks | 12.3% | -23.8% |
| 50 bp | 5–6 stocks | 16.3% | -40.3% |

Book conclusion: concentrated portfolios can backtest better but have unacceptable stock-specific event risk. Ten basis points is a pragmatic compromise, not an optimised constant.

---

## 13. Material limitations in the book

Do not use 12.3% CAGR as an expected live return.

The book does not clearly specify:

- Core simulation commissions.
- Slippage.
- Bid–ask spread.
- Exact fill price.
- Whether signals trade at same close or next open.
- Exact gap formula.
- ATR smoothing convention.
- Final partial-position handling.
- Exact position-rebalance deviation threshold in the canonical run.
- Tax impact.
- Cash yield treatment in every result.
- Liquidity/capacity limits.
- Price-band or halted-stock handling.
- Point-in-time availability delay of index changes.
- Realistic delisting liquidation mechanics.

Other limitations:

- One primary country.
- One primary universe.
- Large-cap stocks.
- Approximately 16 years.
- Backtest ended around 2015.
- No true untouched post-publication validation in the book.
- No walk-forward process.
- No multiple-testing correction.
- No modern Indian market microstructure.

---

## 14. NSE implementation additions

These are not book rules. They are required for a tradable Indian implementation.

### 14.1 Liquidity

Require:

- Positive volume.
- Positive traded value.
- No suspension.
- No unfillable circuit condition.
- Order notional below a fixed percentage of median daily traded value.
- Participation-rate stress tests.

Suggested research constraint:

```text
order_value <= 2% to 5% of 20-day median traded value
```

This is a testing range, not an optimised parameter.

### 14.2 Costs

Model every applicable cost:

- Brokerage.
- STT.
- Exchange transaction charges.
- SEBI charges.
- GST.
- Stamp duty.
- Bid–ask spread.
- Slippage.
- Impact from order size.
- DP charges where applicable.

Run:

- Base expected costs.
- 1.5× cost stress.
- 2× cost stress.

### 14.3 Price bands and execution

- No buy fill at upper circuit without available sell liquidity.
- No sell fill at lower circuit without available buy liquidity.
- No fill on zero-volume bars.
- Carry unfilled orders with an explicit expiry policy.
- Do not assume all orders execute at opening price.

### 14.4 Sector exposure

The book does not require a sector cap.

For production risk, research:

- No cap.
- 25% sector cap.
- 30% sector cap.

Do not select the best historical cap. Prefer the weakest intervention that materially reduces concentration risk without destroying the edge.

### 14.5 Cash

When the strategy scales out:

- Hold cash or a documented liquid cash-equivalent instrument.
- Model its actual return and costs.
- Do not silently give cash a return unless explicitly simulated.

---

## 15. Your current data stack: deployment blocker

Your existing NSE audit already found:

- Critical survivorship bias from missing retired tickers.
- Adjustment-basis drift from incremental Yahoo updates.
- Corrupt adjusted-price windows.
- Tradable fills on zero-volume bars.
- Ticker identity issues.
- Unrealistic delisting handling.

These problems are especially destructive for this strategy because it:

- Ranks every stock cross-sectionally.
- Is long-only.
- Depends on historical universe membership.
- Uses adjusted prices for smoothness and momentum.
- Can accidentally rank corporate-action artifacts at the top.
- Requires realistic treatment of failed and delisted stocks.

Therefore:

```text
DO NOT trust absolute CAGR from this strategy on the current dataset.
```

Minimum prerequisites:

1. Reconstruct a survivor-complete symbol pool from NSE archives.
2. Store raw OHLC and an explicit adjustment factor.
3. Rebuild adjusted history consistently.
4. Add ISIN-keyed security identity.
5. Implement realistic delisting outcomes.
6. Enforce tradability and zero-volume checks.
7. Use a frozen, reproducible data snapshot.
8. Re-audit before strategy evaluation.

---

## 16. Backtest architecture

Use a portfolio-level event-driven engine.

Wrong architecture:

```text
Run all years for stock A
Then all years for stock B
Then combine trades
```

Correct architecture:

```text
For each market session T:
    Load all data known by T
    Update indicators for all securities
    Determine point-in-time universe
    If weekly decision session:
        Generate portfolio-level orders
    Execute previously generated orders using T prices
    Apply corporate actions and cashflows
    Mark portfolio to market
    Record state
```

Required state:

- Cash.
- Current positions.
- Average cost.
- Pending orders.
- Security master.
- Universe membership.
- Adjustment factors.
- Portfolio equity.
- Target shares.
- Rank history.
- Exit reason.
- Fill reason.
- Rejected/unfilled reason.

---

## 17. Deterministic pseudocode

```python
for session in trading_calendar:

    apply_corporate_actions(session)
    process_pending_orders(session)
    mark_to_market(session)

    if not is_weekly_signal_session(session):
        continue

    snapshot = build_point_in_time_snapshot(session)

    universe = [
        s for s in snapshot.securities
        if s.is_index_member
        and s.has_required_history
        and s.has_valid_prices
    ]

    for s in universe:
        s.score = annualised_log_regression_slope(
            adjusted_close=s.adjusted_close[-90:],
            annualisation=250,
        ) * regression_r_squared(
            adjusted_close=s.adjusted_close[-90:]
        )

        s.stock_trend_ok = s.adjusted_close[-1] > sma(
            s.adjusted_close, 100
        )

        s.max_gap_90 = gap_metric(s)[-90:].max()

        s.atr20 = simple_atr(s.raw_ohlc, 20)

    ranked = deterministic_sort(universe, key=(-score, security_id))
    assign_cross_sectional_rank(ranked)

    sell_orders = []

    for position in portfolio.positions:
        row = snapshot.get(position.security_id)

        if (
            row is None
            or not row.is_index_member
            or row.rank_percentile > 0.20
            or not row.stock_trend_ok
            or row.max_gap_90 > 0.15
            or not row.tradable
        ):
            sell_orders.append(full_exit(position))

    submit_for_next_session(sell_orders)

    if is_position_rebalance_cycle(session):
        resize_orders = []

        projected_equity = portfolio.equity

        for position in surviving_positions_after_planned_sells():
            row = snapshot[position.security_id]

            target_shares = floor(
                projected_equity * 0.001 / row.atr20
            )

            if deviation_exceeds_threshold(
                current_shares=position.shares,
                target_shares=target_shares,
                threshold=0.05,
            ):
                resize_orders.append(
                    order_to_target(position, target_shares)
                )

        submit_for_next_session(resize_orders)

    market_positive = (
        snapshot.index_close >
        snapshot.index_sma200
    )

    if not market_positive:
        continue

    projected_cash = cash_after_pending_sells_and_resizes()

    for candidate in ranked:
        if candidate.security_id in projected_holdings:
            continue

        if candidate.rank_percentile > 0.20:
            break

        if not candidate.stock_trend_ok:
            continue

        if candidate.max_gap_90 > 0.15:
            continue

        if not candidate.tradable:
            continue

        target_shares = floor(
            projected_equity * 0.001 / candidate.atr20
        )

        if target_shares <= 0:
            continue

        estimated_cost = estimate_next_session_cost(
            candidate, target_shares
        )

        if estimated_cost > projected_cash:
            continue

        submit_for_next_session(
            buy_order(candidate, target_shares)
        )

        projected_cash -= estimated_cost
```

---

## 18. Test suite

### 18.1 Indicator unit tests

- Regression slope matches a known synthetic exponential series.
- R² = 1 for a perfect log-linear series.
- R² near 0 for noise.
- Annualisation formula correct.
- ATR20 correct for synthetic OHLC.
- SMA100 and SMA200 require full windows.
- Gap metric handles split-adjusted and raw prices correctly.
- No log of zero or negative price.

### 18.2 Point-in-time tests

- Stock cannot be selected before listing.
- Stock cannot remain eligible after index exit.
- Future constituent changes unavailable.
- Renamed ticker retains same security identity.
- Delisted securities remain in historical simulation.
- No use of current constituent snapshot for history.

### 18.3 Timing tests

- Signal on T cannot fill on T.
- Earliest fill is T+1.
- Friday/holiday boundaries handled correctly.
- Unavailable/circuit-locked bar does not fill.
- No future ATR, SMA or rank values.

### 18.4 Portfolio tests

- Sells processed before buys.
- Market filter blocks buys but does not force liquidation.
- Rank exit uses the complete point-in-time universe.
- Full position exit for every exit condition.
- Position resize does not accidentally change investment signal.
- Cash never becomes negative unless explicitly permitted.
- Share count always integer.
- Deterministic results from identical inputs.

### 18.5 Data-quality canaries

- Known delisted tickers present.
- No duplicate security/date rows.
- No unexplained >2× adjustment discontinuity.
- No non-positive adjusted prices.
- No fills on zero-volume bars.
- OHLC invariants hold.
- Dataset checksum reproducible.

---

## 19. Required experiments

Lock a baseline before experimentation.

### 19.1 Baseline replication

Run exactly:

```text
90-day regression × R²
SMA100 stock filter
SMA200 market filter
15% gap filter
ATR20
10 bp risk factor
Top 20% hold cutoff
Weekly replacement
Biweekly position resizing
Long-only
```

### 19.2 Robustness matrix

Test broad neighbouring concepts, not every integer:

Momentum window:

```text
60, 90, 120, 180, 240
```

Stock trend:

```text
SMA80, SMA100, SMA120
```

Market trend:

```text
SMA150, SMA200, SMA250
```

Rank exit:

```text
Top 10%, 15%, 20%, 25%, 30%
```

Risk factor:

```text
5 bp, 8 bp, 10 bp, 12 bp, 15 bp
```

Position rebalance:

```text
Weekly, biweekly, monthly
```

Execution:

```text
T+1 open
T+1 VWAP
T+1 open + adverse 25 bp
T+1 open + adverse 50 bp
```

Gap definition:

```text
Close-to-close shock
Open-to-previous-close gap
```

Do not choose parameters solely by highest CAGR.

### 19.3 Validation periods

Use chronological partitions:

1. Development.
2. Validation.
3. Final untouched holdout.
4. Post-publication period.
5. Paper-trading period.

Also test:

- Bull markets.
- Slow bear markets.
- Sudden crashes.
- Sideways markets.
- Momentum crashes.
- High-rate and low-rate regimes.
- Large-cap and mid-cap universes separately.

---

## 20. Strategy acceptance criteria

A strategy is eligible for paper trading only when:

- It remains profitable after all costs.
- It beats its relevant TRI benchmark on risk-adjusted metrics.
- It survives 2× cost stress.
- It survives adverse execution assumptions.
- Results are not dependent on one momentum window.
- Results are not dependent on one market moving average.
- Results remain acceptable with both gap definitions.
- Most return is not produced by a few data errors or stocks.
- No single sector explains most of the return.
- No small number of years explains the whole edge.
- Delisted-stock inclusion does not destroy results.
- Parameter surfaces are broad rather than spiky.
- Untouched holdout remains positive.
- Paper trading resembles simulated turnover, fills and exposure.

Recommended report metrics:

- CAGR.
- Maximum drawdown.
- Calmar.
- Sharpe and Sortino.
- Volatility.
- Exposure.
- Cash percentage.
- Turnover.
- Average holdings.
- Average holding period.
- Win rate, but not as a primary metric.
- Profit concentration by stock/year/sector.
- Cost drag.
- Slippage drag.
- Benchmark beta.
- Downside capture.
- Recovery time.
- Worst rolling 12-, 24- and 36-month returns.

---

## 21. Failure conditions

Reject or redesign when:

- Profit disappears after realistic costs.
- Current constituent data was used historically.
- Delisted names are missing.
- Results depend on same-close execution.
- One exact parameter produces nearly all performance.
- A few corporate-action artifacts dominate ranking.
- Strategy cannot exit lower-circuit or suspended stocks realistically.
- Rebalancing turnover is impractical.
- 200-day filter merely overfits a small number of historical crashes.
- NSE performance comes almost entirely from illiquid small caps.
- Post-publication results materially contradict the historical edge.
- Data snapshot cannot be reproduced.

---

## 22. Recommended implementation sequence

### Phase 0 — Data gate

Do not write strategy code against untrusted data.

Fix:

- Survivorship.
- Adjustment drift.
- Security identity.
- Delisting.
- Tradability.
- Reproducibility.

### Phase 1 — Pure indicator library

Implement:

- SMA.
- True range and ATR.
- Regression slope.
- R².
- Momentum score.
- Gap variants.

Add unit tests.

### Phase 2 — Book-faithful portfolio backtest

Implement exact canonical rules. No optimisation.

### Phase 3 — Independent audit

Audit:

- Look-ahead.
- Universe bias.
- Corporate actions.
- Execution timing.
- Cash accounting.
- Ranking.
- Position resizing.
- Delisting.

### Phase 4 — NSE realism

Add:

- All charges.
- Slippage.
- Participation limits.
- Circuit handling.
- Order rejection.
- Cash instrument.
- Sector-risk reports.

### Phase 5 — Robustness

Run the planned experiment matrix and untouched holdout.

### Phase 6 — Paper trade

Minimum several months, preferably through different market conditions.

### Phase 7 — Small capital

Use a strict capital cap and automated reconciliation. Increase only after observed live behaviour matches the paper model.

---

## 23. Master prompt for the implementation agent

```text
You are implementing a systematic equity momentum strategy based on the
attached “Stocks on the Move — Agent-Ready Implementation Specification.”

Treat the specification as a contract.

Primary objective:
Build a reproducible, point-in-time, portfolio-level backtest of the canonical
book strategy and a separate execution-realistic NSE version.

Rules:
1. Do not optimise parameters.
2. Do not add indicators or discretionary logic.
3. Do not use current index constituents historically.
4. Do not silently resolve ambiguities. Implement both documented variants.
5. Signals must use data available at session T and execute no earlier than T+1.
6. Use adjusted prices for analytics and raw prices for fills.
7. Include delisted and merged securities.
8. Model failed fills, zero volume, suspensions and price bands.
9. Model all configured transaction costs.
10. Preserve deterministic reproducibility.

Before coding:
- Audit the available data against the specification.
- Produce a blocker report.
- Refuse to produce headline CAGR when critical data requirements fail.

Required modules:
- security master
- corporate-action adjustment
- point-in-time universe
- indicator library
- cross-sectional ranking
- portfolio/order engine
- execution/cost model
- benchmark engine
- reporting
- tests

Required outputs:
1. Exact configuration file.
2. Data-quality report.
3. Unit and integration tests.
4. Canonical baseline results.
5. Execution-realistic results.
6. Trade ledger.
7. Daily portfolio ledger.
8. Exposure and turnover report.
9. Parameter robustness matrix.
10. Attribution by year, sector and security.
11. List of every assumption and unresolved ambiguity.
12. Reproduction command with frozen data checksum.

Implementation order:
A. Write tests for formulas.
B. Implement canonical baseline.
C. Audit for look-ahead and survivorship.
D. Add realistic execution.
E. Run robustness tests.
F. Produce a deployment recommendation.

Do not claim the strategy is profitable merely because the original book
reported historical profitability. The implementation must independently
validate the strategy on trustworthy NSE data.
```

---

## 24. Final recommendation

This is a strong first system to implement because:

- Rules are explicit.
- Portfolio logic is coherent.
- Parameters are shown to be reasonably stable in the book.
- It avoids unnecessary machine learning.
- It is suitable for daily data.
- It forces correct handling of universe, ranking, risk and regime.

Its largest value is not the exact regression formula. The essential components are:

```text
medium-term cross-sectional momentum
+ broad-market downside filter
+ risk-balanced sizing
+ systematic replacement of weakening stocks
+ trustworthy point-in-time data
```

The main implementation risk is not strategy coding. It is obtaining a survivor-complete, correctly adjusted and realistically tradable historical NSE dataset.

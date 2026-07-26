# Stocks on the Move — canonical baseline

## Summary

Implement Clenow's *Stocks on the Move* as a fourth strategy kind in the lab: a
weekly, long-only, cross-sectional momentum portfolio with ATR-risk-balanced
integer-share sizing and a broad-market regime filter.

Governing documents:

- `~/Documents/Personal_Projects/DOWNLOADS/stocks_on_the_move_agent_implementation_spec.md`
- `~/Documents/Personal_Projects/DOWNLOADS/stocks_on_the_move_canonical_config.yaml`

Both are a **contract**. No optimisation, no added indicators, no parameter
search until the canonical baseline runs and its assumptions are documented.
Where the book is ambiguous the spec names both variants; implement both and
expose the choice as a parameter. Never silently pick one.

Two decisions are already made and are not open for re-litigation:

1. **Universe** — the repo has no point-in-time Nifty 500 membership, only a
   current 501-row snapshot, which the spec forbids using retrospectively.
   Build a **liquidity-proxy universe** from the survivorship-free bhavcopy
   panel instead. This is a documented deviation from the book, not a
   replication of it, and every report must say so.
2. **Engine** — add a new order-level engine kind rather than bending the
   existing weight-based rebalance engine. Integer shares, sells-before-buys
   and full-target-or-skip funding do not survive a weight round-trip.

## Data audit (already performed — carry these findings forward)

| Spec requirement | Status |
|---|---|
| Survivorship-free panel | Present. `data/nse_adjusted/ohlcv_all.parquet`, 4,453 symbols, 2005-01-03 → 2026-07-17, 8.35M rows, columns `date, symbol, open, high, low, close, volume, turnover` |
| Point-in-time index membership | **Absent.** Replaced by the liquidity proxy below |
| ISIN identity | In raw bhavcopy zips (`ISIN` column), not in the panel. Out of scope here; see Follow-ups |
| Nifty 500 TRI | `data/reference/nifty500_tri_2010-01-04_2026-03-17.csv`. Ends 2026-03-17, four months short of the panel |
| Nifty 500 price index | **Absent.** Needed for the regime filter |
| Delisting dates/reasons/terminal values | Absent. Retired tickers simply stop |
| Corporate actions | `data/reference/nse_corporate_actions.parquet`; 538 quarantined rows unreviewed |
| Transaction-cost model | Absent. Only `slippage_bps` and a flat `round_trip_cost_pct` exist |

## Implementation Changes

### 1. Indicator library — `src/sepa_scanner/lab/indicators.py` (new)

Pure functions over Polars frames or NumPy arrays, no I/O, no config:

- `sma(values, window)` — returns null until the window is complete.
- `true_range(high, low, prev_close)` per spec §5.4.
- `atr_simple(ohlc, window=20)` — arithmetic mean of TR. Canonical baseline.
- `atr_wilder(ohlc, window=20)` — Wilder smoothing. Variant only.
- `log_regression_slope_r2(prices, annualisation=250)` — returns
  `(annualised_slope, r_squared)`. OLS on `ln(price)` against `x = 0..N-1`.
  `annualised_slope = exp(beta * 250) - 1`. Raise on any non-positive price;
  return `None` when fewer than `N` valid sessions exist.
- `momentum_score(prices, window=90, annualisation=250)` —
  `annualised_slope * r_squared`.
- `gap_close_to_close(adj_close)` — `abs(c_t / c_{t-1} - 1)`. Variant A.
- `gap_open(raw_open, raw_prev_close)` — `abs(o_t / c_{t-1} - 1)`. Variant B.
- `max_gap(metric, window=90)`.

Vectorise across symbols with Polars `rolling`/`over(symbol)` where possible;
the regression needs a rolling window over 90 log prices for ~4,400 symbols
across ~4,000 sessions, so compute it once per decision date only, not daily.

### 2. Point-in-time universe — `src/sepa_scanner/lab/universe.py` (new)

```python
def liquidity_universe(
    panel: pl.DataFrame,
    size: int = 500,
    lookback_sessions: int = 60,
    min_history_sessions: int = 200,
    reconstitute: Literal["semiannual", "quarterly", "annual"] = "semiannual",
) -> pl.DataFrame:  # -> columns: effective_date, symbol, rank_by_turnover
```

Rules, all strictly backward-looking:

- On each reconstitution date, rank every symbol by **median `turnover` over
  the trailing `lookback_sessions`**, computed from sessions strictly before
  the reconstitution date.
- Require at least `min_history_sessions` prior sessions and positive turnover
  on at least 80% of the lookback window.
- Take the top `size`. Membership holds until the next reconstitution date.
- Semiannual reconstitution on the first session on/after 1 January and
  1 July, matching NSE's own Nifty 500 cadence.
- A symbol that stops appearing in the panel mid-period stays a member until
  reconstitution; the engine's exit rules handle it (see §4, delisting).

Emit a membership table, not a filter function, so it can be asserted against
in tests and audited independently.

### 3. Contracts — `src/sepa_scanner/lab/contracts.py` (modify)

Add:

```python
class StrategyKind(str, Enum):
    ...
    PORTFOLIO = "portfolio"
    """Ranked decision frames executed as integer-share orders against cash."""

DECISION_REQUIRED_COLUMNS = (
    "decision_date", "symbol", "score", "rank", "rank_pct",
    "atr20", "trend_ok", "gap_ok", "is_member",
)
```

`validate_intent` in `runner.py` gains a `PORTFOLIO` branch checking those
columns, plus: `rank` is a dense 1-based integer unique within each
`decision_date`; `rank_pct` lies in `(0, 1]`; `atr20 > 0`.

### 4. Order engine — `src/sepa_scanner/lab/engines/portfolio_order_engine.py` (new)

A direct transcription of spec §17. Signature:

```python
def run_portfolio_order_engine(
    decisions: pl.DataFrame,
    panel: pl.DataFrame,
    config: PortfolioOrderConfig,
) -> dict  # {"daily_equity", "orders", "positions", "rejections"}
```

`PortfolioOrderConfig` (frozen dataclass) carries: `initial_capital`,
`risk_factor`, `market_regime` (frame of `date, allows_new_buys`),
`rebalance_every_n_weeks`, `rebalance_tolerance`, `require_full_target`,
`participation_limit_adv`, `slippage_bps`, `cost_model`,
`pending_order_expiry_sessions`, `cash_yield_pct` (default `0.0`).

Per-session loop, in this exact order:

1. **Process pending orders** against session `T` prices. Fill at raw open
   plus slippage (buys pay up, sells pay down). Reject and re-queue when:
   `volume == 0`; order notional exceeds `participation_limit_adv ×` 20-day
   median turnover; the symbol is absent from the panel that session. Expire
   after `pending_order_expiry_sessions`, recording a rejection reason.
2. **Mark to market** using session close. Accrue `cash_yield_pct` on idle
   cash — default `0.0`, so it is off unless explicitly simulated (spec §14.5).
3. If not a decision session, continue.
4. **Sells first.** Exit the full position when any of: not `is_member`;
   `rank_pct > rank_exit_pct`; not `trend_ok`; not `gap_ok`; symbol absent from
   the current decision frame. No stops, no targets, no time limit.
5. **Resize** on position-rebalance cycles only. `target_shares =
   floor(equity × risk_factor / atr20)`. Skip when
   `abs(target_value - current_value) / target_value < rebalance_tolerance`.
6. **Buys**, only if `allows_new_buys`. Walk the ranking from rank 1, skipping
   held and disqualified names, breaking at `rank_pct > rank_exit_pct`. Size as
   above. Under `require_full_target`, skip any candidate whose full size
   cannot be funded from projected cash; otherwise allow the final position to
   be cut to available cash.
7. All orders submit for `T+1`. **A signal on `T` can never fill on `T`.**

Cash may never go negative. Share counts are always integers. Identical inputs
must produce identical outputs — sort every iteration deterministically by
`(-score, symbol)`.

**Delisting.** When a held symbol disappears from the panel and never returns,
close it at the **last available close marked down 100%** if the disappearance
coincides with a suspension, otherwise at the last available close, and record
the terminal value in `rejections` with reason `delisted_assumed_terminal`.
Report the count and total P&L impact of these separately in run extras — the
spec requires uncertain terminal values be flagged, not buried.

### 5. Cost model — `src/sepa_scanner/lab/costs.py` (new)

`indian_delivery_cost(side, notional) -> float`, summing brokerage (₹0 delivery,
configurable), STT (0.1% both legs), exchange charges (0.00297%), SEBI
(₹10/crore), GST (18% on brokerage + exchange + SEBI), stamp duty (0.015% buy
only). Expose a `stress_multiplier` so the required 1.0×/1.5×/2.0× runs are a
parameter, not three code paths.

### 6. Benchmark — `src/sepa_scanner/lab/panel.py` (modify)

- Add a Nifty 500 **price** index loader. The series is not in the repo; add a
  fetch script `scripts/fetch_nifty500_price_index.py` writing
  `data/reference/nifty500_price_<start>_<end>.csv`.
- Extend the TRI series past 2026-03-17 to the panel's end.
- **If either series is missing or short, the run must suppress all
  benchmark-relative metrics** — reuse the existing discontinuity-suppression
  path in `runner.py` rather than inventing a second mechanism. A broken
  benchmark producing fabricated alpha is the exact bug this repo just fixed.

### 7. Strategy — `src/sepa_scanner/lab_strategies/stocks_on_the_move.py` (new)

`id = "stocks_on_the_move"`, `kind = StrategyKind.PORTFOLIO`,
`data_requirement = DataRequirement.PANEL`.

`ParamSpec` list, defaults taken verbatim from the canonical YAML:

| Param | Default | Group |
|---|---|---|
| `momentum_window` | 90 | Momentum |
| `annualisation_sessions` | 250 | Momentum |
| `stock_sma_sessions` | 100 | Filters |
| `market_sma_sessions` | 200 | Filters |
| `gap_window_sessions` | 90 | Filters |
| `gap_threshold` | 0.15 | Filters |
| `gap_definition` | `close_to_close` (enum: `close_to_close`, `open_gap`) | Filters |
| `rank_exit_pct` | 0.20 | Filters |
| `atr_sessions` | 20 | Risk |
| `atr_smoothing` | `simple` (enum: `simple`, `wilder`) | Risk |
| `risk_factor` | 0.001 | Risk |
| `signal_weekday` | `WED` | Schedule |
| `position_rebalance_every_n_weeks` | 2 | Schedule |
| `rebalance_tolerance` | 0.05 | Schedule |
| `require_full_target` | `True` | Orders |
| `participation_limit_adv` | 0.02 | Execution |
| `slippage_bps` | 0.0 | Execution |
| `cost_stress_multiplier` | 1.0 | Execution |
| `universe_size` | 500 | Universe |
| `universe_reconstitute` | `semiannual` | Universe |

`generate()` builds the decision frame: for each weekly decision session
(next available session when the anchor weekday is a holiday), compute
momentum score, rank and `rank_pct` **over the full point-in-time universe**
— not over the filtered survivors, since the percentile denominator is the
whole universe per spec §6 — then attach `trend_ok`, `gap_ok`, `atr20` and
`is_member`.

`engine_config()` builds `PortfolioOrderConfig`, including the market-regime
frame from the price index vs its SMA200.

Register in `src/sepa_scanner/lab_strategies/__init__.py`.

### 8. Runner and reporting — `src/sepa_scanner/lab/runner.py` (modify)

Add the `PORTFOLIO` branch: `include_trade_stats = True` (round trips are real
trades with entry and exit), but **R-multiples do not apply** — there is no
stop, so leave `avg_r` null rather than computing a meaningless number.

Extend `RunMetrics` with the spec §20 report fields the engine can supply now:
`exposure_pct`, `cash_pct`, `turnover_pct`, `avg_holdings`,
`avg_holding_days`, `cost_drag_pct`, `slippage_drag_pct`. Leave Sharpe,
Sortino, beta, downside capture and rolling-window returns for a later pass;
do not stub them with placeholder values.

### 9. UI — `sepa-terminal/`

`ParamForm` already renders from `ParamSpec`, so the form needs no work.
Required changes:

- `lab-types.ts`: add `"portfolio"` to the kind union and the new metric fields.
- `LabRun.tsx`: for `portfolio`, hide the R-multiple column and show exposure,
  cash, turnover and average holdings instead.
- Surface the universe deviation as a visible banner on any
  `stocks_on_the_move` run: *"Liquidity-proxy universe, not Nifty 500
  membership — not a book replication."*

### 10. Documentation

- `docs/BACKTEST_TRUTH.md`: add `portfolio` to the engine-kinds table; record
  the universe deviation, the missing price index, and the delisting
  assumption as standing caveats.
- `strategies/stocks_on_the_move/README.md`: the config used, every ambiguity
  and which variant is baseline, and the full deviation list.
- Copy both source documents into `docs/specs/` so the contract is versioned
  with the code rather than living in `~/Downloads`.

## Test Plan

Mirror spec §18. New tests under `tests/lab/`.

**Indicators** (`test_indicators.py`) — regression slope recovers a known
`exp(0.0004 * t)` series to 1e-9; R² == 1.0 for a perfect log-linear series and
< 0.1 for seeded noise; annualisation identity
`exp(beta * 250) - 1 == exp(beta) ** 250 - 1`; ATR20 against a hand-computed
synthetic OHLC fixture; SMA null until the window fills; both gap variants on a
known split; log of a non-positive price raises.

**Universe** (`test_universe.py`) — a symbol listing mid-period cannot appear
before it has `min_history_sessions`; membership never uses forward turnover
(assert by truncating the panel and re-deriving the same table); reconstitution
dates land on real sessions; a delisted symbol remains in historical
membership.

**Timing** (`test_portfolio_timing.py`) — a signal on `T` never fills on `T`;
earliest fill is `T+1`; a holiday-shifted anchor resolves to the next session;
a zero-volume bar does not fill and the order carries or expires; no indicator
reads a future bar.

**Portfolio** (`test_portfolio_order_engine.py`) — sells settle before buys
within a decision; the market filter blocks buys but forces no liquidation;
rank exit uses the full universe denominator; every exit is a full exit; cash
never goes negative; share counts are integers; two identical runs produce
byte-identical ledgers; `require_full_target=True` skips an underfunded
candidate while the variant truncates it.

**Data canaries** (`test_som_data_quality.py`) — no duplicate symbol/date rows;
no non-positive adjusted prices; no fills on zero-volume bars in a full run;
OHLC invariants hold; known delisted tickers present in the panel.

**Regression gate — non-negotiable.** `uv run python optimize_joint_wf.py
--parity` must still print `CAGR=12.48% MaxDD=24.28% Calmar=0.51 trades=281`.
Nothing in this plan should touch that path; if it moves, the change is wrong.

Full suite (`uv run pytest`) must stay green — currently 357 passing. Mark the
end-to-end portfolio run `slow`.

## Assumptions

1. Liquidity-proxy universe substitutes for Nifty 500 membership. **Results are
   not a book replication** and must never be reported as one.
2. Regime filter needs the Nifty 500 price index; until it is fetched, the run
   suppresses benchmark-relative metrics rather than substituting TRI.
3. Delisted holdings exit at last available close, flagged separately, since
   the panel has no delisting reasons or terminal values.
4. Adjusted prices drive analytics; the panel's OHLC serves fills. The repo has
   no separate raw execution series, so this is a known imprecision — record it.
5. Idle cash earns zero unless explicitly configured.
6. Canonical baseline runs with `slippage_bps = 0` and
   `cost_stress_multiplier = 1.0`; the realistic NSE run is a separate
   invocation, reported separately.

## Risks

- **Compute.** A 90-session OLS per symbol per decision date across 4,400
  symbols and ~840 weekly decisions is the hot path. Compute only on decision
  dates and vectorise; if a full run exceeds ~10 minutes, cache the momentum
  frame keyed by `(panel_checksum, momentum_window)`.
- **Universe deviation swamping the comparison.** A turnover-ranked top 500 is
  not the Nifty 500; expect a materially different result from the book's
  12.3%. That is the honest outcome, not a bug to tune away.
- **Silent ambiguity resolution.** Every AMBIGUOUS field in the YAML must be a
  parameter with both variants runnable. A hard-coded choice fails the contract.
- **Data-quality contamination.** The spec's §15 blocker list still partly
  applies: 538 unreviewed quarantined adjustment rows, no ISIN identity. A
  cross-sectional ranker is maximally exposed to corporate-action artifacts
  ranking top. Add a canary that reports the top 20 momentum scores per year
  for manual inspection before any headline number is quoted.

## Acceptance Criteria

1. `sepa lab list` shows `stocks_on_the_move | portfolio`.
2. A canonical baseline run completes and emits a trade ledger, a daily
   portfolio ledger, and an exposure/turnover report.
3. Both gap variants and both ATR variants run and are reported side by side.
4. The full test suite passes; SEPA parity is byte-identical.
5. The README lists every assumption and unresolved ambiguity.
6. No headline CAGR is published without the universe deviation stated
   alongside it.

Out of scope for this plan: the §19.2 robustness matrix, walk-forward
partitions, sector caps, and paper trading. Those come after the baseline is
locked, per spec §19.

## Follow-ups (not in this plan)

- ISIN-keyed security identity carried from raw bhavcopy into the panel.
- Reconstruct genuine point-in-time Nifty 500 membership; swap the proxy out.
- Review the 538 quarantined corporate-action rows.
- Real delisting outcomes (cash consideration, merger terms, recovery value).

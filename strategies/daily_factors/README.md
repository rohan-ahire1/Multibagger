# Daily factor strategy comparison

This package tests four close-to-next-open, cross-sectional portfolios against the
canonical SEPA result and Nifty 500 TRI:

- 12–1 momentum, monthly, top 20, equal weight
- 52-week-high proximity, monthly, top 20, equal weight
- Jensen alpha plus low volatility, June/December, top 30, score weight capped at 5%
- 52-week-high plus alpha/low-volatility, monthly, top 30, equal weight

It also runs a monthly equal-weight universe control. The point-in-time universe is
the latest NSE EQ bhavcopy snapshot available on each signal date, reduced to the
top 200 names by trailing 63-session turnover (top 150 for alpha/low-volatility).
This avoids using today's survivors but is only a proxy for historical Nifty 200
membership; exact historical index constituents are not present in the repository.

Run:

```bash
uv run python -m strategies.daily_factors
```

Outputs are written under `strategies/daily_factors/output/` and include the daily
equity curves, every rebalance order, a machine-readable summary, and a short report.

## Migration delta (2026-07-25) — survivorship wipes out most of the edge

This strategy is registered in the strategy lab as `daily_factors`, the first
`REBALANCE`-kind strategy. Feature construction, point-in-time membership, and
target selection still come from `engine.py` here; execution goes through
`lab/engines/rebalance_engine.py`, which wraps this module's own
`simulate_rebalanced_portfolio`. So the engine is unchanged.

**What changed is the price panel, and it changes the conclusion.**

`DEFAULT_OHLCV` in this module is `data/yf_adjusted/ohlcv_all.parquet` — the
legacy yfinance panel. That panel contains **2,831 symbols against the
survivorship-free NSE panel's 4,453, and is a strict subset of it**: 1,622
delisted and merged names are simply absent. Everything in `output/` was
produced on it. The `sepa_canonical` row in `output/summary.json` reads 14.03%,
which is exactly the legacy-panel figure recorded in `docs/BACKTEST_TRUTH.md` —
confirming the whole output set predates the 2026-07-19 data cutover.

Rerun on the survivorship-free panel, full history 2010-01-04 → 2026-03-17:

| factor | recorded CAGR (yf) | lab CAGR (NSE) | delta | lab max DD | lab Calmar |
|---|---|---|---|---|---|
| `momentum_12_1_top20` | 18.79% | **11.28%** | −7.51 | 40.67% | 0.28 |
| `alpha_low_vol_top30` | 16.96% | **10.62%** | −6.34 | 31.04% | 0.34 |
| `high_52w_alpha_low_vol_top30` | 16.30% | **9.55%** | −6.75 | 32.45% | 0.29 |
| `high_52w_top20` | 12.31% | **7.43%** | −4.88 | 33.39% | 0.22 |
| `equal_weight_universe` | 9.34% | **5.81%** | −3.53 | 49.30% | 0.12 |

Every sleeve loses 3.5–7.5 CAGR points. This is larger than SEPA's survivorship
cost (~1.5 points) and the reason is structural: these are cross-sectional
strategies that rank the whole universe and hold the top names for a month. On a
survivor-only panel the losers that would have been ranked in — and then
delisted — are not in the data to be bought. A trend strategy with a stop sells
its way out of those names; a monthly-rebalanced factor portfolio holds them.

Note the panel change is *not* a dividend artifact working against these
numbers: the NSE panel is total-return, so it includes dividends the yf panel
did not. Returns still fell.

The practical read: with the benchmark at roughly 10.6% CAGR over the same
window, `momentum_12_1_top20`'s apparent 7-point edge becomes well under a
point, and the rest underperform. **The recorded numbers in `output/` should not
be quoted.** They are retained as the legacy-panel reference that produced this
comparison.

`load_and_build_features` now takes an optional `ohlcv_path`, defaulting to
`DEFAULT_OHLCV` so `run_research()` still reproduces the recorded output. The lab
passes the configured panel instead. Features and execution must come from the
same panel — an earlier version of the lab strategy built features from yf and
filled from the NSE panel, which silently dropped orders for symbols missing
from the other panel.

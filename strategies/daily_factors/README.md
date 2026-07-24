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

# Episodic Pivot Research

Headline: episodic pivots do not beat the local TRI benchmark after tax and risk adjustment; Index the money.

## Definition
- Large positive gap or large day return.
- Exceptional volume relative to 20-day average.
- Strong close in the event-day range.
- Entry at next trading day open; no same-day fills.

## Selected Config
- `ep_gap10_vol5_hold90`
- gap >= 10.0%, day return >= 12.0%, volume ratio >= 5.0x.
- Max hold 90 days, initial stop 12%, trail 18%.

## Holdout 2023-2026-03-17
- Strategy: post-tax CAGR -14.04%, pre-tax CAGR -6.35%, MDD -38.2%, Sharpe -0.03, trades 264.
- Benchmark: post-tax CAGR 11.05%, pre-tax CAGR 12.01%, MDD -18.6%, Sharpe 0.94.
- Spread: post-tax CAGR -25.08%, MDD -19.6 pct-pts, Sharpe -0.98.

## Train Ranking 2015-2022
- ep_gap10_vol5_hold90: post-tax CAGR -12.42%, MDD -52.5%, Sharpe 0.05, trades 513.
- ep_gap8_vol4_hold60: post-tax CAGR -13.95%, MDD -46.5%, Sharpe -0.04, trades 821.
- ep_liquid_gap5_vol25: post-tax CAGR -20.30%, MDD -54.8%, Sharpe -0.14, trades 948.
- ep_gap6_vol3_hold60: post-tax CAGR -21.07%, MDD -56.6%, Sharpe -0.17, trades 973.
- ep_day12_vol4_hold45: post-tax CAGR -34.37%, MDD -69.8%, Sharpe -0.57, trades 975.

## Verdict
Index the money.

A clean no is still cleaner than an overfit yes.
# Mean Reversion Realism Validation

- Requested end date: `2026-05-29`
- Actual data end date: `2026-05-29`
- Baseline CAGR / max DD: `62.0519` / `-38.2189`
- Realistic CAGR / max DD: `-29.9565` / `-99.6737`
- OOS 2023-latest CAGR: `-27.4689`

## Checks

- PASS `no_negative_cash_realistic`
- FAIL `realistic_cagr_positive`
- FAIL `realistic_drawdown_under_35pct`
- FAIL `oos_cagr_positive`
- PASS `realistic_has_enough_fills`

## Interpretation

Use `validation_summary.csv` as the source of truth. The realistic row uses 0.5% round-trip cost, 10 bps slippage each side, 1% risk, no negative cash, and next-open indicator exits.

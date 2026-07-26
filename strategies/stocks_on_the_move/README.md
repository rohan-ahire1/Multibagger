# Stocks on the Move

This is Clenow's weekly, long-only cross-sectional momentum model implemented
in the strategy lab as `stocks_on_the_move`. It is a research baseline, not a
book replication or a deployment recommendation.

## Canonical configuration

The versioned contract is [the canonical YAML](../../docs/specs/stocks_on_the_move_canonical_config.yaml).
The baseline uses 90-session log-regression × R² momentum, SMA100 stock trend,
SMA200 market trend, ATR20 at a 0.001 risk factor, a top-20% rank cutoff,
Wednesday decisions, biweekly resizing, zero slippage, and a 5% resize tolerance.

All source-marked ambiguities are runnable parameters:

- `gap_definition`: `close_to_close` baseline or `open_gap`.
- `atr_smoothing`: `simple` baseline or `wilder`.
- `require_full_target`: `true` baseline or `false` to permit the final partial position.

The source also calls for comparing zero-tolerance resizing. Set
`rebalance_tolerance=0` for that documented baseline; the canonical YAML default
remains `0.05`.

## Deviations and standing limitations

- Historical Nifty 500 membership is unavailable. The strategy uses a
  strictly backward-looking top-500 median-turnover liquidity proxy, so results
  must say: **Liquidity-proxy universe, not Nifty 500 membership — not a book replication.**
- An ISIN security master and canonical-symbol mode now exist, but the committed
  panel remains ticker-keyed until the four rename-boundary discontinuities are
  reviewed. These results use that committed default.
- The panel's OHLC is used for both adjusted analytics and raw execution. Separate
  raw execution prices are not available.
- Nifty 500 price-index history is not stored locally. The default
  `regime_source=price_index` therefore refuses to run rather than fabricating a
  flat curve. The explicit `nifty500_tri` variant is runnable as a documented
  deviation; TRI drifts above the required price index through reinvested dividends.
- The TRI file currently ends on 2026-03-17. Do not present post-cutoff benchmark
  alpha until it is extended from a reviewed source.
- Delisting dates, reasons, and terminal values are absent. A retired holding is
  closed at the last close, or marked down 100% when its final bar has zero volume;
  the run reports each `delisted_assumed_terminal` case separately.
- Idle cash earns zero by default. The canonical run uses `slippage_bps=0` and
  `cost_stress_multiplier=1.0`; realistic NSE-cost runs are separate invocations.
- The corporate-action quarantine is down to 150 rows, including 148 manual
  cases. Price-band/tradability state remains unavailable. The engine rejects
  zero-volume fills and honours the configured 2% ADV participation limit.

## Post-corporate-action rerun — 2026-07-26

These results use the ticker-keyed rebuilt NSE panel and the explicit Nifty 500
TRI regime substitute. The like-for-like window is 2015-01-01 → 2026-03-17:

| Friction | CAGR | Benchmark | Excess | MaxDD | Calmar |
|---|---:|---:|---:|---:|---:|
| 0 bps | 18.02% | 12.13% | +5.89 | 29.14% | 0.62 |
| 25 bps | 15.47% | 12.13% | +3.33 | 30.60% | 0.51 |
| 50 bps | 11.86% | 12.13% | −0.27 | 37.48% | 0.32 |
| 50 bps + 2× costs | 10.84% | 12.13% | −1.29 | 36.41% | 0.30 |

The prior 18.18% headline was incorrectly labelled as 25 bps in the handoff; it
was a zero-bps result. The CA rebuild barely moved that exact case
(18.18% → 18.02%), but it does not rescue the strategy:

- At 25 bps, the 2015–2020 development slice trails the benchmark by 3.01 points.
- Restricting the universe to the 200 most liquid names trails by 3.56 points
  at 25 bps.
- Extending the start to 2010 leaves only +0.77 points of excess at 25 bps and
  −1.32 points at 50 bps.

The holdout has already been inspected repeatedly. The cost sensitivity and
liquid-universe failure are structural, so the verdict remains:
**do not trade or tune this strategy on the current dataset.**

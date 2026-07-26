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
- An ISIN security master and canonical-symbol mode exist, but the committed
  panel remains ticker-keyed. The clean A/B did not support a re-baseline; the
  earlier apparent improvement was contaminated by double-applied actions and
  stale signals.
- The panel's OHLC is used for both adjusted analytics and raw execution. Separate
  raw execution prices are not available.
- Official Nifty 500 price-index history is stored locally from 2010-01-04
  through 2026-07-24. The default `regime_source=price_index` now runs the
  specified regime; `nifty500_tri` remains an explicit research variant.
- The TRI file currently ends on 2026-03-17. Do not present post-cutoff benchmark
  alpha until it is extended from a reviewed source.
- Delisting dates, reasons, and terminal values are absent. A retired holding is
  closed at the last close, or marked down 100% when its final bar has zero volume;
  the run reports each `delisted_assumed_terminal` case separately.
- Idle cash earns zero by default. The canonical run uses `slippage_bps=0` and
  `cost_stress_multiplier=1.0`; realistic NSE-cost runs are separate invocations.
- The corporate-action quarantine is down to 87 rows: 71 unsupported manual
  cases, 9 reviewed complex actions, and 7 long-hiatus boundaries. Price-band/tradability state remains unavailable. The engine rejects
  zero-volume fills and honours the configured 2% ADV participation limit.

## Post-corporate-action rerun — 2026-07-26

These results use the ticker-keyed rebuilt NSE panel and the required Nifty 500
price-index regime. The like-for-like window is 2015-01-01 → 2026-03-17:

| Friction | CAGR | Benchmark | Excess | MaxDD | Calmar |
|---|---:|---:|---:|---:|---:|
| 0 bps | 17.96% | 12.13% | +5.83 | 28.39% | 0.63 |
| 25 bps | 14.52% | 12.13% | +2.39 | 33.02% | 0.44 |
| 50 bps | 11.99% | 12.13% | −0.14 | 35.32% | 0.34 |
| 50 bps + 2× costs | 10.58% | 12.13% | −1.55 | 36.48% | 0.29 |

The corrected data and regime series do not rescue the strategy:

- At 25 bps, the 2015–2020 development slice trails the benchmark by 4.27 points.
- Restricting the universe to the 200 most liquid names trails by 3.29 points
  at 25 bps.
- Extending the start to 2010 leaves only +0.84 points of excess at 25 bps.

The holdout has already been inspected repeatedly. The cost sensitivity and
liquid-universe failure are structural, so the verdict remains:
**do not trade or tune this strategy on the current dataset.**

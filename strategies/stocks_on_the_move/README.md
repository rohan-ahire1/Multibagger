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
- ISIN identity is absent from the adjusted panel; symbols are the available key.
- The panel's OHLC is used for both adjusted analytics and raw execution. Separate
  raw execution prices are not available.
- Nifty 500 price-index history is not stored locally. The regime filter therefore
  blocks new buys until a reviewed price series is fetched; benchmark-relative
  metrics are suppressed whenever the price series or complete TRI is absent.
- The TRI file currently ends on 2026-03-17. Do not present post-cutoff benchmark
  alpha until it is extended from a reviewed source.
- Delisting dates, reasons, and terminal values are absent. A retired holding is
  closed at the last close, or marked down 100% when its final bar has zero volume;
  the run reports each `delisted_assumed_terminal` case separately.
- Idle cash earns zero by default. The canonical run uses `slippage_bps=0` and
  `cost_stress_multiplier=1.0`; realistic NSE-cost runs are separate invocations.
- Corporate-action quarantine rows and price-band/tradability state remain data
  limitations. The engine rejects zero-volume fills and honours the configured
  2% ADV participation limit, but cannot infer unavailable price-band status.

# Episodic Pivots

Standalone episodic pivot research sandbox.

This does not modify SEPA, mean reversion, or the previous autonomous research
folder. It reads the existing yfinance-adjusted parquet store and writes all
outputs under `strategies/episodic_pivots/output/`.

Run:

```bash
uv run python strategies/episodic_pivots/ep_engine.py
```

Definition used here:

- Event day has a large gap or day return.
- Volume is a large multiple of recent average volume.
- Close is strong in the day range.
- Stock is liquid enough for realistic position sizing.
- Entry is next trading day open, never same-day.

## Migration delta (2026-07-25)

This strategy is now registered in the strategy lab as `episodic_pivots`. Signal
detection still comes from `ep_engine.build_features` / `make_signals` in this
directory — the lab imports them rather than copying, so the two cannot drift.
Everything after signal generation (sizing, exits, friction, metrics) is the
shared engine.

**The numbers in `output/summary.json` are not comparable to lab numbers.** This
engine sized positions as a fixed percentage of equity and exited on a
percentage trail; the shared engine sizes on risk-per-trade against the stop
distance and exits on a trailing EMA. Metrics also differ: the lab computes
CAGR, drawdown, and Calmar from the daily equity curve for every strategy.

The conclusion is unchanged, which is the point of the check:

| source | window | CAGR | max DD |
|---|---|---|---|
| `ep_engine` train | 2010–2017 | −4.52% (−12.42% post-tax) | 52.5% |
| `ep_engine` holdout | 2018+ | −6.35% (−14.04% post-tax) | 38.2% |
| lab (`episodic_pivots`) | 2015–2020 | −14.11% | 62.0% |

The lab figure sits within about a point of `ep_engine`'s own post-tax holdout
result, on a different window. Episodic pivots loses money on this universe
under either engine — the migration reproduces the finding rather than
overturning it. It is registered so the finding stays visible and comparable,
not because it is a candidate for capital.

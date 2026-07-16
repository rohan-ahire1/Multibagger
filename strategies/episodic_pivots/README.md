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

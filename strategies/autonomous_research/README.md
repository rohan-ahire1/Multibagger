# Autonomous Strategy Research

Standalone research sandbox for Indian equity daily-data strategy discovery.

This folder is intentionally isolated from the SEPA and mean-reversion strategy
code. It uses the existing parquet store at `data/yf_adjusted/ohlcv_all.parquet`
through `ParquetOhlcvStore`, and it checks that the Rust core extension is
available, but it runs its own realistic portfolio simulation.

Run:

```bash
uv run python strategies/autonomous_research/research_engine.py --quick
uv run python strategies/autonomous_research/research_engine.py
```

Outputs are written to `strategies/autonomous_research/output/`.

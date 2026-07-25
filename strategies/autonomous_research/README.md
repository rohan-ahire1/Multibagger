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

## Migration delta (2026-07-25)

This directory is a search process, not a strategy. Its selected configuration
`mom_126_hold84_r150` is registered in the strategy lab as `research_momentum`,
so the finding stays runnable and comparable; `build_features` and `signal_frame`
are imported from `research_engine.py` rather than copied.

Holdout window 2023-01-01 → 2026-05-29:

| source | CAGR | max DD | win rate | avg hold |
|---|---|---|---|---|
| recorded holdout (pre-tax) | 18.74% | 23.61% | 37.7% | ~60d |
| recorded holdout (post-tax) | 11.86% | — | — | — |
| lab (`research_momentum`) | **6.93%** | 32.84% | 39.0% | 53d |

The benchmark agrees exactly (9.06% in both), which confirms the window and data
line up, and the win rate is close (39.0% vs 37.7%). The return gap comes from
execution, not entries: the research engine sized every position at a flat 10% of
equity and exited below the 100-day mean, while the lab sizes from the stop
distance and exits on an EMA trail, the nearest rule the shared engine offers.

So the entry signal reproduces; the recorded return does not, and the lab figure
is the one computed under the shared friction and metric definitions. Treat the
recorded numbers as this directory's own harness, not as comparable results.

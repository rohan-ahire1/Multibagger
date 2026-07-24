# Structural SEPA research — 2026-07-17

All comparisons use the canonical Python portfolio simulator, D+1 entries,
intraday hard stops, next-open close-signal exits, statutory friction, 10 bps
slippage per side unless stressed, and zero assumed idle-cash yield.

## Accepted

Close-confirmed stagnation exit after 15 post-entry symbol sessions when the
trade's maximum favorable excursion remained below +0.4R and its close is at or
below entry. Execution is at the next symbol-session open. See
`stagnation_exit_validation.csv` for folds and stress comparisons.

Only seven of 287 full-history fills use the exit. Six would later have become
full-stop losses, one later finished at +0.67R, and none became a 3R winner.

## Rejected

- Breakout gap gates and limit/retest entries.
- Failed-breakout exits at 3–15 sessions.
- Chandelier/ATR trails and EMA-to-Chandelier hybrids.
- Initial-stop floors and wider stops, including momentum-selective widening.
- Longer maximum holds and tail-aware pyramiding.
- Regime-adaptive EMA10 exits: standard-window gains failed cost/collision stress.
- Hard or sized earnings-growth and filing-recency gates.
- Momentum-acceleration gates, priority, and sizing.
- Relaxed range, volume-dry-up, and setup-streak entry gates.
- Larger fixed or adaptive low-position-count concentration.

The main remaining limitation is signal supply: the strategy fills about 18
trades per year and averages roughly 40% deployment. Attempts to increase
deployment by lowering quality or increasing concentration worsened robustness.

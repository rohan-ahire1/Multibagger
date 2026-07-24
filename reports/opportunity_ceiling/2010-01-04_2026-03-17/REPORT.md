# Opportunity-Ceiling Diagnosis

**Window:** 2010-01-04 to 2026-03-17

This is a diagnostic, not a strategy backtest. Forward maxima and oracle exits use
hindsight only to locate the bottleneck; they must never be treated as tradable results.

## Result

Primary bottleneck: entry signal supply. Raw winner-episode recall=14.8%; major-signal filter recall=31.4%; actual/oracle P&L capture=23.3%.

The breakout generator misses most major eligible-universe runs; exit tuning cannot monetize opportunities that never become entries.

## Signal funnel

- Raw breakouts: 1860
- D+1-fillable raw breakouts: 1850
- Canonical-selected breakouts: 354
- Portfolio entries: 281
- Major signal opportunities (>=50% 120-session MFE): 159
- Filter recall of major signal opportunities: 31.4%
- Final portfolio capture of major signal opportunities: 24.5%

## Universe winner coverage

- Non-overlapping eligible +50% winner episodes: 3767
- Any raw breakout within 120 sessions: 559 (14.8%)
- Canonical breakout within 120 sessions: 192 (5.1%)
- Winner episodes missed by the breakout generator entirely: 3208

## Same-entry hindsight exit bound

- Actual trade P&L: INR 5,636,310
- Best pre-original-stop 120-session P&L, fixed historical sizes: INR 24,214,391
- Actual/oracle P&L capture: 23.3%
- Non-compounded oracle-equivalent CAGR: 22.0%

## Decision

- Primary bottleneck: **entry signal supply**
- Can exit work alone credibly justify a 30% target? **No; even the fixed-size hindsight bound remains below 30% CAGR.**
- Recommended next experiment: the pre-registered pullback-continuation stream, behind a disabled config flag, because it directly targets missing entry supply

## Caveats

- Major winners are ex-post labels, sampled from the first eligible date per symbol-month.
- Overlapping 120-session winner windows for the same symbol are suppressed.
- Oracle exits know the future, hold historical position sizes fixed, and ignore the
  portfolio schedule that different exit dates would create.
- Filter recall is descriptive, not permission to loosen filters; rejected winners must
  be weighed against the additional losers each filter removes.
- Therefore the oracle number diagnoses latent opportunity; it is not an expected return.

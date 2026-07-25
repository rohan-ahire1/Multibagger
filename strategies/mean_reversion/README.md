# Mean reversion (Connors RSI-2)

Buys short-term oversold names that are still above their long-term moving
average and sells when the reversion completes — a close back above the 5-day
mean, or RSI-2 back above 70. Short holds, high win rate, small edge per trade.

Layout: `signal_engine.py` (long, short, and v2 scans), `exit_engine.py` (the
reversion exit stack), `simulation.py` (its own executor, long-only and
long/short), `indicators.py`, `universe.py`, `validation.py`, `live/` (paper
runner), `tests/`.

## Migration delta (2026-07-25) — needed a third engine kind

Registered in the strategy lab as `mean_reversion`, and it is the reason the lab
has a third engine kind (`StrategyKind.REVERSION`).

**Why it could not use the shared engine.** The shared portfolio simulator exits
on a trailing EMA, a hard stop, or a holding limit. This strategy exits when the
*reversion completes* — a close back above the 5-day mean, or RSI-2 back above 70
(`exit_engine.evaluate_mr_exit`). Routing it through the shared engine replaces
the rule that defines it. Measured that way it produced 46-day average holds and
a ~32% win rate — a trend strategy wearing mean reversion's entries — and about
-12% CAGR, which says nothing about mean reversion.

The alternative was to teach the canonical simulator a reversion exit. That
simulator is what `optimize_joint_wf.py --parity` depends on, so the plan's
documented fallback applied: keep this strategy on its own executor behind the
same contract. `lab/engines/reversion_engine.py` wraps `simulate_mr_portfolio`
from this directory. Metrics still come from the shared equity-curve summariser,
which is where comparability actually lives.

On the reversion exit the behaviour is right again — roughly 6-day holds and a
~60% win rate, the signature of the strategy.

**The verdict is unchanged, and it is not good.** `reports/mean_reversion/
validation/*/validation_verdict.md` already records FAIL on
`realistic_cagr_positive`, `realistic_drawdown_under_35pct`, and
`oos_cagr_positive`. Full history 2010-01-04 → 2026-05-29, 1% risk, next-open
exits, 0.5% round-trip cost:

| source | CAGR | max DD | win rate | avg R |
|---|---|---|---|---|
| recorded `baseline` (0.2% cost, same-close exits, 5% risk) | +62.05% | 38.2% | 66.0% | +0.062 |
| recorded `realistic` (0.5% cost, next-open exits, 1% risk) | −29.96% | 99.67% | 56.3% | −0.0037 |
| lab (`mean_reversion`, realistic settings) | **−16.44%** | **94.74%** | 58.3% | −0.022 |

The lab lands between the two and firmly on the realistic side, agreeing with the
existing verdict: the +62% baseline is an artifact of same-close exits on a
signal that is only known at the close, plus understated costs. `round_trip_cost_pct`
is exposed as a parameter and **defaults to 0.005**, the setting that validation
treats as truthful; setting it to 0 reproduces the flattering baseline.

**Short leg not migrated.** `scan_short_mean_reversion` and the long/short
`simulate_mr_long_short_portfolio` stay here and are not reachable from the lab.
Adding a short side to the shared engine is not worth doing for a long leg that
does not yet clear honest costs.

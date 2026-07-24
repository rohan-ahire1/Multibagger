# Second-Entry Experiment: Pullback-Continuation on Confirmed Leaders

## Summary

The SEPA strategy trades only `BREAKOUT` signals and sits ~60% in cash (avg exposure ~40%,
median 2 open positions; the 20-slot cap never binds). Exposure is capacity-constrained by
**signal supply**, not by universe size or the risk/RS knobs — and supply is throttled by the
VCP tightness gate (`range10 ≤ 0.08`), which walk-forward testing has repeatedly refused to
loosen. The only honest way to raise exposure is to add a **second, uncorrelated entry** that
fires when new bases are *not* completing.

This plan scopes and validates one candidate: a **pullback-continuation entry** — buy an
already-confirmed Stage-2 / high-RS leader when it pulls back to a rising moving average and
shows a reversal trigger, rather than only at a base breakout. Rationale:
- Reuses the existing quality bar (trend template + RS), so it does not lower entry quality.
- Fires in consolidation/quiet regimes when few new bases break out → **timing-uncorrelated**
  with breakout fills → fills idle capacity instead of cannibalizing it.
- Uses the same risk framework, exit engine, and portfolio caps — additive, not a new system.

**Alternatives considered and not chosen:** 12-month momentum entry (higher beta, correlated
with breakouts), WATCH-signal promotion (lower quality by construction), episodic-pivot /
earnings-gap (already tested and rejected — negative train+holdout), failed-breakout re-entry
(already rejected — raised drawdown to 21–26%).

**This is a research experiment with real overfit risk. It ships only if it beats
breakout-only on the folds AND the 2023+ holdout under honest execution — not on full history
alone.** Default outcome is rejection-with-documentation unless the acceptance bar is cleared.

## Hypothesis (falsifiable)

Adding pullback-continuation entries to the committed breakout strategy raises strategy-only
CAGR and average exposure, with a **fill set materially uncorrelated** to breakout fills, and
**does not worsen MaxDD/Calmar** on any of the three regime folds or the 2023+ holdout, using
the canonical harness with honest execution and full friction. If any of these fail, or the
result depends on a knife-edge parameter, reject.

## Entry definition (causal; all inputs from data on or before signal date T)

A `PULLBACK` signal fires on date T for a symbol in the eligible universe when **all** hold:

1. **Confirmed leader:** trend-template pass (`tt_pass`, same as breakout) AND `rs_rank ≥
   rs_pullback_min` (default 70, matching `backtest.rs_rank_min`).
2. **Established prior up-leg:** close has been above the 50-SMA for ≥ `leader_min_days`
   (default 20) and the 50-SMA is rising — excludes falling knives / fresh names.
3. **Pullback to rising support:** the day's low touched or came within `ma_touch_pct`
   (default 2%) of a **rising** anchor MA (default 21-EMA; 50-SMA is a swept alternative),
   and the pullback low is above the prior swing structure (no lower-low breakdown).
4. **Volume dry-up on the pullback:** `voldry`-style contraction over the pullback window
   (reuse `volume`/`tightness` indicators) ≤ `pullback_voldry_max` (default 0.9, matching the
   breakout quality filter) — supply drying up, not distribution.
5. **Reversal trigger on T:** T is an up close in the **upper third** of its range that
   reclaims/holds the anchor MA (a "kiss" of the MA), OR completes a 2–3-bar higher-low.
6. **De-dup vs breakout:** no `BREAKOUT` signal for the same symbol within `dedup_days`
   (default 10) before or after T — prevents entering the same leg twice and keeps the two
   streams uncorrelated by construction.

**Execution (identical to breakout):** entry on T+1 open with the same pending window; stop
at the pullback swing low or the structural/7%-capped stop, whichever is tighter; 2%
per-trade risk sizing; the committed exit engine (`baseline_ema_hybrid`, 5R tighten,
stagnation exit); `stop_trigger_mode: intraday_hard`, `signal_exit_execution: next_open`,
`slippage_bps: 10`. **No filled trade enters on its EOD signal date.**

## Implementation Changes

Follow the "no new backtest scripts / no param sidecars" rule — everything routes through the
existing signal engine, `config.yaml`, and the canonical harness.

1. **`src/sepa_scanner/strategy/pullback_setup.py` (new).** A detector that consumes the
   already-prepared indicator frame from `signal_engine._prepare_frame` (which computes SMA
   50/150/200, 52w high/low, ATR, tightness, contraction, volume columns, and adds a 21-EMA)
   and returns a boolean `pullback_pass` plus `pullback_pivot`/`pullback_stop`, using rules
   1–6 above. Reuse `sma`, `highs_lows`, `tightness`, `volume`, `rs` indicators; add a rising
   21-EMA column if not already present.
2. **`signal_engine.py`.** After the existing BREAKOUT/WATCH emission, emit `PULLBACK`
   signals via `pl.lit("PULLBACK").alias("signal_type")` with the same schema (entry_ref,
   stop, pivot, rs_rank, range10, voldry, etc.), applying the rule-6 de-dup against same-day
   and windowed BREAKOUT rows. Persist through the existing `upsert_signals` path.
3. **`config.yaml` (new `pullback` block).** `enabled: false` (default OFF), `rs_pullback_min`,
   `leader_min_days`, `anchor_ma` (`ema21`|`sma50`), `ma_touch_pct`, `pullback_voldry_max`,
   `reversal_upper_range_frac`, `dedup_days`. Mirror in `settings.py` as a `PullbackSettings`
   model. When `enabled: false`, no PULLBACK signals are emitted — committed behavior is byte
   -for-byte unchanged.
4. **Harness inclusion.** Extend `filter_signals` (both `scripts/portfolio_diagnostics.py`
   and `src/sepa_scanner/backtest/simulation.py`) and the `--parity` signal load to accept
   `signal_type ∈ {BREAKOUT, PULLBACK}` when `pullback.enabled`, applying the same
   `max_signals_per_day` and quality filters across the merged stream. Portfolio ranking when
   breakout and pullback compete for the same slot: by `rs_rank` desc, breakout winning ties
   (preserves the primary strategy's priority).
5. **No engine changes** — pullback trades simulate through the existing entry/exit path.

## Test Plan (the crux — anti-overfit protocol)

All runs use the canonical harness (`optimize_joint_wf.py` / `portfolio_diagnostics.py`) on
the **survivorship-free NSE panel** (`data.source: nse_bhavcopy`), honest execution, full
friction, `cash_yield_apy: 0` (strategy-only).

1. **Baseline lock:** confirm breakout-only reproduces 12.48% / 24.28% / 0.51 / 281.
2. **Signal regeneration:** re-run `scripts/backfill_scans.py` with `pullback.enabled: true`
   to populate PULLBACK signals over 2010-01-04 → latest.
3. **Correlation / additivity check (gate 0):** measure overlap between PULLBACK and BREAKOUT
   fills by (symbol, month). If >~40% of pullback fills coincide with a breakout fill in the
   same name/month, the stream is redundant → reject before performance testing.
4. **Exposure check:** average exposure and open-position distribution, breakout-only vs
   combined. The thesis requires exposure to rise from ~40%.
5. **Performance across all windows:** report CAGR / MaxDD / Calmar for breakout-only vs
   combined on: full history, the 3 overlapping regime folds, and the 2023+ slice.
6. **Neighborhood stability:** small pre-registered grid — `ma_touch_pct ∈ {0.015,0.02,0.03}`,
   `anchor_ma ∈ {ema21, sma50}`, `leader_min_days ∈ {15,20,30}`, `dedup_days ∈ {5,10,15}`.
   Accept only if a *contiguous neighborhood* (not a single cell) clears the bar.
7. **Execution stress:** re-check at 25 bps and 50 bps slippage; combined must remain ≥
   breakout-only.
8. Add a regression test asserting `pullback.enabled: false` leaves the committed signal set
   and parity numbers unchanged.

### Acceptance criteria (all required)

- Combined **CAGR > breakout-only** on full history AND ≥ breakout-only on **each** of the 3
  folds and the 2023+ holdout.
- Combined **MaxDD ≤ breakout-only + 1.0 pt** and **Calmar ≥ breakout-only** on every window.
- Pullback/breakout fill correlation below the gate-0 threshold; exposure demonstrably rises.
- Result holds across a contiguous parameter neighborhood and both slippage stresses.

### Kill criteria (any → reject and document, like episodic pivots)

Redundant fills (gate 0), any fold/holdout regression, knife-edge parameter dependence, or
MaxDD/Calmar degradation. On rejection, record the outcome in `BACKTEST_TRUTH.md`'s
rejected-candidates section so it is not re-litigated.

## Assumptions

- The committed exit engine is appropriate for pullback entries (they are the same leaders on
  the same timeframe); no exit retuning in this experiment — that would be a separate, later
  question and a fresh overfit surface.
- Pullback entries share the same 2% risk budget and portfolio caps; no new capital or sizing
  regime is introduced.
- Validation uses the NSE panel now that it is the default; the pre-cutover yfinance archive
  is available if a cross-check is wanted.

## Risks & acceptance summary

- **Overfit / data-snooping:** mitigated by the pre-registered small grid, fold+holdout+stress
  protocol, and neighborhood-stability requirement. The strategy's history is littered with
  single-cell winners that failed the folds (the "27% candidate", failed-breakout re-entry);
  this protocol exists to catch exactly that.
- **Cannibalization:** gate-0 correlation check ensures the second stream is additive, not a
  re-timing of trades already taken.
- **Drawdown creep:** pullback entries buy into weakness by design; the MaxDD acceptance bound
  and slippage stress guard against trading return for hidden risk.
- **Definition of done:** a validation report (breakout-only vs combined across all windows +
  correlation + exposure) and a go/no-go decision recorded in `BACKTEST_TRUTH.md`. Config stays
  `pullback.enabled: false` unless the bar is cleared.

## Suggested sequencing for implementation

Detector + signal emission (behind `enabled: false`) → regression test for unchanged committed
behavior → generate signals → gate-0 correlation/exposure → performance across folds/holdout →
neighborhood + slippage stress → decision + `BACKTEST_TRUTH.md` update.

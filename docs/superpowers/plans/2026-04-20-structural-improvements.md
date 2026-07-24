# Structural Improvements — Comprehensive Evaluation

## Context

Strategy is locked at:
- 27% CAGR, -12.5% MaxDD, Calmar 2.17 (2010–2026)
- 393 total fills, ~25/yr, 63% WR, avg_r +0.85R, payoff 3.4:1
- Train EV 0.74R → Test EV 0.82R (no overfitting)
- Config: `config/config.yaml`, simulation: `scripts/portfolio_diagnostics.py`

Current locked config (baseline for all comparisons):
```yaml
backtest:
  pending_days: 5
  max_hold_days: 60
  exit_mode: baseline_ema21
  rs_rank_min: 70
  skip_months: [1]
  signal_quality_range10_max: 0.08
  signal_quality_voldry_max: 0.9
  signal_quality_setup_streak_max: 0

portfolio:
  max_positions: 20
  max_sector_positions: 4
  initial_capital: 1000000
  risk_per_trade_pct: 0.020
  regime_gate_enabled: true
  max_heat_pct: 1.0
  max_sector_pct: 1.0

exit_strategy:
  max_loss_pct: 0.07
  absolute_max_loss_pct: 0.07
  breakeven_activation_r: 0.75
  partial_profit_r: 1.2
  time_stop_days: 5
  trail_pct_bull: 0.30
  trail_pct_bear: 0.15
  trail_widen_r: 3.0
```

## Goal

Evaluate three structural improvements that have economic justification independent of historical parameter fitting. For each one, run a proper comparison: baseline vs improvement, with full metrics including year-by-year CAGR.

The evaluation function is `run_full_backtest()` in `scripts/portfolio_diagnostics.py`. It calls `simulate_portfolio_python()` and returns a dict with `cagr`, `max_drawdown`, `calmar`, `win_rate`, `avg_r`, `payoff_ratio`, `fills`, etc.

Primary metric: **Calmar ratio** (CAGR / MaxDD). Secondary: CAGR, WR, avg_r. Also report year-by-year CAGR.

---

## Improvement 1: Signal-Quality-Based Position Sizing

### Rationale
Currently all signals get identical sizing (risk_per_trade_pct=0.020). High-conviction signals (tight voldry, fresh base, high RS) should get larger size. This is structurally sound — it's the same logic as "size up on your best ideas" that every discretionary SEPA trader uses.

### Implementation

The simulation already has `signal_size_mult` per-signal and `add_signal_size_multiplier()` in `src/sepa_scanner/strategy/ranking.py`. The multiplier is already plumbed through `PendingSignal.size_mult` and applied at entry in `simulate_portfolio_python()` (lines ~768-770 and ~853-855).

The `signal_quality_size_tilt_mode` setting in `BacktestSettings` already exists. Valid values are `"off"` and `"offense_v2"`. The `add_signal_size_multiplier()` function in `ranking.py` implements the score-based tilt.

**What to add to `config/config.yaml`:**
```yaml
backtest:
  signal_quality_size_tilt_mode: offense_v2
  signal_quality_size_tilt_score_min: 5.5    # scores below → 0.75x size
  signal_quality_size_tilt_score_max: 8.0    # scores above → 1.25x size
  signal_quality_size_tilt_min_mult: 0.75
  signal_quality_size_tilt_max_mult: 1.25
```

**Sweep plan:**
Run 4 variants and compare vs baseline:
1. Baseline (size_tilt_mode=off, current config)
2. Mild tilt: min_mult=0.75, max_mult=1.25, score_min=5.5, score_max=8.0
3. Medium tilt: min_mult=0.60, max_mult=1.40, score_min=5.5, score_max=8.0
4. Aggressive tilt: min_mult=0.50, max_mult=1.50, score_min=5.0, score_max=9.0

For each variant, pass the correct params into the `filter_signals()` and `add_signal_size_multiplier()` calls. Look at where `signal_quality_size_tilt_mode` is already read in `portfolio_diagnostics.py` (search for `size_tilt`) — the wiring may already exist. If so, just sweep the config values.

Report for each variant: fills, CAGR, MaxDD, Calmar, WR, avg_r, + year-by-year CAGR.

---

## Improvement 2: Sector Heat Cap (Correlated Drawdown Protection)

### Rationale
Currently `max_sector_pct=1.0` (no cap). If 4 pharma positions all get hit simultaneously (correlated move), the portfolio takes 4x the expected loss. Capping sector *capital exposure* at 20-25% of equity is structurally sound risk management — it reduces tail risk without filtering any signals.

The simulation already has `max_sector_pct` wiring in `simulate_portfolio_python()` (lines ~784-790 and ~909-913). It compares `marked_sector_values[sector] / current_equity` against `max_sector_pct`. So this is already implemented — we just need to change the config value and verify the sector map is being passed correctly.

**Check first:** In `portfolio_diagnostics.py`, find where `analysis_sector_map` is built and passed. Verify it's populated (not all "UNKNOWN"). If all UNKNOWN, the cap has no effect — flag this in results.

**Sweep plan:**
1. Baseline (max_sector_pct=1.0)
2. max_sector_pct=0.30 (cap sector exposure at 30% of equity)
3. max_sector_pct=0.25
4. max_sector_pct=0.20

Report for each: fills, CAGR, MaxDD, Calmar, WR, + sector fill distribution (how often cap blocked an entry — add a counter if needed).

---

## Improvement 3: Regime-Aware Position Sizing

### Rationale
Currently `regime_gate_enabled=true` acts as a binary gate (stop all entries in weak market). A softer approach: size down (0.5x) in choppy markets rather than blocking entirely, so we still participate in strong individual setups that break out even in weak markets.

The simulation already has `entry_size_mult: dict[date, float]` built by `build_entry_controls()` in `portfolio_diagnostics.py`. In strong regimes it returns 1.0, in weak it likely returns 0.0 (full gate). The `portfolio.breadth_soft_floor_pct` and `portfolio.breadth_soft_size_mult` settings already exist in `PortfolioSettings`.

**What to test:**
1. Baseline: regime_gate_enabled=true (current binary gate)
2. Soft gate: regime_gate_enabled=true, breadth_soft_floor_pct=0.30, breadth_soft_size_mult=0.50 — means: below strong threshold but above 30% breadth → 0.5x size instead of 0.0x
3. Pure size-down: regime_gate_enabled=false, breadth_soft_floor_pct=0.20, breadth_soft_size_mult=0.40 — never fully block, always allow at least 0.4x

**Check:** Look at `build_entry_controls()` in `portfolio_diagnostics.py` to understand exactly what `breadth_soft_floor_pct` controls. If already implemented, just change config values. If not, implement: when market breadth is between `breadth_soft_floor_pct` and `breadth_strong_pct`, set size_mult to `breadth_soft_size_mult` instead of 0.0.

Report for each: fills, CAGR, MaxDD, Calmar, WR, year-by-year.

---

## Output Format

For each improvement, print a clear comparison table:

```
=== IMPROVEMENT 1: SIGNAL QUALITY SIZE TILT ===
Variant              Fills  CAGR    MaxDD   Calmar  WR    AvgR
baseline             393    27.0%  -12.5%   2.17  63%   0.85R
mild_tilt            ???    ???%   ???%     ???   ???%  ???R
medium_tilt          ???    ???%   ???%     ???   ???%  ???R
aggressive_tilt      ???    ???%   ???%     ???   ???%  ???R

Year-by-year for best variant vs baseline:
Year  Baseline  Best_Variant
2010  ...       ...
...
```

Then a final recommendation section:
- Which improvements pass the "worth keeping" test (Calmar improvement ≥ 0.1 with no WR regression)?
- Which should be rejected?
- Recommended config changes if any pass.

---

## Implementation Notes

- Do NOT change the baseline config during this evaluation. Run each variant as a separate in-memory override passed to `simulate_portfolio_python()` or via a temp config dict. Only update `config/config.yaml` at the end if a variant clearly wins.
- The simulation is in `scripts/portfolio_diagnostics.py`. The `run_full_backtest()` function (or equivalent) accepts `overrides` dict — use that pattern for sweeping.
- Keep the date range 2010-01-04 to 2026-03-17 consistent across all runs.
- If a feature requires code changes (e.g., soft regime gate not implemented), implement minimally — don't refactor surrounding code.
- Print intermediate results as you go so progress is visible.
- Final commit: only if at least one improvement clears the bar. Commit message should describe what was accepted and what was rejected.

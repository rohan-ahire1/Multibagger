# BACKTEST TRUTH — single source of truth

> **Read this before running, tuning, or "improving" any backtest.**
> Numbers produced by anything other than the canonical harness below are **invalid**
> and must not be used to justify config changes. This file exists because multiple
> independent backtest engines/metrics historically disagreed, and tuning done in one
> tool was lost or contradicted by another. Do not add new backtest scripts.

Last verified: **2026-07-19** (data cutover: survivorship-free NSE bhavcopy panel is now the
default price source, `config.data.source: nse_bhavcopy`)

> **DATA SOURCE CHANGE (2026-07-19).** Production now reads the self-built,
> survivorship-free, corporate-action-adjusted NSE bhavcopy panel
> (`data/nse_adjusted/ohlcv_all.parquet`, total-return basis) instead of the legacy
> yfinance panel. The yfinance panel omitted ~16.8% of securities that ever traded
> (delisted/merged names) — see `reports/data_audit/`. All canonical metrics below were
> re-measured on the new panel. The pre-cutover yfinance DB is archived at
> `data/db/sepa_PRE_CUTOVER_yfinance_20260719.duckdb`. **Known gap:** `sepa update` still
> feeds the legacy yfinance panel; a daily-bhavcopy refresh job is not yet wired, so the
> NSE panel must be refreshed manually via `scripts/nse_bhav_backfill.py` for now.

---

## The committed config IS the primary config — everywhere

The single source of truth for parameters is **`config/config.yaml`**. The terminal,
paper runner, UI, and the canonical harness all read it. Do not fork parameters into
JSON sidecars, notebooks, or per-script constants.

Key knobs of the committed config (see `config/config.yaml` for the full set):

| Area | Knob | Value |
|---|---|---|
| Exit | `exit_mode` | `baseline_ema_hybrid` |
| Exit | `baseline_tighten_r` | `5.0` (EMA21 after 3R, EMA10 after 5R peak close) |
| Exit | `stagnation_exit_bars` | `15` symbol sessions |
| Exit | `stagnation_max_mfe_r` / `stagnation_max_open_r` | `0.40R` / `0.00R` |
| Execution | `stop_trigger_mode` | `intraday_hard` (original/breakeven hard stops use session low) |
| Execution | `signal_exit_execution` | `next_open` (close-confirmed EMA exits trade next session) |
| Friction | `slippage_bps` | `10` each side, plus statutory charges and DP fee |
| Exit | `use_extended_exit_model` | `false` (phased `ExitConfig` is NOT active) |
| Entry | `backtest.rs_rank_min` / `rs_rank_max` | `70` / `100` |
| Entry | `pending_days` | `8` D+1 onward symbol trading bars |
| Entry | `max_signals_per_day` | `20` |
| Entry | `signal_ranking_mode` | `default` |
| Entry | `setup_vcp.contraction_max_count` | `2` (reject 3+ on breakout day) |
| Sizing | `risk_per_trade_pct` | `0.020` |
| Sizing | `max_positions` | `20` |
| Sizing | `max_name_pct` | `0.20` |
| Sizing | `max_sector_positions` | `4` |
| Cash | `cash_yield_apy` | `0.0` (assumed idle-cash income is excluded from strategy CAGR) |

---

## Canonical metrics (honest, equity-curve based)

Window: signal dates **2010-01-04 → 2026-03-17** (later OHLCV is loaded so open
trades can be managed). Metric source: the real `daily_equity`
curve from `simulate_portfolio_python` (friction on: slippage + STT/charges + DP fee).
**Not** the serial-R-compound number the CLI prints (see "Known traps").

| Config | CAGR | Max DD | Calmar | Trades |
|---|---|---|---|---|
| **Committed strategy-only** (`cash_yield_apy: 0`) — NSE panel | **12.48%** | **24.28%** | **0.51** | 281 |
| Total portfolio: + 6% idle-cash yield sleeve | 17.20% | 15.08% | 1.14 | 278 |
| Legacy yfinance panel (pre-cutover, survivor-biased) | 14.03% | 15.01% | 0.93 | 287 |

**Survivorship is the whole difference between the last two rows.** Like-for-like (same
committed config), moving from the survivor-biased yfinance panel to the survivorship-free
NSE panel costs only ~1.5 CAGR pts (14.03→12.48) but **nearly doubles max drawdown
(15.0→24.3) and halves Calmar (0.93→0.51)**. The trend-exit sells losers before they fully
collapse, so return is barely dented — but the delisted blow-ups (DHFL, SINTEX, ALBK…) still
hit hard on the ride, and their absence had been *hiding the true drawdown*. This refines the
old "survivorship not a blocker" conclusion below: it does not inflate CAGR, it hides risk.

The +6% idle-cash row is the **total-portfolio** outcome (exposure averages ~40%, so ~60% of
capital is idle). It both recovers CAGR and pulls drawdown back to 15% because idle cash
buffers the equity blow-ups. Per policy the committed *strategy* headline still excludes it
(`cash_yield_apy: 0`); deploy idle cash in a liquid instrument and track it as a separate
sleeve. ~+0.8 CAGR pts per +1% APY.

Verified 2026-07-16 with OHLCV through 2026-07-09. The simulator accepts entries
only from D+1 and counts `pending_days` as symbol trading bars. **No filled trade enters
on its EOD signal date.** Small CAGR drift is expected as the data window extends.

The 6% reference is arithmetic on uninvested cash, not SEPA alpha, and is excluded from
the committed headline. If idle cash is actually deployed into a liquid instrument, track
that return as a separate sleeve rather than adding it to strategy CAGR.

The former 19.41% / 14.85% / 1.31 headline used two optimistic execution semantics:
the 7% "hard" stop ignored an intraday low unless the close also failed, and a close-confirmed
EMA exit filled at that already-observed close. It is retained only as a legacy comparison.
Under the same 7R exit with honest execution, strategy-only performance is
13.61% / 15.76% / 0.86; revalidating the exit neighborhood selected the 5R plateau center.

The prior parity report (13.30% / 18.34% / 0.72) was invalid for the configured strategy:
`portfolio_diagnostics.py` silently routed `baseline_ema21` through the phased exit engine.
The route is now covered by a regression test. The repaired, like-for-like EMA21 baseline
before the accepted entry/exit changes was 16.36% / 17.13% / 0.96 with cash yield.

### How to reproduce (the ONLY authoritative command)

```bash
uv run python optimize_joint_wf.py --parity
# expect (NSE panel, signals through 2026-03-17): CAGR=12.48%  MaxDD=24.28%  Calmar=0.51  trades=281
# total portfolio (6% idle-cash sleeve):          CAGR=17.20%  MaxDD=15.08%  Calmar=1.14  trades=278
# legacy yfinance panel for comparison:           CAGR=14.03%  MaxDD=15.01%  Calmar=0.93  trades=287
```

This runs the full production path (quality filters + sector map + exits + friction).
If this number drifts materially, something broke — investigate before trusting any
other result.

---

## Rejected candidates / tested-and-failed changes (do NOT re-wire these)

### Current validation pass (2026-07-17)

All figures in this subsection use strategy-only cash yield, the honest canonical route,
three overlapping regime folds, and the 2023+ validation slice. That slice has now been
inspected repeatedly and is no longer described as a pristine holdout.

- **Accepted entry remains: reject breakout-day `contraction_count >= 3`.** Under honest
  execution, max count 2 produces 13.61% CAGR / 15.76% DD / 0.86 Calmar with fold
  Calmars 0.25 / 0.70 / 1.60. Allowing 3 produces 11.64% / 16.84% / 0.69 with
  folds 0.23 / 0.53 / 1.08; allowing all is 12.31% / 17.48% / 0.70. A cap of 1
  becomes too selective at 9.69% / 13.33% / 0.73. Keep max count 2.
- **Accepted exit: EMA21 → EMA10 at 5R peak close.** Plain EMA21 produces
  12.38% / 17.30% / 0.72; the 5R hybrid produces 13.96% / 15.34% / 0.91.
  The 4.5R and 5.5R neighbors also improve on EMA21, and 5R has the best balanced
  fold score across the local plateau. These figures precede the stagnation exit below.
- **Accepted stagnation exit: 15 symbol sessions, peak below +0.4R, close at/below
  entry, execute next open.** Full history improves from 13.96% / 15.34% / 0.91 to
  **14.03% / 15.01% / 0.93**. Fold Calmars improve 0.302→0.308, 0.802→0.840,
  and 1.406→1.724; 2023+ improves 19.13% / 14.53% / 1.32 to
  **21.37% / 13.90% / 1.54**. Only 7 of 287 full-history trades exit this way:
  six later became full-stop losses, one later reached +0.67R, and none became a ≥3R winner.
  Adjacent 15–30-session and 0.3R–0.5R tests were used to reject a sharp optimum;
  15/0.4 is the best balanced fold/stress center, not the best full-history cell. Exact
  comparisons are in `reports/portfolio_diagnostics/2010-01-04_2026-03-17/stagnation_exit_validation.csv`.
- **Execution stress:** close-only stops plus same-close EMA fills reproduce the old
  15.73% / 18.67% / 0.84 strategy-only result. Hard intraday original/breakeven
  stops plus next-open EMA/stagnation fills produce 14.03% / 15.01% / 0.93. At
  25 bps slippage per side this is 12.97% / 16.13% / 0.80; at 50 bps per side,
  10.80% / 18.62% / 0.58. A conservative entry-day stop-first collision stress is
  14.29% / 15.59% / 0.92; combined with 25 bps it is 11.69% / 16.96% / 0.69.
  Execution quality and unknown intraday sequencing remain binding operational risks.
- **Concentration stress:** cutting max name size from 20% to 10% reduces full-history
  strategy-only CAGR to 9.28% and DD to 11.31% (Calmar 0.82), while improving the
  2023+ slice to 1.79 Calmar. A two-name/40% sector cap lowers full Calmar to 0.74.
  The committed 20% name cap remains aggressive; use smaller sizing for a lower-risk pilot.
  Realized deployment averages 40.4% (median two positions), but reaches 100%; the largest
  sector reached 80.0% of equity and exceeded 48.2% on 5% of sessions. Six of 17 calendar
  years were negative, so low average exposure does not eliminate clustering/regime risk.

The remaining rejection notes in this subsection were measured before the honest-execution
switch unless they explicitly say otherwise. They justify leaving those features disabled,
but must be rerun under `intraday_hard`/`next_open` before any future reconsideration.
- **Rejected entry filters:** tighter `range10`, tighter volume dry-up, and their
  combinations improved selected windows but failed the holdout or adjacent thresholds.
  Raising breakout-volume requirements was directionally harmful in the feature cohorts.
  Keep RS minimum 70, pending window 8 bars, `range10_max=0.08`, and `voldry_max=0.9`.
- **Rejected exits:** earlier breakeven at 1R–2.5R, hard-stop only, EMA10-only, and the
  phased exit. Earlier breakeven materially reduced CAGR; hard-stop only raised drawdown;
  EMA10-only lowered drawdown but lost holdout CAGR/Calmar; phased exits were much worse.
- **Rejected adjacent controls:** offense-score linear/band sizing, no January exclusion,
  risk at 1.0%/1.5%/2.5%, and name caps at 15%/25%. None improved the complete
  fold/full/holdout set. Keep quality sizing off, January excluded, 2% risk, and 20% max name.
- **Regime and breadth gates:** tested benchmark/breadth variants did not improve robustly;
  keep `use_regime_entry_controls: false`.
- **Maximum hold:** 100, 120, 130, 135, 140, 145, 150, and 165-day caps were
  compared across the same folds and holdout. A 135-day cap was the best isolated
  cell (15.73% → 16.49% full-history CAGR), but adjacent 130/140/145-day values did
  not preserve the gain and the earliest fold remained weak. Keep 150 days.
- **Stop normalization:** widening tight initial stops to 2%–4%, and rejecting signals
  below minimum stop distances, produced no stable neighborhood. The apparent 3%
  floor improvement came with worse first-fold behavior and failed at 2.5%/3.5%; it
  was fill-condition confounding, not reliable entry quality. Keep the structural/7%
  capped stop and existing sizing.
- **Failed-breakout re-entry:** a causal prototype waited for an original fill to stop,
  then for a later close to reclaim the original entry with trend/RS/setup confirmation,
  and entered only from the following day. It improved the favorable 2023+ holdout
  but raised full-history drawdown to roughly 21%–26% and cut the earliest-fold Calmar.
  Quarter- and half-risk variants still failed the complete window set. Do not add re-entry.
- **Lagged volatility sizing:** down-only and symmetric 20-day benchmark-volatility
  scaling slightly improved selected full-history cells but reduced holdout CAGR/Calmar.
  Keep fixed per-trade risk; any future volatility overlay must remain lagged by one bar.
- **Sector leadership:** point-in-time 63-day sector-return ranks at thresholds 20–80
  all reduced full-history and/or holdout results. Keep sector caps, but no sector-alpha gate.
- **Fundamental growth:** corrected annual-growth gates at 15%–35% all reduced canonical
  performance on the partial local Screener dataset. The gate remains disabled. Its data
  path now supports actual NSE broadcast timestamps and strict mode refuses lag-proxy files;
  this is research infrastructure, not an accepted source of alpha.
- **Episodic-pivot strategy:** the existing independent prototype was materially negative
  in train and holdout. It remains isolated under `strategies/episodic_pivots/` and must not
  feed SEPA re-entry or signal ranking.
- **Large structural pass (2026-07-17):** next-open retest entries, gap filters,
  failed-breakout exits, Chandelier/ATR trails, wider initial stops, longer holds,
  tail-only pyramids, regime-adaptive EMA10 exits, and adaptive solo-position
  concentration all failed the complete fold/full/validation/stress set. Do not revive
  individual best cells without a new causal hypothesis and new data.
- **Point-in-time fundamentals and momentum acceleration:** official NSE result-filing
  timestamps now cover most 2013–2024 signals, but earnings-growth gates and filing-recency
  sizing failed sealed validation. Momentum acceleration was directionally predictive at
  trade level but hard gates, sizing tilts, and priority ordering did not improve the
  portfolio robustly. Keep both as research features, not production alpha.
- **Relaxing entry quality to increase opportunity supply:** raising `voldry_max` from
  0.90 to 1.00 looked attractive on full history (14.37% / 13.89% / 1.03) but weakened
  two of three earlier folds and had no stable neighborhood. Relaxing range, setup streak,
  or combinations increased drawdown faster than CAGR. Keep the existing entry gates.
- **Pullback-continuation second entry (2026-07-23): rejected.** The default EMA21
  pullback increased average exposure only from 38.3% to 42.0% and had low same-name/month
  overlap with breakouts (3.8%), but worsened full-history performance from
  **12.48% / 24.28% / 0.51** to **11.57% / 30.12% / 0.38**. It failed folds 2–3,
  the 2023+ validation slice, and both 25/50 bps stresses. Three of 54 grid cells passed
  the full-history screen, all using SMA50 plus a 15-session de-dup window, but every one
  failed the required fold/validation Calmar or drawdown bar and both stresses. Keep
  `pullback.enabled: false`; details are in
  `reports/pullback_experiment/2010-01-04_2026-03-17/validation_report.md`.

### Legacy historical notes

The older comparisons below predate the 2026-07-12 D+1 correction. They are retained as
research history and must be revalidated before they justify a future configuration change.

- **"27% joint candidate"** (`best_joint_params.json` → `holdout_candidate`):
  rs55 / vol_mult 1.7 / max_signals 8 / phased exit (be 2.75R, partial 3.5R, trail 0.15/0.10, time_stop 15).
  Looks good ONLY on the 2023+ holdout (27.0% / Calmar 2.03). On **full history it is 15.4% / Calmar 1.16**,
  and the committed config **beats it on every window** (45% vs 27% holdout; 21.2% vs 15.4% full).
  Tested and **rejected**. The high holdout number is a favorable-sub-window artifact, not an upgrade.

- **Pyramiding (adding to winners)** — tested 2026-06-12, `scripts/pyramid_sweep.py`. Swept 1–3 adds
  at +1R/+2R triggers, risk_frac 0.5 and 1.0. **Every variant is worse risk-adjusted.** Best CAGR
  (2 adds @1R x0.5 = 21.69%) barely beats base 21.18% but DD jumps 16.6%→21.9%, Calmar collapses
  1.27→0.99. Reason: adds trigger at +1R on *any* winner; with a 37% win rate most fizzle, so adds
  load size into the noisy middle before pullbacks, not the rare +20R monsters (already full-size).
  **Keep `pyramid_max_adds: 0`.** A *selective* pyramid (add only to confirmed top-RS/high-MFE names)
  is unexplored but is a new feature with overfit risk, not a config flip.

- **Index-ETF parking of idle cash (naive or regime-gated)** — tested 2026-07-11 via
  `cash_returns_by_date` in the canonical sim. Exposure is ~40% because of **signal supply**
  (median 2 open positions; the 20-position cap is never hit), so idle cash is large.
  Naive NIFTYBEES parking: 26.03% CAGR but MaxDD explodes 16.6%→35.4% (cash is high exactly
  in bear markets). Regime-gated (bench>EMA21) *looked* like 61% CAGR — that was **1-day
  look-ahead**; with the flag lagged 1 day + 0.1% switch cost (29 flips/yr) the gated index
  earns 7.9%/yr standalone and the portfolio is 24.21% / 18.31% DD / 1.32 Calmar — worse than
  committed on every axis. **Keep idle cash in a liquid instrument.** Yield scales linearly:
  ~+0.8 CAGR pts per +1% APY (12% control run: 30.69% / 16.53% / 1.86).
- **max_name_pct 0.25/0.30** — tested 2026-07-11. 0.25: 27.29% CAGR but DD 18.84%, Calmar
  1.45 (worse). 0.30: worse on everything (24.24% / 18.35% / 1.32). **Keep 0.20.**
- **rs_rank_min 65/60** — tested 2026-07-11 at `cash_yield_apy: 0` across WF folds. rs65 wins
  folds 1–2 but ties fold3 and slightly loses the 2023+ holdout (44.1% vs 45.3%); full-history
  gain is +0.26 pts (noise). Barely moves exposure (40%→42%) — signal supply, not the RS
  threshold, is the binding constraint. **Keep 70.**
- **risk_per_trade_pct 2.5%/3.0%** — tested 2026-07-11 through the canonical harness. The knob is
  nearly INERT: the 20% `max_name_pct` cap is the binding constraint (median stop ~2.6% means the
  2% budget already wants ~77% of equity per name). 2.5% gained +1.2 pts on full history but LOST
  on 3 of 4 walk-forward windows (holdout 43.3% vs 45.3%) — same artifact class as the 27% candidate.
  3% was worse everywhere. **Keep 2.0%.**

---

## Known traps (why numbers used to differ every run)

1. **Multiple engines.** `simulate_portfolio_python` (canonical, has equity curve) vs
   `engine._simulate_with_portfolio` (discards equity curve) vs per-symbol `_simulate_symbol_group`
   (no portfolio) vs the Rust path. Only the canonical harness above is trustworthy.
2. **Fake CAGR.** `metrics.estimate_compounded_cagr` compounds `1 + risk·R` serially across all
   trades as if each reinvests the full bankroll → inflates (e.g. 44% / 365x). The CLI `backtest`
   command prints this. **Ignore it.** Use the equity-curve CAGR from `--parity`.
3. **Two signal filters.** Raw `signals` table vs `filter_signals` (rs/quality/per-day). The
   canonical path always uses `filter_signals` with the committed `rs_rank_min`.
4. **Dead exit knobs.** When `exit_mode` is a `baseline_*` mode, phased `ExitConfig`
   overrides are inert. The committed hybrid reads `backtest.baseline_tighten_r`; tuning
   phased breakeven/partial/trail percentages changes nothing.
5. **Benchmark omission.** Regime controls require NIFTYBEES OHLCV even when it has no
   trade signal. `load_inputs` now appends the benchmark explicitly; do not remove it.
6. **Pseudo point-in-time fundamentals.** A reporting deadline or `period_end + N days`
   is not an announcement timestamp. If the experimental fundamental gate is ever enabled,
   require actual exchange dissemination dates. The committed strict setting fails closed.
7. **Same-close exit hindsight.** A close-confirmed EMA failure cannot normally fill at
   that already-observed close. The committed model schedules it for the next symbol-session
   open. Do not restore `same_close` to improve a backtest.
8. **Soft stops disguised as hard stops.** The original/breakeven stop is an executable
   intraday level in committed config. `close_only` is available solely to reproduce the
   legacy research result; it is not the live risk model.

## Survivorship — SUPERSEDED 2026-07-19 by the daily-engine cutover

> **UPDATE 2026-07-19.** This section's "not a blocker" conclusion came from a *coarse weekly
> proxy*. The real daily engine on a survivorship-free daily NSE bhavcopy panel now shows
> survivorship **does not inflate CAGR (−1.5 pts) but nearly doubles max drawdown (15→24%)
> and halves Calmar (0.93→0.51)** — it was hiding risk, not return. Production has been cut
> over to the survivorship-free panel. The weekly-proxy analysis below is retained as history.

The yfinance price panel under-covers delisted NSE names: of 3,447 point-in-time EQ symbols
(from bhavcopy snapshots), **36% (1,233) have no yfinance OHLCV**, and of 223 names the committed
config actually traded, only **1 (CAPF) ever stopped trading**. This *looked* like a fatal
survivor bias. It was tested and is not.

Test (`scripts/build_weekly_bhav_panel.py` → `scripts/survivorship_test.py`): built a
survivorship-clean **weekly** panel from the on-disk bhavcopy snapshots (4,073 symbols incl. the
delisted ones; UNADJUSTED prices), then ran one identical coarse weekly momentum strategy two ways:

| Universe | CAGR | MaxDD |
|---|---|---|
| FULL (incl. 1,500 delisted) | 14.75% | 54.4% |
| SURVIVOR-only (2,573 alive) | 13.58% | 50.0% |

**Including dead names was slightly *better*, not worse.** Mechanism: a trend-following exit
(close < 10w EMA / −8% stop) sells a failing name on the trend break long before it delists, so the
strategy almost never rides a position into its terminal collapse. Dead names contribute their up-moves
while alive, downside truncated by the same exit as any loser. The ~1.2-pt gap is within the noise of
a crude test. **Survivorship does not materially inflate this trend-exit strategy.**

Caveats on the test: coarse weekly proxy (not the real daily engine), unadjusted prices (split
artifacts inflate both arms' DD equally), 0.5% forced-exit haircut. A *precise* answer would need a
daily corp-action-adjusted bhavcopy panel — now a nice-to-have, not a go-live gate.

## Alpha vs beta — historical pre-D+1 analysis, requires re-run

The figures in this section were measured before the 2026-07-12 entry correction and
are not evidence for the current strategy. Re-run only after the exit-engine mismatch
is resolved.

`scripts/alpha_vs_beta.py` regresses strategy daily equity returns on benchmarks:

| Benchmark | Strat CAGR | Bench CAGR | Excess | corr | R² | Jensen α (rf=0) | up-cap | down-cap |
|---|---|---|---|---|---|---|---|---|
| Nifty 500 TRI (full) | 21.2% | 11.6% | **+9.6%/yr** | 0.34 | 0.12 | +18.4%/yr | 45% | 28% |
| Nifty Next 50 (full) | 21.2% | 12.4% | +8.8%/yr | 0.32 | 0.10 | +18.8%/yr | 37% | 21% |
| Nifty Midcap 150 (2019+) | 36.6% | 19.3% | **+17.4%/yr** | 0.33 | 0.11 | +34.7%/yr | 55% | 31% |

Beats every benchmark incl. the true midcap peer; correlation ~0.33 (R²~0.11 → ~90% idiosyncratic);
**down-capture ~20–31% vs up-capture 25–55%** — the asymmetry IS the alpha (sits in cash during
weakness, deployed in strength). Not a closet index fund. Earlier "lumpy midcap beta" framing was
wrong: it's lumpy *alpha*. Even subtracting a 6% risk-free, alpha stays +12–28%/yr. **Verdict: deepen it.**

## Remaining caveat (the real one)

The corrected canonical strategy-only outcome set contains 287 fills: 36.2% winners,
average winner +3.34R, average loss −1.03R, and median trade −1.00R. The best 10%
of trades supply about 111% of net R, while the 19 trades at 5R or better supply about
82%. This is a highly tail-dependent trend strategy: clipping exceptional
winners or abandoning it during a quiet regime can erase most of the long-run edge.

The non-aggregated research artifacts are generated by the existing
`scripts/run_signal_quality_research.py` workflow under
`data/backtest/signal_quality_research/`. They include per-signal fill delay, gap,
initial risk, first stop/1R/2R/3R/5R timing, multi-horizon MFE/MAE, price/volume/regime
features, canonical outcomes, and the strategy-only equity curve. Treat cohort results as
hypothesis generation; configuration changes still require fold, neighborhood, and holdout
validation in the canonical harness.

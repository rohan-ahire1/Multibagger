# BACKTEST TRUTH — single source of truth

> **Read this before running, tuning, or "improving" any backtest.**
> Numbers produced by anything other than the canonical harness below are **invalid**
> and must not be used to justify config changes. This file exists because multiple
> independent backtest engines/metrics historically disagreed, and tuning done in one
> tool was lost or contradicted by another. Do not add new backtest scripts.

Last verified: **2026-06-12** · git `6590717` (branch `feat/sepa-terminal-v2`)

---

## The committed config IS the primary config — everywhere

The single source of truth for parameters is **`config/config.yaml`**. The terminal,
paper runner, UI, and the canonical harness all read it. Do not fork parameters into
JSON sidecars, notebooks, or per-script constants.

Key knobs of the committed config (see `config/config.yaml` for the full set):

| Area | Knob | Value |
|---|---|---|
| Exit | `exit_mode` | `baseline_ema21` |
| Exit | `use_extended_exit_model` | `false` (phased `ExitConfig` is NOT active) |
| Entry | `backtest.rs_rank_min` / `rs_rank_max` | `70` / `100` |
| Entry | `max_signals_per_day` | `20` |
| Entry | `signal_ranking_mode` | `default` |
| Sizing | `risk_per_trade_pct` | `0.020` |
| Sizing | `max_positions` | `20` |
| Sizing | `max_name_pct` | `0.20` |
| Sizing | `max_sector_positions` | `4` |

---

## Canonical metrics (honest, equity-curve based)

Window: full history **2010-01-04 → 2026-03-17**. Metric source: the real `daily_equity`
curve from `simulate_portfolio_python` (friction on: slippage + STT/charges + DP fee).
**Not** the serial-R-compound number the CLI prints (see "Known traps").

| Window | CAGR | Max DD | Calmar | Trades |
|---|---|---|---|---|
| **Full 2010–2026** | **21.18%** | 16.63% | 1.27 | 348 |
| 2023+ holdout | 45.3% | 14.7% | 3.08 | 110 |

Committed reference (for parity drift detection): `21.01% / 16.63% / 1.26 / 348`.
Small CAGR drift vs reference is expected as the data window extends past 2026-03-17.

### How to reproduce (the ONLY authoritative command)

```bash
uv run python optimize_joint_wf.py --parity
# expect: CAGR=21.18%  MaxDD=16.63%  Calmar=1.27  trades=348
```

This runs the full production path (quality filters + sector map + exits + friction).
If this number drifts materially, something broke — investigate before trusting any
other result.

---

## Rejected candidates / tested-and-failed changes (do NOT re-wire these)

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
4. **Dead exit knobs.** When `exit_mode` is a `baseline_*` mode, the phased `ExitConfig` overrides
   are inert. Tuning `ExitConfig` while `exit_mode=baseline_ema21` changes nothing.

## Survivorship — TESTED 2026-06-12, NOT a go-live blocker

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

## Remaining caveat (the real one)

- **Regime concentration:** ~4 years (2017 +53%, 2021 +86%, 2023 +70%, 2024 +57%) carry essentially
  the entire CAGR; 2011–2016 is flat-to-down. This is lumpy midcap-momentum beta — long dead stretches
  punctuated by bursts. Behavioral/sizing risk (abandoning before a burst), not a data problem.
- Trade profile: 37.4% win rate, avg win +4.56R / avg loss −0.76R, **top 10% of trades = 74% of profit**.
  Tail-driven, as expected for breakout momentum.

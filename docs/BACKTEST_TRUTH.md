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

## Rejected candidates (do NOT re-wire these)

- **"27% joint candidate"** (`best_joint_params.json` → `holdout_candidate`):
  rs55 / vol_mult 1.7 / max_signals 8 / phased exit (be 2.75R, partial 3.5R, trail 0.15/0.10, time_stop 15).
  Looks good ONLY on the 2023+ holdout (27.0% / Calmar 2.03). On **full history it is 15.4% / Calmar 1.16**,
  and the committed config **beats it on every window** (45% vs 27% holdout; 21.2% vs 15.4% full).
  Tested and **rejected**. The high holdout number is a favorable-sub-window artifact, not an upgrade.

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

## Open caveats (do not over-trust the 21% in absolute terms)

- **Survivorship:** OHLCV is yfinance, which under-covers delisted NSE names. The effective
  symbol panel is thin (~480 names ever signalled) → CAGR is biased upward by an unknown amount.
- **One regime:** 2010–2026 is bull-dominated. Robustness across a real bear is unproven.
- These do not change the *ranking* of configs above, but they cap confidence in the absolute level.

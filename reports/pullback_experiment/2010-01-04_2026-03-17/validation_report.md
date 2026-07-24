# Pullback-Continuation Experiment — Validation Report

Date: 2026-07-23  
Panel: survivorship-free, corporate-action-adjusted NSE bhavcopy  
Signal window: 2010-01-04 through 2026-03-17  
Decision: **REJECT; keep `pullback.enabled: false`**

## Result

The pullback stream is genuinely additive by timing, but it does not improve the
portfolio robustly. The default EMA21 version raises average exposure only from 38.3%
to 42.0%, while reducing full-history CAGR and increasing drawdown. It fails two of the
three regime folds, the 2023+ validation slice, and both execution stresses.

The 54-cell pre-registered neighbourhood produced three full-history survivors. All
three use an SMA50 anchor and a 15-session breakout de-duplication window; none uses the
planned default EMA21 anchor. Every survivor then fails at least one earlier fold,
holdout risk-adjusted criterion, and both slippage stresses. There is no configuration
that clears the complete acceptance bar.

## Gate 0: additivity and exposure

| Measure | Breakout only | Default combined |
|---|---:|---:|
| Filtered signals | 354 | 655 |
| Filled trades | 281 | 431 |
| Average exposure | 38.3% | 42.0% |
| Median open positions | 1 | 2 |
| Maximum open positions | 8 | 8 |

The combined simulation filled 158 pullbacks and 273 breakouts. Six pullback fills had
a breakout fill in the same symbol/month: **3.8% overlap**, well below the 40% rejection
threshold. Gate 0 therefore passes, but the exposure increase is modest.

## Default configuration: canonical window results

| Window | Breakout CAGR | Combined CAGR | Breakout MaxDD | Combined MaxDD | Breakout Calmar | Combined Calmar | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Full | 12.48% | 11.57% | 24.28% | 30.12% | 0.514 | 0.384 | Fail |
| Fold 1: 2010–2014 | 4.79% | 5.02% | 10.62% | 10.64% | 0.451 | 0.472 | Pass |
| Fold 2: 2014–2018 | 4.05% | 4.22% | 24.16% | 29.71% | 0.168 | 0.142 | Fail |
| Fold 3: 2018–2022 | 15.92% | 12.24% | 10.17% | 15.50% | 1.566 | 0.790 | Fail |
| 2023+ | 28.51% | 26.59% | 11.40% | 10.70% | 2.501 | 2.485 | Fail |

## Execution stress

| Slippage per side | Breakout CAGR / MaxDD / Calmar | Default combined | Verdict |
|---|---|---|---|
| 25 bps | 11.67% / 25.33% / 0.461 | 10.08% / 31.51% / 0.320 | Fail |
| 50 bps | 9.85% / 27.53% / 0.358 | 7.62% / 34.72% / 0.219 | Fail |

## Neighbourhood stability

The complete 54-cell full-history screen is in `neighborhood_grid.csv`. Only three
cells passed the full-history CAGR, drawdown, and Calmar screen:

| Anchor | Leader days | Touch | De-dup | CAGR | MaxDD | Calmar | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| SMA50 | 15 | 1.5% | 15 | 13.24% | 24.83% | 0.533 | 325 |
| SMA50 | 20 | 1.5% | 15 | 14.42% | 24.91% | 0.579 | 316 |
| SMA50 | 20 | 2.0% | 15 | 13.09% | 25.04% | 0.523 | 335 |

Their apparent full-history improvement does not survive the required windows:

- SMA50 / 15 days / 1.5%: fold-1 CAGR 4.35% and Calmar 0.380, both below baseline;
  fold-2 CAGR 3.98% and Calmar 0.160 are also below baseline.
- SMA50 / 20 days / 1.5%: fold-1 CAGR 4.62% and Calmar 0.403 are below baseline;
  fold-2 Calmar is 0.166 versus 0.168 baseline; 2023+ Calmar falls to 2.277.
- SMA50 / 20 days / 2.0%: fold-1 CAGR 4.06% and Calmar 0.374, fold-2 CAGR 3.55%
  and Calmar 0.143, and 2023+ MaxDD 13.19% all fail the acceptance criteria.

All three survivors also trail breakout-only at both 25 bps and 50 bps. The surviving
full-history patch is therefore a regime-dependent artefact, not a robust neighbourhood.

## Implementation and causality note

The live detector uses only information available on or before signal date T. Breakout
de-duplication suppresses same-day and prior-window pullbacks. It deliberately does not
remove a pullback because a breakout occurs after T: that would introduce look-ahead.

The optional implementation remains available behind the disabled config flag for
reproducibility, but it is not part of the committed strategy and is excluded from canonical
backtests while disabled.

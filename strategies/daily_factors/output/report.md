# Daily factor comparison

All portfolio strategies use adjusted daily prices, close-to-next-open signals, 10 bps slippage per side, canonical SEPA NSE charge multipliers, and no cash yield.

The trading universe is a point-in-time proxy: the latest historical NSE EQ snapshot, ranked monthly by trailing 63-session turnover. It is not exact historical Nifty 200 membership.

| Strategy | CAGR | Max DD | Calmar | Sharpe |
|---|---:|---:|---:|---:|
| sepa_canonical | 14.03% | 15.01% | 0.93 | — |
| alpha_low_vol_top30 | 16.96% | 31.77% | 0.53 | 1.16 |
| high_52w_alpha_low_vol_top30 | 16.30% | 33.29% | 0.49 | 1.07 |
| momentum_12_1_top20 | 18.79% | 49.43% | 0.38 | 0.82 |
| high_52w_top20 | 12.31% | 36.20% | 0.34 | 0.75 |
| nifty_500_tri | 11.61% | 38.11% | 0.30 | 0.77 |
| equal_weight_universe | 9.34% | 52.65% | 0.18 | 0.58 |

## Calmar by market slice

| Strategy | 2010–14 | 2015–19 | 2020–26 |
|---|---:|---:|---:|
| sepa_canonical | 0.31 | 0.78 | 1.87 |
| alpha_low_vol_top30 | 1.04 | 0.87 | 0.56 |
| high_52w_alpha_low_vol_top30 | 0.90 | 0.43 | 0.58 |
| momentum_12_1_top20 | 0.48 | 0.12 | 0.77 |
| high_52w_top20 | 0.45 | 0.20 | 0.51 |
| nifty_500_tri | 0.34 | 0.45 | 0.38 |
| equal_weight_universe | 0.11 | 0.09 | 0.47 |

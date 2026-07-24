# Autonomous Indian Equity Strategy Research Report

Headline: the strategy beats the Nifty 50 proxy on post-tax CAGR, but loses the risk-adjusted comparison on drawdown and Sharpe; index the money.

## Data Audit
- Source: `data/yf_adjusted/ohlcv_all.parquet` via `ParquetOhlcvStore`.
- Rows/symbols/date range: 6,659,662 rows, 2,831 symbols, 2010-01-04 to 2026-05-29.
- Schema: `{'date': 'Date', 'symbol': 'String', 'open': 'Float64', 'high': 'Float64', 'low': 'Float64', 'close': 'Float64', 'volume': 'Int64', 'turnover': 'Float64'}`.
- Quality flags: 4104 invalid OHLC rows, 426691 zero-volume rows, 1284 absolute daily moves above 45%.
- Universe snapshots: 1111 NSE bhavcopy snapshots available. The parquet appears to be broad historical NSE, not just today's survivors, but delisted coverage is still only as good as yfinance and the collected snapshots.
- Rust core check: Multibagger Rust Core is online!.

## Realism Assumptions
- Signals are computed after day T close and execute at day T+1 open.
- Delivery model: integer shares, no leverage, cash drag, max concurrent positions, and position notional capped at 1.5-2.0% of ADV depending on strategy.
- Costs: brokerage min(₹20/order, 0.03%), STT 0.1% on sell, exchange charges, SEBI fees, stamp duty on buy, GST on brokerage/exchange, plus 0.10-0.25% slippage per side through strategy config.
- Liquidity filter: price >= ₹20 and ADV20 >= ₹20-30m. Illiquid/penny names are excluded.
- Taxes are reported separately using FY26 assumptions: STCG 20%, LTCG 12.5% above ₹1.25L.
- Benchmark: NIFTYBEES adjusted close proxy. Local data does not include official Nifty 50 TRI; NIFTYBEES adjusted OHLCV is used as the closest investable local Nifty 50 total-return proxy.

## Coarse Candidate Results (2015-2022 only)
- mom_126_hold84_r150: CAGR 17.06%, MDD -26.1%, Sharpe 0.56, trades 381
- mom_126_hold63: CAGR 13.23%, MDD -28.0%, Sharpe 0.51, trades 456
- mom_126_hold63_r200: CAGR 13.23%, MDD -28.0%, Sharpe 0.51, trades 456
- mom_leaders_42: CAGR 11.90%, MDD -24.4%, Sharpe 0.56, trades 455
- mom_126_hold42_r100: CAGR 9.03%, MDD -26.8%, Sharpe 0.44, trades 599
- mom_126_hold42_r150: CAGR 9.03%, MDD -26.8%, Sharpe 0.44, trades 599
- breakout_126_vol: CAGR 8.81%, MDD -22.3%, Sharpe 0.43, trades 514
- mom_leaders_84: CAGR 8.40%, MDD -30.3%, Sharpe 0.39, trades 343
- mom_126_top80_conc: CAGR 7.30%, MDD -40.8%, Sharpe 0.38, trades 353
- vol_breakout_252: CAGR 6.21%, MDD -26.1%, Sharpe 0.34, trades 383
- leader_pullback: CAGR -11.83%, MDD -65.2%, Sharpe -0.92, trades 2070

## Selected Strategy
- Name: `mom_126_hold84_r150` (momentum).
- Logic: `momentum_trend` ranked by `rank_mom126`, hold up to 84 trading days, max 12 positions.
- Exit: `below_sma100`, stop ATR=3.0, trailing ATR=None.

## In-Sample vs Final Holdout
- In-sample 2015-2022: pre-tax CAGR 17.06%, post-tax CAGR 11.81%, MDD -26.1%, Sharpe 0.56, win rate 38.3%, trades 381.
- Final holdout 2023-present: pre-tax CAGR 18.74%, post-tax CAGR 11.86%, MDD -23.6%, Sharpe 0.63, win rate 37.7%, avg hold 56.3 days, turnover 26.3x, trades 207.
- Benchmark holdout: pre-tax CAGR 9.06%, post-tax CAGR 8.40%, MDD -15.2%, Sharpe 0.82.
- Strategy minus benchmark: post-tax CAGR spread 3.45%; drawdown spread -8.4 percentage points; Sharpe spread -0.19.

## Cost And Turnover
- Gross no-friction P&L: ₹858,963.
- Slippage cost: ₹39,478; explicit charges/taxes: ₹24,712.
- Annual friction load: 1.88% of initial capital; average cash: 50.5%; estimated annual cash drag vs benchmark: 4.58%.

## One Improvement Pass
- mom_126_hold84_r150: post-tax CAGR 11.81%, MDD -26.1%, Sharpe 0.56, turnover 77.0x, friction 2.26%/yr.
- vol_target_18: post-tax CAGR 8.41%, MDD -24.1%, Sharpe 0.50, turnover 54.7x, friction 1.63%/yr.
- vol_target_16: post-tax CAGR 8.22%, MDD -23.9%, Sharpe 0.49, turnover 53.2x, friction 1.59%/yr.
- lower_turnover_hold126: post-tax CAGR 9.71%, MDD -40.2%, Sharpe 0.51, turnover 53.6x, friction 1.58%/yr.
- lower_turnover_hold168: post-tax CAGR 10.14%, MDD -47.9%, Sharpe 0.52, turnover 48.6x, friction 1.42%/yr.

## Hold x Lookback Plateau
- plateau_lb63_hold63: post-tax CAGR 6.24%, MDD -28.9%, Sharpe 0.48, trades 406.
- plateau_lb126_hold63: post-tax CAGR 7.02%, MDD -28.0%, Sharpe 0.51, trades 456.
- plateau_lb252_hold63: post-tax CAGR 3.32%, MDD -32.2%, Sharpe 0.42, trades 550.
- plateau_lb63_hold84: post-tax CAGR 6.88%, MDD -27.6%, Sharpe 0.48, trades 340.
- plateau_lb126_hold84: post-tax CAGR 11.81%, MDD -26.1%, Sharpe 0.56, trades 381.
- plateau_lb252_hold84: post-tax CAGR 4.88%, MDD -30.9%, Sharpe 0.43, trades 487.
- plateau_lb63_hold126: post-tax CAGR 7.52%, MDD -33.6%, Sharpe 0.48, trades 316.
- plateau_lb126_hold126: post-tax CAGR 9.40%, MDD -42.2%, Sharpe 0.51, trades 360.
- plateau_lb252_hold126: post-tax CAGR 7.12%, MDD -41.7%, Sharpe 0.48, trades 453.

## Walk-Forward
- Test 2015: train CAGR 8.67%, test CAGR 5.87%, MDD -18.8%, trades 48.
- Test 2016: train CAGR 9.20%, test CAGR 21.79%, MDD -23.0%, trades 47.
- Test 2017: train CAGR 10.17%, test CAGR 51.29%, MDD -21.1%, trades 68.
- Test 2018: train CAGR 25.45%, test CAGR -13.63%, MDD -20.7%, trades 55.
- Test 2019: train CAGR 19.53%, test CAGR 0.00%, MDD 0.0%, trades 0.
- Test 2020: train CAGR 17.84%, test CAGR 17.72%, MDD -23.6%, trades 52.
- Test 2021: train CAGR 14.21%, test CAGR 70.29%, MDD -20.6%, trades 74.
- Test 2022: train CAGR 13.12%, test CAGR -10.99%, MDD -24.4%, trades 74.

## Sensitivity And Bootstrap
- Sensitivity variants written to `sensitivity.csv`; performance should be judged by spread, not the single best point.
- Bootstrap trade resampling: `{'return_p05': 0.3544661039306411, 'return_p50': 0.7939189242728983, 'return_p95': 1.2551640872876322, 'maxdd_p05': -0.14291658129793428, 'maxdd_p50': -0.07460975501772077, 'maxdd_p95': -0.04197033520662431}`.

## Biases And Limitations
- yfinance adjusted OHLCV is the price source; splits/bonuses are assumed handled upstream.
- Survivorship bias is reduced by broad historical symbols and NSE snapshots, but not eliminated. Apply a practical 10-20% CAGR haircut before believing any result.
- Daily bars cannot know intraday order sequencing when stop and target both occur; this engine uses conservative stop-first logic where applicable.
- No borrow, impact model beyond ADV cap/slippage, or corporate action event exclusions are added here.

## Verdict
Index the money. The strategy is interesting, but it does not clear the benchmark after tax and drawdown.

A pleasingly unsentimental answer, which is usually the only useful kind.

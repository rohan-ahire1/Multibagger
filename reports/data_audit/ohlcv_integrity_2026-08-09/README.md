# OHLCV integrity audit

Source: `data/nse_adjusted/ohlcv_all.parquet`  
Scope: read-only audit; source dataset was not modified.

## A. Dataset statistics

- rows: 8,345,112
- securities: 4,453
- first date: 2005-01-03
- last date: 2026-07-17

## B. Hard failures

- duplicates: 0 rows across 0 security/date groups
- null OHLC: 0 rows (open 0 / 0.00000000%, high 0 / 0.00000000%, low 0 / 0.00000000%, close 0 / 0.00000000%)
- invalid prices: 0 rows (open 0, high 0, low 0, close 0)
- invalid OHLC relationships: 0 rows (high<open 0, high<close 0, high<low 0, low>open 0, low>close 0)
- negative volume: 0 rows

## C. Suspicious observations

- zero volume: 52 rows
- null volume: 0 rows / 0.00000000%
- large returns: |return| >20% 8,805; >40% 1,642; >80% 399
- observation gaps: >7 days 7,660; >30 days 844; >90 days 190
- abnormal market-day coverage: 1 dates
- extreme price/volume changes: close >10x previous 2; close <0.1x previous 6; volume >100x previous 20-observation median 9,580

## D. Per-test PASS / FAIL / WARNING

| Test | Status | Result |
|---|---|---|
| Duplicates | PASS | 0 duplicate security/date groups |
| Null values — OHLC | PASS | 0 rows with a null OHLC field |
| Null values — volume | PASS | 0 null-volume rows |
| Price validity | PASS | 0 invalid-price rows |
| OHLC consistency | PASS | 0 inconsistent-OHLC rows |
| Volume — negative | PASS | 0 negative-volume rows |
| Volume — zero | WARNING | 52 zero-volume rows |
| Date order after sorting | PASS | 0 duplicate dates (reported separately) |
| Large price moves | WARNING | >20% 8,805; >40% 1,642; >80% 399 |
| Missing-day / gaps | WARNING | >7 days 7,660; >30 days 844; >90 days 190 |
| Security coverage | PASS | 4,453 securities reported |
| Market-day coverage | WARNING | 1 dates below 50% of surrounding median |
| Extreme values | WARNING | price >10x 2; price <0.1x 6; volume >100x median 9,580 |

## E. Final status

PASS = zero hard failures

## Detail files

- `duplicates.csv`
- `null_ohlc_rows.csv`
- `null_volume_rows.csv`
- `invalid_prices.csv`
- `invalid_ohlc_relationships.csv`
- `negative_volume.csv`
- `zero_volume.csv`
- `large_price_moves.csv`
- `observation_gaps.csv`
- `security_coverage.csv`
- `market_day_coverage.csv`
- `extreme_changes.csv`
- `date_order_check.csv`

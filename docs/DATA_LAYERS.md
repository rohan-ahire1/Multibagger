# Research Data Layers

The NSE research pipeline has explicit, non-interchangeable layers:

- `data/nse_raw/` — immutable source observations from NSE bhavcopies. Never edited by a build.
- `data/reference/` — source identity, corporate-action, and lifecycle reference records. A missing source date remains null.
- `data/processed/` — normalized economically raw OHLCV with `security_id`, `isin`, `symbol`, and `identity_quality`.
- `data/adjusted/` — retrospective split-adjusted and total-return series. Their manifests mark them `point_in_time_eligible: false`.
- `data/pit/` — raw-price, identity-aware inputs allowed for historical signal generation. `data.source: nse_bhavcopy` resolves here.
- `data/research/` — reserved for forward returns, MFE/MAE, labels, and other explicitly ex-post fields. Existing legacy research outputs remain outside PIT and signal scanners reject explicit ex-post columns.

Ticker symbols are attributes observed through time, not the canonical security key. An NSE-supplied ISIN becomes `security_id=isin:<ISIN>`. A row with no ISIN uses a scoped `fallback:` identity and remains `identity_quality=unverified`; it is not retroactively joined to a later ISIN.

# India Fundamental Data Options for SEPA (research note, 2026-06-11)

## Why
SEPA's signal engine is purely technical (price/volume). Signal-quality analysis
(2009–2026, 1,731 breakouts) showed the typical signal is ~a coin flip vs the
index; returns are tail-driven and existing features don't separate winners.
Real Minervini SEPA = technical setup **×** fundamental superperformance
(accelerating EPS / sales growth, margin expansion). That fundamental leg is
entirely missing. This note scopes where to get it.

## Requirements (ranked)
1. **Survivorship-clean** — must include delisted/merged/bankrupt names, or the
   backtest inherits the same inflation we already fight with yfinance.
2. **Point-in-time / as-reported** — values keyed to the *actual filing date*
   (with reporting lag), not restated. Otherwise we manufacture look-ahead alpha.
3. **Deep history** — 2010+ minimum (ideally pre-2010) for the backtest.
4. **Quarterly EPS + revenue (sales)**, enough to compute YoY growth & acceleration.
5. **Nifty500 coverage** (~500 names) at least.
6. Affordable + programmatic (API or bulk export).

The backtest needs 1–4 strictly. Live/forward use only needs recent data.

## Options

### Tier 1 — Institutional (only ones that truly satisfy survivorship + point-in-time)
- **CMIE Prowess DX** — gold standard for Indian academic research. ~35k companies
  incl. unlisted & delisted, time series since 1990, periodic releases. Powers the
  IIM Fama-French/momentum factor library. Access: institutional subscription
  (pricey), query-app not a clean REST API. **Best for a survivorship-clean backtest.**
- **Capitaline (Accord Fintech) / Ace Equity** — Indian institutional standard;
  deep statements, ratios, delisted coverage; data feeds available. Subscription.
- **Refinitiv/LSEG, Bloomberg** — excellent, global, very expensive. Overkill.

### Tier 2 — Developer APIs (clean REST; survivorship NOT guaranteed; India depth shallower)
- **EODHD** — NSE/BSE covered; fundamentals incl. revenue, diluted EPS, quarterly
  earnings growth (YoY); markets delisted-tickers data for survivorship work.
  Indian *fundamentals* depth is thinner (earnings roughly 2018+). Cheap (~$20–80/mo).
  **Best price/effort for the live/forward path and a recent-history prototype.**
- **Financial Modeling Prep (FMP)** — 30yr history claim; India coverage exists but
  quality/depth inconsistent for NSE. Verify before trusting.
- **Alpha Vantage / Finnhub** — fundamentals APIs; India coverage thin/inconsistent,
  especially on cheaper tiers. Not recommended as primary for India.

### Tier 3 — India retail (cheap, but shallow history and/or survivorship bias)
- **screener.in** — great UI, ~10yr quarterly results; **no official API** (manual
  export/scrape), current-constituents only (survivorship-biased). Good for spot checks.
- **Trendlyne / Tijori / Tickertape** — retail analytics; backtests usually on
  current index members (survivorship bias).
- **sharpely.in** — notable: explicitly markets point-in-time, bias-free backtesting
  for India. Worth evaluating as a hosted research surface.

### Free / raw
- **NSE/BSE XBRL filings** — free, as-reported (point-in-time by nature),
  survivorship-clean *if* we archive continuously. But raw; needs a parsing/ETL
  pipeline. High effort, full control.
- **yfinance** (current) — India fundamentals shallow + survivorship-biased. Not adequate.

## Recommendation — two-track
1. **Answer "do fundamentals add alpha?" first, cheaply.** Prototype a fundamental
   gate (EPS-growth accel, sales-growth, margin) on **2018+** data via **EODHD**
   (or a one-off Capitaline/Ace pull). Run it through the portfolio engine the same
   way we tested the technical signal. If it doesn't move realized CAGR/Calmar
   *through the engine*, stop — don't buy deep history. (We just learned forward-
   return edge ≠ realized edge; demand the engine test.)
2. **Only if (1) shows real lift**, invest in a **survivorship-clean deep-history**
   source — **CMIE Prowess DX** (or Capitaline) — to build the full 2010+ backtest
   and validate properly.

For ongoing live execution, EODHD's quarterly EPS/revenue is sufficient (live
doesn't need deep history).

## Hard caveat
Adding fundamentals re-introduces **look-ahead risk**. Every fundamental value must
be stamped with its *filing/availability date* plus a conservative reporting lag
(Indian quarterly results land ~30–60 days after quarter-end). Join on
"as-of-trading-date" using only data filed on/before that date, or the backtest
will look brilliant and be fake.

## Sources
- EODHD — https://eodhd.com/ , https://eodhd.com/lp/fundamental-data-api
- EODHD survivorship — https://eodhd.com/financial-academy/financial-faq/survivorship-bias-free-financial-analysis
- CMIE Prowess — https://prowess.cmie.com/
- Capitaline — https://www.capitaline.com/
- screener.in — https://www.screener.in/
- sharpely point-in-time — https://sharpely.in/blog/bias-free-backtesting-explained...
- FMP — https://site.financialmodelingprep.com/
- Alpha Vantage — https://www.alphavantage.co/best_stock_market_api_review/
- Finnhub — https://finnhub.io/

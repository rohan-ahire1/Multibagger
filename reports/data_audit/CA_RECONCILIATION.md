# Corporate-Action Adjustment Reconciliation

**Last verified:** 2026-07-26
**Panel:** `data/nse_adjusted/ohlcv_all.parquet` (survivorship-free NSE bhavcopy, back-adjusted)
**CA source:** `data/reference/nse_corporate_actions.parquet` (NSE corporate-actions API, 39,178 events)

## Current audited state

The evidence-led follow-up reduced the original 538 quarantined discontinuities
to **87**:

| Final bucket | Rows | Decision |
|---|---:|---|
| `manual` | 71 | Insufficient factor evidence; unchanged |
| `complex_action` | 9 | Demerger, rights issue, or scheme; a one-symbol multiplier would be false precision |
| `long_hiatus` | 7 | More than 10 calendar days between observations; not treated as a one-session CA |

There are zero inferable rows. The 454-row override ledger contains 268
purpose-parsed actions, 140 ISIN-corroborated actions, 43 independently reviewed
manual corrections, one ETF inference, and two corroborated but inert seeds.

Purpose parsing now combines every same-date feed leg, calculates the complete
replacement factor, subtracts any leg already represented in the feed, and
requires the remaining factor to agree with the observed move within 25%.
`--write` upserts newly inferred rows without deleting older evidence.

The 71-row residual is bounded diminishing-return debt: 63 rows predate the
2010 parity window. Seven of the eight later rows are known
scheme/demerger/rename boundaries; `VIVIDHA` on 2015-05-28 is the only later
jump without corroborating evidence. None is adjusted by guesswork.

The final ticker-keyed build has 8,345,112 rows, 4,453 symbols, 15,635 applied
CA events, and spans 2005-01-03 through 2026-07-17. Canonical-symbol mode yields
88 quarantined rows versus 87 ticker-keyed; it remains off after the clean A/B
failed to support a re-baseline.

SEPA parity remains **12.48% CAGR / 24.28% MaxDD / 0.51 Calmar / 281 trades**.

## Initial 2026-07-18 audit

## Method

Two independent checks, both leveraging the fact that we now hold **daily** data:

1. **Continuity test (primary).** For every split/bonus ex-date in the CA feed, measure the
   adjusted single-day return across the ex-date. A correct back-adjustment factor removes the
   split/bonus "cliff", so `|adjusted_return|` should be small even where the raw return shows
   a large drop.
2. **Completeness test.** Scan the *adjusted* panel for large single-day jumps (>40%) in the
   tradable universe (close ≥ ₹50) that are **not** near any CA event — these are candidate
   *missed* corporate actions (or genuine limit-up moves).
3. **Cross-check** split events against the independent `yfinance_splits_cache.json`.

## Results

### Continuity (events present in the feed)

| Metric | Value |
|---|---|
| Split/bonus events checked (matched to a trading bar) | 841 |
| Median raw ex-date return (the cliff) | −50.4% |
| Median **adjusted** ex-date return (residual) | **−3.5%** |
| Residual cliffs `|adj_ret|>25%` | **33 / 841 (3.9%)** |
| …of which on names with adj close ≥ ₹50 (could affect backtest) | **13** |

96.1% of known split/bonus events adjust cleanly. The residual 3.9% are almost all
**sub-₹50 penny stocks outside the tradable universe** (e.g. SARVESHWAR ₹4.9, VINNY ₹15,
SUNILHITEC ₹10.6) whose stated bonus ratio understates the actual raw drop — likely
concurrent unrecorded actions or penny-stock data noise. Only 13 residual-cliff events touch
names ≥ ₹50, and those are borderline (₹24–36).

**Bug fixed during reconciliation:** combined subjects like TITAN 2011-06-23
*"Bonus 1:1 / Face Value Split From Rs.10 To Re.1"* were parsed as bonus-only (m=0.5),
dropping the 10:1 split leg (true combined m=0.05). The parser now captures **both legs**
(`action_type='bonus_split'`, 14 events recovered); residual cliffs fell 43→33.

### Completeness (missed actions)

| Metric | Value |
|---|---|
| Tradable (≥₹50) daily jumps `|ret|>40%` | 549 |
| …not near any CA event (candidate missed actions / real moves) | **285** over 21 years (~13/yr) |
| Concentration | 2005–2006: 89 (pre-parity-window); recent years mostly single digits |

The most extreme (KAUSHALYA +99×, ARIHANT +22×) are auto-quarantined by the builder's >4×
guardrail (`reports/data_audit/adjustment_quarantine.csv`, 11 rows). The remainder are
moderate (1.2–1.7×) — a mix of genuine illiquid limit-up moves and minor missed splits.

### Cross-check vs yfinance

NSE-feed splits: 289; yfinance-cache splits: 577; agree on (symbol, date): 89. The apparent
gap overstates the problem — yfinance encodes bonuses as split ratios (so its 577 mixes
splits+bonuses), and the yfinance cache only covers 524 *surviving* symbols (no delisted
names). The **continuity test on the actual panel** is the decision-relevant measure, and it
shows 96% of present events adjust correctly.

## Verdict

**Adjustment is sound for the tradable universe.** 96% of split/bonus events reconcile
cleanly; residual errors are concentrated in sub-₹50 penny stocks the strategy filters out.

**Documented residual limitations (do not affect the survivorship conclusion, add minor
noise to precise returns):**
- CA feed is not fully complete for older (2005–2006) and some recent splits → ~13
  unexplained tradable jumps/yr, mostly outside 2010–2026 or in illiquid names.
- Penny-stock bonus ratios occasionally understate the true action.
- Dividend ex-date `prev_close` uses close-on-or-before ex-date (≈1% approximation; dividend
  adjustment is a small effect).

**Recommended follow-up (optional, diminishing returns):** supplement the NSE CA feed with a
second split source for pre-2011 and delisted names; the extreme-jump quarantine already
protects the backtest from the worst missed actions.

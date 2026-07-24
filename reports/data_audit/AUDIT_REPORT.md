# Market-Data Layer Audit — Multibagger (NSE daily OHLCV)

**Date:** 2026-07-18
**Scope:** Read-only audit of the price panel, storage, corporate-action data, universe
construction, and backtest data consumption. **No data was modified.**
**Auditor artifacts:** `reports/data_audit/` (this report, `data_quality_issues.csv`,
`coverage_by_year.csv`, `listing_activity_by_year.csv`).

---

## 1. Executive summary

**Confidence rating: 55 / 100.**

**Verdict: PASS WITH LIMITATIONS** — safe for *relative* strategy research and
*directional* conclusions, **not** safe for headline absolute performance claims
(CAGR/Calmar) without a survivorship correction.

The good news is that the **methodology** of the pipeline is largely sound: entry
executes on the bar *after* the signal (T+1, no look-ahead), indicators enforce full
warm-up windows (`min_samples = window`, producing nulls not zeros), the trading universe
is built **dynamically** from trailing liquidity as-of each date (not from today's index
membership), primary keys are clean (0 duplicate `symbol+date` rows, 0 nulls), and
corporate-action tables are informational only (no double-adjustment in the signal path).

The bad news is the **input data itself**. The price panel is sourced live from yfinance,
which only returns history for tickers that still resolve on Yahoo today. Delisted and
merged companies whose ticker was retired are largely **absent**, so the otherwise-unbiased
dynamic universe is fed a survivor-only symbol pool. This is a **critical** survivorship
bias that inflates long-only backtest returns. **Quantified (§12a): 16.8% of all securities
that ever traded 2011–2026 are missing from the panel; 587 are genuine historical exits, 216
of them once liquid enough to trade, ~87 distressed/blow-up exits (DHFL, SINTEX, ALBK,
ANDHRABANK, ROLTA…) whose losses the 348-trade backtest never took.** Secondary issues: incremental daily updates
cause split-adjustment drift, back-adjustment produces extreme/negative prices in the early
years, and ~6% of rows are zero-volume (non-traded) but still tradable in simulation.

### Critical blockers
1. **DQ-001 Survivorship bias** — panel excludes retired-ticker delistings/mergers.
2. **DQ-002 Adjustment drift** — incremental 4-day updates desync historical adjustment basis.

### Highest-risk assumptions currently baked in
- "Absence of a symbol before a date = it wasn't listed/liquid." Often it just means
  yfinance no longer serves that ticker.
- "Adjusted close = clean total-return price." It is dividend+split adjusted, but the
  adjustment basis can be internally inconsistent after incremental updates.
- "The dynamic liquidity universe is point-in-time unbiased." True in *construction*, but
  undermined by the survivor-only symbol pool it draws from.

### Most urgent next actions
1. Quantify survivorship: rebuild the symbol pool from the full NSE bhavcopy archive
   (already present under `data/raw/`) including delisted names, re-run the canonical
   `optimize_joint_wf.py --parity`, and measure the CAGR delta. This is the single most
   decision-relevant number.
2. Do a full re-backfill (not incremental) to normalize adjustment basis, and switch the
   update path to store raw OHLC + a maintained cumulative adjustment factor.
3. Quarantine the confirmed corrupt symbol-windows (negative/absurd adjusted prices).

---

## 2. Dataset inventory

| Dataset | Path | Format | Rows | Symbols | Dates | Size | Raw/Transformed | Source | Keys | Dup keys | Nulls |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Price panel (primary)** | `data/yf_adjusted/ohlcv_all.parquet` | Parquet (zstd) | 7,782,874 | 2,831 | 2005-01-03 → 2026-07-17 | 224 MB | Transformed (adjusted) | yfinance (`auto_adjust=False`, back-adjusted) | date+symbol | **0** | **0** |
| US panel | `data/us/ohlcv_all.parquet` | Parquet | — | — | — | 68 MB | Transformed | yfinance | date+symbol | n/c | n/c |
| US small | `data/us_small/ohlcv_all.parquet` | Parquet | — | — | — | 25 MB | Transformed | yfinance | date+symbol | n/c | n/c |
| Corporate actions | DuckDB `corporate_actions` | DuckDB table | 1,571 | — | 2010-01-21 → 2026-03-06 | in 985 MB db | Reference only | mixed | symbol+action+ex_date | — | isin nullable |
| Splits cache | `data/reference/yfinance_splits_cache.json` | JSON | 524 keys | 524 | — | — | Reference | yfinance actions | symbol | — | — |
| Nifty500 constituents | `data/reference/nifty500_constituents.csv` | CSV | 500 | 500 | **single snapshot** | — | Reference | symbol | — | — |
| Nifty500 TRI benchmark | `data/reference/nifty500_tri_2010-01-04_2026-03-17.csv` | CSV | — | 1 index | 2010-01-04 → 2026-03-17 | — | Reference | date | — | — |
| Universe list | `data/reference/nse_eq_universe.yaml` | YAML | 4078 lines | union | — | — | Reference (union of bhavcopy) | NSE bhavcopy | symbol | — | — |
| Signals (runtime) | DuckDB `signals`, `data/signals/*` | DuckDB/parquet/csv | — | — | — | — | Output | derived | — | — |
| PIT financials | `data/reference/nse_pit_financial_*.{csv,parquet}` | CSV/Parquet | — | — | — | — | Reference | symbol+period | — | — |

**Cols/types (panel):** `date: Date`, `symbol: str`, `open/high/low/close: f64`,
`volume: i64`, `turnover: f64`. **Absent:** ISIN, exchange, series, listing_date,
delisting_date, sector, adjusted-vs-raw split, VWAP, trade count, delivery qty.

**Competing/duplicate datasets & risks:**
- Multiple `best_*.json` optimizer sidecars and `optuna/*_best.json` exist — informational,
  `config.yaml` is authoritative per `BACKTEST_TRUTH.md`.
- `data/tmp/` (921 MB) holds ad-hoc scripts and a `sepa_perf_analysis.duckdb` — **unclear
  origin, not reproducible, should not be trusted** as production input.
- `nifty500_constituents.csv` is a **single current snapshot** — a survivorship trap if any
  code ever uses it as a historical universe (backtest does not; it uses the dynamic
  liquidity universe — see §13).

---

## 3–5. Integrity findings (measured)

| Check | Result |
|---|---|
| Rows | 7,782,874 |
| Symbols | 2,831 |
| Duplicate `symbol+date` rows | **0** |
| Null values (any column) | **0** |
| `high < low` | 3,312 rows |
| `high < max(open,close,low)` | 6,643 rows (median rel-err ~1e-16 = float rounding; tail up to 17×) |
| `low > min(open,close,high)` | 7,790 rows |
| Non-positive prices | **4,944 rows / 3 symbols** (LEEL 2,576; INDUSFILA 441; VALECHAENG 252) |
| Negative volume | 0 |
| Zero volume | 479,287 rows (**6.16%**) |
| Zero volume **with** price change | 38,641 rows (impossible → data-quality) |
| `close == prev_close` (stale) | 645,467 rows (**8.3%**) |
| `|ret| > 20%` | 8,985 |
| `|ret| > 40%` | 1,782 |
| `|ret| > 90%` | 739 |
| Single-day > 10× (up) | 110 rows; < −90% : 120 rows |

**OHLC violations and non-positive prices are concentrated in 2005–2012** (480–1,310
violations/yr early vs ~15/yr recent; all non-positive prices pre-2013). Recent data is
materially cleaner (see `coverage_by_year.csv`).

**Extreme back-adjustment artifacts (confirmed):** SEJALLTD +3369× (2009-12-03, adj close
₹17,779), AQYLON +2440× (adj close ₹41,527), GOLDBEES +99× (2019-12-23 = known ETF split),
BAJFINANCE +108× (2005 split). These are split-ratio artifacts, not real moves. Most are
excluded by the liquidity filter but pollute rolling indicators (52w-high, SMA, RS rank)
for windows that touch them.

---

## 6. Trading-calendar audit

- **Weekend rows: 5,552** — sampled and found **mostly legitimate** NSE special sessions:
  Budget Saturdays (2015-02-28, 2025-02-01), Diwali Muhurat (2020-11-14, 2019-10-27 Sun).
  A handful may be tz artifacts; low severity (DQ-009).
- Sessions/year are consistent (248–260), matching the NSE calendar.
- **No forward-fill** in code; missing sessions are simply absent rows. But the pipeline
  does **not distinguish** "no session" from "listed but did not trade" from
  "suspended/delisted" — see §11, DQ-006.

## 7. Missing-value audit

Zero nulls in stored columns. Missing data manifests as **absent rows**, not NaNs.
Consequence: a delisted or suspended stock simply stops appearing — the backtester's
`future_bars` becomes empty and the trade is closed `incomplete_data` (see §11), **not**
marked as a loss. Combined with survivorship this systematically hides failures.

## 8–9. Corporate-action & total-return treatment

**Adjustment method (confirmed in `data_sources/yfinance_adjusted.py`):**
```
ratio      = adj_close / raw_close        # includes splits AND dividends
open/high/low = raw * ratio
close      = adj_close                     # split + dividend adjusted
volume     = split-adjusted (NOT dividend)
turnover   = raw_close * raw_volume        # UNADJUSTED  ← inconsistent
```
- `close` is yfinance **Adj Close → dividend + split adjusted**. The traded price series is
  therefore total-return-ish; strategy returns implicitly include dividends. Comparison vs
  **Nifty500 TRI** is thus fair on the dividend dimension.
- **No double-adjustment**: the `corporate_actions` DuckDB table (1,571 rows) and
  `yfinance_splits_cache.json` are **not applied** to the panel — they are reference/audit
  only. Confirmed by grep; adjustment happens solely inside the yfinance fetch. Good.
- **Turnover is internally inconsistent** with adjusted OHLC (DQ-007): ratio
  `turnover/(adjclose·vol)` has median 1.0 but 75th pct 1.066 with ±inf on bad rows.
- **Adjustment-drift bug (DQ-002, HIGH):** `scripts/yf_daily_update.py` fetches only
  `end − 4 days` and upserts with `keep="last"`. yfinance re-scales the *entire* history's
  Adj Close whenever a new dividend/split occurs, but stored history is never re-fetched.
  Result: the old history remains on its prior adjustment basis while new bars use the new
  basis → a step discontinuity at every split (and micro-steps at dividends) unless a full
  re-backfill is run. This is a leading explanation for the extreme scale-jump returns.

**Reconciliation table:** could not be fully built — the panel has **no stored adjustment
factor** and no ISIN, so an independent factor reconstruction requires re-pulling raw
prices. GOLDBEES (2019-12-23, ~1:100) and BAJFINANCE (2005) match known splits directionally.
Full reconciliation is a recommended follow-up once raw + factor columns exist.

## 10–11. Symbol identity, listing/delisting

- **No ISIN / no security master.** Identity is the NSE ticker string only (DQ-011).
  Renames (MINDTREE→LTIM, CADILAHC→ZYDUSLIFE, GRUH→merged) break continuity: pre-rename
  history is either lost or split across tickers.
- **Delisting handling:** a delisted stock simply stops producing rows. The engine
  (`backtest/engine.py _simulate_signal`) closes such trades as `incomplete_data` at the
  last available bar — **not** at a realistic terminal/liquidation price and **not** as a
  −100% loss. For genuine bankruptcies this understates losses.

## 12. Survivorship-bias audit — **CRITICAL (DQ-001)**

Probe of 24 known dead/merged NSE tickers: **8 MISSING** from the panel — DHFL, KWALITY,
GRUH, MINDTREE, HDFC, ANDHRABANK, CORPBANK, VIJAYABANK, CADILAHC. Present-but-suspended
names survive only because their Yahoo ticker still resolves (RCOM, JETAIRWAYS, YESBANK,
GITANJALI, MANPASAND, INDUSFILA).

Corroborating signatures:
- Only **136 / 2,831** symbols have their last bar >90 days before the panel end. For 15+
  years of NSE this is implausibly low — real delistings number in the many hundreds.
- Symbol count grows monotonically **683 (2005) → 2,707 (2026)**. A point-in-time-complete
  panel would show far more early-year names (many later delisted), not fewer.

**Mechanism:** yfinance returns history only for tickers currently valid on Yahoo. Retired
tickers vanish. The dynamic universe (§13) is methodologically unbiased but is fed a
survivor-only pool → **long-only backtest returns are biased upward.**

### 12a. Survivorship **quantified** against the NSE bhavcopy archive

The `data/raw/universe_snapshots/` archive holds 1,111 **weekly** NSE bhavcopy files
(2005–2026) with raw point-in-time OHLCV + ISIN for *every* symbol trading that day —
including delisted names. Keying on **ISIN** (true security identity; both legacy and 2024+
formats parsed) over the ISIN-available era **2011–2026** (774 weekly snapshots):

| Metric | Count | % |
|---|---|---|
| True securities ever traded (unique ISIN) | 3,972 | 100% |
| **Covered** — ≥1 of its symbols in the panel | 3,304 | 83.2% |
| **Missing** — no symbol in the panel | **668** | **16.8%** |
| ↳ still trading (last seen ≥2025) but absent = **coverage gap** (bug, not survivorship) | 81 | |
| ↳ **genuine historical exits** absent = **survivorship** | **587** | |
| ↳↳ of those, **once liquid** enough to trade (peak close ≥₹50 **and** day-value ≥₹5cr) | **216** | |
| ↳↳↳ **distressed** exit (last close <50% of peak → return-inflating) | **87** | |
| ↳↳↳ healthy exit (merger/buyout, ~neutral) | 129 | |

**216 liquid names the strategy could have held then vanished from the panel**, ~15–24/yr
across 2011–2024 — against a backtest of only **348 total trades drawn entirely from
survivors**. The 87 distressed exits include unambiguous blow-ups absent from the panel:
**DHFL** (₹670→₹16), **SINTEX** (₹184→₹2), **ALBK/Allahabad Bank** (₹218→₹9),
**ANDHRABANK** (₹137→₹10), **ORIENTBANK** (₹363→₹43), **ROLTA**, plus mergers of leaders
(HDFC, MINDTREE, HEXAWARE, CAIRN). A breakout/momentum long strategy would plausibly have
bought several of these during their run-ups; because they are absent, **those losses were
never taken by the backtest.** Lists: `reports/data_audit/survivorship_liquid_exits.csv`,
`survivorship_missing_all.csv`.

*(Caveat: the 87/129 distressed-vs-healthy split uses raw unadjusted last/peak close, so a
few "distressed" names are actually stock-split artifacts, not crashes; the named blow-ups
above are manually confirmed real.)*

**Remediation reality:** a fully corrected **daily** backtest is **not achievable from local
data** — the missing names have only *weekly* bhavcopy prices, and the panel's source
(yfinance) structurally cannot serve retired-ticker daily history. Options: (a) reacquire
daily delisted OHLCV from a paid vendor (Prowess/CMIE, EODHD, or NSE daily bhavcopy
back-archive) keyed on ISIN; (b) reconstruct the missing names at *weekly* resolution from
the bhavcopy archive and run a weekly-approximation sensitivity check; (c) treat the current
absolute CAGR as an optimistic upper bound and haircut it. Only (a) yields a deployable
number.

## 13. Point-in-time universe audit

- **Good:** `strategy/signal_engine.py::_eligible_universe` selects each day by trailing
  liquidity computed as-of that date — `history_count ≥ min_history_days (320)`,
  `close ≥ ₹50`, `adv50 ≥ ₹5cr`, `vol50 ≥ 200k`, top `max_symbols=1500` by `adv50`. This is
  **dynamic and unbiased in construction** — it does NOT apply today's index membership
  historically.
- **Caveat:** `nifty500_constituents.csv` is a single current snapshot. Backtest does not
  use it as a historical universe, but any future code that does would inject survivorship.
- **Undermined by DQ-001:** point-in-time correctness is only as good as the symbol pool,
  which is survivor-biased.

## 14–17. Look-ahead, warm-up, sorting

- **Entry timing (no look-ahead):** `_simulate_signal` computes
  `future_bars = rows where date > signal_date` and enters on the next qualifying bar's
  open/high. Signal on T → earliest execution T+1. ✔
- **Warm-up enforced:** all rolling indicators use `min_samples = window`
  (`indicators/sma.py`, `highs_lows.py`, `tightness.py`), yielding nulls (not zeros) before
  enough history; `history_count ≥ 320` gate exceeds the 252 (52-week) and 200 (SMA200)
  requirements. ✔

| indicator | required | implemented | partial allowed | pass |
|---|---|---|---|---|
| SMA50/150/200 | 50/150/200 | `min_samples=window` | no | ✔ |
| 52-week high/low | 252 | `min_samples=252` | no | ✔ |
| ATR / tightness / contraction | period | `min_samples=window` | no | ✔ |
| Universe gate | — | `history_count ≥ 320` | no | ✔ |

- **Sorting:** every `over("symbol")` window is preceded by `sort(["symbol","date"])`; no
  cross-symbol contamination observed. ✔
- **Look-ahead in preprocessing:** none found — no full-series normalization/winsorization;
  transformations are mechanical per-row or trailing-window.

## 18–21. Cross-source, benchmark, liquidity, execution

- **Cross-source:** only one OHLCV provider (yfinance). No independent reconciliation source
  wired in; bhavcopy is used only for symbol discovery. Cannot cross-validate prices today.
- **Benchmark mismatch (DQ-008, MEDIUM):** regime gate uses **NIFTYBEES** (Nifty50 ETF,
  price return, tracking error) while performance is reported vs **Nifty500 TRI** — two
  different indices for two different purposes. Dividend treatment is fair (both total
  return) but the index breadth and the regime-vs-reporting inconsistency should be aligned.
- **Liquidity/execution:** the liquidity filter is trailing and sound, but **circuit
  locks** are not modeled — entries/exits assume fills at open/stop even on upper/lower
  circuit days; 6.16% zero-volume rows and 38,641 zero-volume-with-price-move rows mean some
  simulated fills occur on non-traded days (DQ-006). Small/illiquid fills are optimistic.

## 22–25. Returns, preprocessing, outliers, completeness

- Returns are close-to-close on the adjusted series; delisting closes as `incomplete_data`
  (not a modeled loss) — see §11.
- No future-aware preprocessing found (§14).
- Outliers documented in §3 and `data_quality_issues.csv`; **not deleted** per instructions.
- Yearly completeness in `coverage_by_year.csv`; quality regime shift visible ~2013 (early
  years far noisier).

## 26–28. Reproducibility, storage, code-path

- **Reproducibility (DQ-012, LOW→HIGH for research integrity):** no manifest, no checksums,
  no immutable raw snapshot. Re-pulling yfinance yields *different* numbers (adjustment
  drifts, tickers disappear). The dataset is **not deterministically reproducible.**
- **Storage:** single flat `ohlcv_all.parquet` (zstd), no partition drift, reads cleanly;
  `scan_lazy` falls back to hive partitions if the flat file is absent (not currently used).
- **End-to-end code path (traced):** `ParquetOhlcvStore.read_frame` →
  `signal_engine._prepare_frame` (indicators, all trailing) → `_eligible_universe` (dynamic
  liquidity, as-of date) → RS ranks (cross-sectional, same-date) → signals → `engine`
  simulates entry at T+1 open/high → phased/baseline exits. Confirmed for a normal large-cap
  (BAJFINANCE), a CA-heavy name (GOLDBEES), and a problematic delisted name (INDUSFILA —
  negative-price window, would be filtered by `close ≥ ₹50`/`close ≤ 0`).

---

## 30. Severity roll-up

| Severity | Count | Issues |
|---|---|---|
| **Critical** | 1 | DQ-001 (survivorship) |
| **High** | 4 | DQ-002 (adjustment drift), DQ-003 (extreme adj artifacts), DQ-004 (negative prices), DQ-006 (zero-volume tradability) |
| **Medium** | 4 | DQ-005 (OHLC violations), DQ-007 (turnover inconsistency), DQ-008 (benchmark), DQ-010 (PIT pool) |
| **Low** | 3 | DQ-009 (weekend rows), DQ-011 (no ISIN/master), DQ-012 (reproducibility) |

Full register with evidence, affected rows, and fixes: `reports/data_audit/data_quality_issues.csv`.

---

## Bias report (§ required deliverable 5)

| Bias | Status | Evidence |
|---|---|---|
| **Survivorship** | **PRESENT — critical** | 8/24 dead tickers missing; only 136/2831 truncate early; monotonic 683→2707 |
| **Look-ahead** | Not found | T+1 entry; trailing windows; warm-up enforced |
| **Universe** | Low (construction sound) | dynamic liquidity as-of date, not current index; but pool is survivor-biased |
| **Selection** | Low | no performance-based row/column filtering found |
| **Corporate-action** | Present — high | incremental-update adjustment drift; extreme/negative adj prices |
| **Delisting** | Present — high | delisted stocks absent; survivors closed as `incomplete_data`, not as losses |
| **Liquidity** | Medium | circuits unmodeled; zero-volume days tradable |
| **Benchmark mismatch** | Medium | NIFTYBEES (regime) vs Nifty500 TRI (reporting) |
| **Data-snooping** | Not assessed here | out of data-layer scope; heavy optimizer surface exists (see BACKTEST_TRUTH.md) |

## Data lineage (actual)

```
yfinance (.NS, auto_adjust=False, actions=True)          [live, non-immutable]
  → normalize_history_frame: close=AdjClose; OHL=raw*(adj/raw); vol=split-adj; turnover=raw
  → download_adjusted_history (batch, unique keep=last)
  → ParquetOhlcvStore.upsert (dedupe date+symbol keep=last, sort)
      → data/yf_adjusted/ohlcv_all.parquet                [PRIMARY PANEL]
  (daily: yf_daily_update fetches only end-4d → adjustment drift, DQ-002)

  → signal_engine._prepare_frame (trailing indicators, warm-up enforced)
  → _eligible_universe (dynamic liquidity as-of date)     [unbiased construction]
  → RS ranks (cross-sectional, same date)
  → signals → backtest/engine (_simulate_signal: entry T+1 open/high)
  → portfolio → metrics; compared vs Nifty500 TRI (regime gate uses NIFTYBEES)

corporate_actions (DuckDB) + splits_cache.json  →  REFERENCE ONLY (not applied)
nifty500_constituents.csv                        →  single snapshot (not used as historical universe)
```

## Remediation plan (priority order)

1. **DQ-001 Survivorship (Critical).** Reconstruct the symbol pool from the full NSE
   bhavcopy archive under `data/raw/` (includes delisted names), attach terminal prices for
   delisted series, feed into the dynamic universe. Validate by re-running
   `uv run python optimize_joint_wf.py --parity` and reporting ΔCAGR/ΔMaxDD vs the committed
   21.18% / 16.63% baseline. *Requires raw data reacquisition.*
2. **DQ-002 Adjustment drift (High).** Replace incremental Adj-Close updates with either
   (a) periodic full re-backfill, or (b) storing raw OHLC + a maintained cumulative
   adjustment-factor column applied at read time. Files: `scripts/yf_daily_update.py`,
   `data_sources/yfinance_adjusted.py`, `storage/parquet_store.py`. Validate:
   adjustment-factor continuity test (§29).
3. **DQ-004 / DQ-003 Corrupt prices (High).** Quarantine confirmed corrupt symbol-windows
   (LEEL, INDUSFILA, VALECHAENG; SEJALLTD/AQYLON extreme). Validate: positive-price and
   split-ratio cross-check tests.
4. **DQ-006 Tradability (High).** Add a per-row tradable flag (volume>0, not circuit-locked)
   and enforce at execution in `backtest/engine.py`. Validate: no fills on zero-volume days.
5. **DQ-008 Benchmark (Medium).** Use Nifty500 TRI consistently for regime + reporting.
6. **DQ-011 / DQ-012 (Low).** Add ISIN-keyed security master + dataset manifest/checksums.

## Recommended read-only test suite (§29)

Implement as pytest/SQL that **fail loudly**: (a) PK uniqueness `symbol+date`; (b) OHLC
invariants with float tolerance; (c) positive-price; (d) zero-volume-with-price-move = 0;
(e) adjustment-factor continuity (no >2× unexplained jumps absent a CA record); (f) warm-up
(no signal before `history_count ≥ required`); (g) chronological sort per symbol;
(h) survivorship canary (a fixed list of known delisted tickers must be present once fixed);
(i) determinism (rebuild → identical checksum given a frozen raw snapshot).

---

## Audit verdict

# PASS WITH LIMITATIONS

**Justification.** The *pipeline* is methodologically clean on the dimensions that most
often silently break backtests: no look-ahead (T+1 execution), enforced indicator warm-up,
correct chronological windowing, a dynamically-constructed point-in-time liquidity universe,
clean primary keys, and no double corporate-action adjustment. On those grounds the system
is usable for **relative** and **directional** research, and the committed
21.18%/16.63%/1.27 baseline is internally self-consistent.

However, the **input data carries a critical survivorship bias** (delisted/merged
retired-ticker names are absent) plus high-severity corporate-action drift and early-period
price corruption. These bias long-only absolute returns **upward** by an as-yet-unquantified
amount. Until survivorship is corrected and the adjustment basis is stabilized, **headline
absolute performance numbers must be treated as optimistic upper bounds**, not deployable
truth. Fix DQ-001 and DQ-002, re-run `--parity`, and re-audit before trusting absolute CAGR.

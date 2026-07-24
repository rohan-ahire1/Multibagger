# Plan: Take SEPA Live — Manual Execution, Small Pilot

## Summary

Operational go-live path for the locked SEPA strategy. Orders are placed
**manually on Dhan** off a daily decision sheet, starting with a **₹5–10L
pilot**, and only after a **4–8 week forward-paper gate**. The strategy logic,
parameters, sizing, and exits are unchanged from the validated/locked config —
"manual" means the human keys the computed orders into the broker, not that the
human improvises sizing or exits. Deviating from the computed size/stop/exit
means you are no longer trading the validated strategy.

## Decisions (locked with user)

- **Execution:** fully manual. Place orders on the Dhan app from the daily sheet.
- **Capital:** ₹5–10L pilot. Scale only after live tracks the backtest.
- **Go-live gate:** forward-paper 4–8 weeks, then small live.

## Frozen strategy parameters (source of truth: `config/config.yaml`)

Trade gate RS≥70; pending_days 8; max_hold_days 150; breakeven 1.0R; partial
2.0R; trail_pct_bull 0.20 / bear 0.15; time_stop 15; max_positions 20;
max_sector_positions 4; risk_per_trade_pct 2%. Do not change these during the
pilot — the validation only applies to this exact set.

## Known caveats carried into live (do not forget)

1. **Survivorship:** yfinance under-covers delisted names → backtest 20.7% CAGR
   is **inflated; treat ~20% as a ceiling, not an expectation.**
2. **Concentration:** the tight ~2.6% median stop makes the risk-sizer want
   ~77% of the book in one name (observed 65% in a single position). **Must be
   capped before any live money** (Phase 0.1).
3. **Capacity:** a non-issue at ₹5–10L (positions are <1% of daily volume).
   Becomes relevant only above ~₹50L–₹1Cr; revisit before scaling there.

---

## Phase 0 — Prerequisites (before any live rupee)

### 0.1 Per-name concentration cap (CODE — blocking)
Add a hard cap on single-position size as a fraction of equity (default **20%**,
config-driven) to the portfolio sizing path so a tight stop can no longer load
the book into one name. Applies in `simulate_portfolio_python` (and
`PortfolioManager`) at the point shares are computed from the risk budget: after
risk-based shares, also clamp `shares <= floor(max_name_pct * equity /
entry_price)`. Re-run the backtest to confirm the cap doesn't materially harm
the locked metrics (expect slightly lower CAGR, lower tail risk).

### 0.2 Pilot capital config
Set `portfolio.initial_capital` for the pilot via an override the live tooling
reads (env var `SEPA_CAPITAL` or a `--capital` flag), default the pilot to the
chosen ₹5–10L. Do not edit the committed config's 10L baseline used by the
backtest; pass pilot capital at runtime.

### 0.3 Data-staleness guard (CODE)
The daily runner must refuse to emit a trade sheet if OHLCV is older than the
last trading day, or if a signal's price is implausible (e.g. >50% gap vs prior
close). Abort with a clear message rather than emit bad tickets.

---

## Phase 1 — Daily decision sheet (CODE)

Extend `scripts/sepa_paper_daily.py` (or a sibling `sepa_live_daily.py`) so the
daily run, after `--update`, emits an execution-ready **Trade Sheet** for the
pilot capital:

- **ENTER (today's RS≥70 breakouts):** symbol, entry trigger (= breakout
  `entry_ref`), stop, **computed shares for pilot capital** (risk 2% / (entry−stop),
  clamped by the per-name cap and cash), sector, est. cost. Manual rule: place a
  **buy-stop-limit at the entry trigger** with the listed stop; if already past
  trigger intraday, skip (don't chase).
- **EXIT today:** open positions the engine exits today (stop/trail/time/partial),
  with the action (sell all / sell half / new trailing stop level).
- **HOLD:** current positions with their current stop to keep working.

Output to console + `data/live/sepa_live/trade_sheet_<date>.txt` and append a
running log. This is the only thing the user reads each evening.

---

## Phase 2 — Forward paper (4–8 weeks, no real money)

Protocol:
- Run the daily sheet every evening after NSE close (`--update`).
- Record what you *would* place; the paper book (`data/live/sepa_paper/`) tracks
  the modelled equity/positions automatically.
- **Weekly check:** does the paper book behave sanely? Any data gaps, weird
  signals, or workflow friction?

**Go/No-Go after the window (all must hold):**
- No data-integrity aborts that weren't quickly explainable.
- Signals and sizing look reasonable; no surprise concentration (cap working).
- You are comfortable executing the sheet in the available time each day.
- Paper equity path is consistent with backtest expectations for the period.

If No-Go: extend paper or fix the gap. Do not start live to "see what happens".

---

## Phase 3 — Small live (₹5–10L)

- Place orders manually per the daily sheet. **Follow the computed size and
  stop exactly.**
- **Reconciliation (CODE):** record actual fills (date, symbol, fill price, qty)
  into a live state file `data/live/sepa_live/portfolio.json`. The daily runner
  must drive exits/trailing off **your actual fills**, not the model's, so the
  EXIT/HOLD sections reflect your real book. This is the one extra piece beyond
  the paper runner.
- Reconcile weekly: live state vs broker statement (qty, avg price, cash).

---

## Risk controls / kill switches

- Per-trade risk 2%, per-name cap 20% (Phase 0.1), max 20 positions, 4/sector.
- Portfolio drawdown throttle: confirm `drawdown_half/stop/close_pct` are active
  in the live path; they scale down / halt new entries as equity falls.
- **Manual kill switch:** if live drawdown exceeds 25% from peak, stop all new
  entries; manage existing positions to their stops only, and review.
- Abort-on-stale-data guard (Phase 0.3).

## Scaling ladder (post-pilot)

Only after ≥2–3 months of small-live tracking the backtest within reason:
₹10L → ₹25L → ₹50L → … At ₹50L+, re-check capacity (position vs ADV) and the
concentration cap; before large size, source a survivorship-clean data set or
discount expected return further.

---

## Implementation Changes (for Codex)

1. **Concentration cap:** add `portfolio.max_name_pct` (default 0.20) to settings
   + config; clamp shares in `simulate_portfolio_python` and `PortfolioManager`.
2. **Pilot capital at runtime:** `--capital` / `SEPA_CAPITAL` override in the
   live runner; do not hardcode.
3. **Daily Trade Sheet:** extend `sepa_paper_daily.py` to emit ENTER/EXIT/HOLD
   tickets with computed shares for the runtime capital; write to
   `data/live/sepa_live/trade_sheet_<date>.txt`.
4. **Live state + reconciliation:** `data/live/sepa_live/portfolio.json` of actual
   fills; daily runner computes exits/trailing from it.
5. **Data-staleness / price-sanity guard:** abort the run on stale or implausible
   data.

## Test Plan

- Unit: concentration cap limits single-position fraction; daily sheet computes
  correct shares for a given capital and stop; live state round-trips fills;
  staleness guard trips on old data.
- Dry-run the daily sheet on 3 historical dates with known breakouts; verify
  ENTER/EXIT rows match the backtest's actions on those dates.
- Re-run the full backtest with the concentration cap; confirm metrics stay in
  the same ballpark (CAGR within a few points, maxDD not worse).

## Assumptions & Risks

- Backtest return is inflated by survivorship; pilot is sized to make that safe.
- Manual execution adds timing/slippage vs the model; faithfulness to the sheet
  is the main operational risk.
- No Dhan API in this plan; fill reconciliation is manual and must be done
  weekly to keep exits correct.

## Acceptance criteria

- Concentration cap implemented, tested, and backtest re-validated.
- Daily Trade Sheet produces correct, decision-ready tickets, verified against
  the backtest on historical dates.
- Live state reconciliation working (exits driven by real fills).
- 4–8 week forward-paper completed with a documented Go/No-Go review before any
  real order.

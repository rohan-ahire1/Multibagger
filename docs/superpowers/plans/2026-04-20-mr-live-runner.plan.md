# Plan: Mean Reversion Live Runner (Semi-Automated)

## Summary

Build a daily live-trading runner for the Connors RSI2 mean reversion strategy. Semi-automated: runs after market close each day, generates buy/sell instructions, sends via Telegram, user places orders manually on Dhan. A parallel paper portfolio tracks signals automatically for validation.

Locked config: RSI threshold=35, ATR mult=3.5, exit=sma3, risk=4%, max_positions=5, max_sector_positions=3, cost=0.2%.

---

## Architecture

```
strategies/mean_reversion/live/
    __init__.py
    portfolio_state.py     # JSON-backed position store
    paper_portfolio.py     # Paper trade auto-fill logic
    signal_runner.py       # Daily signal generation + exit checks
    notifier.py            # Telegram formatting + send
    run_daily.py           # Main orchestrator (cron entrypoint)
    config.py              # Live config constants (locked params)
```

State files (git-ignored):
```
data/live/mr_portfolio.json        # Live portfolio state
data/live/mr_paper_portfolio.json  # Paper portfolio state
data/live/mr_paper_trades.csv      # Paper trade log (append-only)
```

---

## Implementation Changes

### 1. `strategies/mean_reversion/live/config.py`

Constants for locked strategy parameters. No YAML loading — these are frozen for live.

```python
LOCKED_PARAMS = {
    "rsi_threshold": 35,
    "stop_atr_mult": 3.5,
    "exit_mode": "sma3",
    "time_stop_days": 5,
    "max_hold_days": 20,
    "risk_per_trade_pct": 0.04,
    "max_positions": 5,
    "max_sector_positions": 3,
    "round_trip_cost_pct": 0.002,
    "initial_capital": 500_000.0,   # ₹5L — user-configurable via env var MR_CAPITAL
}
```

`initial_capital` is read from env var `MR_CAPITAL` if set, else defaults to 500000.

---

### 2. `strategies/mean_reversion/live/portfolio_state.py`

Dataclass `LivePosition` and class `PortfolioState`.

`LivePosition` fields:
- symbol: str
- entry_date: date
- entry_price: float
- stop: float
- active_stop: float
- shares: float
- initial_risk: float
- cost_basis: float
- rsi2_at_entry: float
- atr14: float
- is_paper: bool

`PortfolioState` methods:
- `load(path: Path) -> PortfolioState` — reads JSON, returns empty state if file missing
- `save(path: Path)` — writes JSON
- `add_position(pos: LivePosition)`
- `close_position(symbol: str, exit_price: float, exit_date: date, reason: str) -> dict` — returns closed trade record
- `open_symbols() -> set[str]`
- `equity(current_prices: dict[str, float]) -> float` — cash + mark-to-market
- `sector_count(sector: str, sector_map: dict[str, str]) -> int`

State JSON schema:
```json
{
  "cash": 500000.0,
  "positions": [
    {
      "symbol": "RELIANCE.NS",
      "entry_date": "2026-04-18",
      "entry_price": 1234.5,
      "stop": 1180.0,
      "active_stop": 1180.0,
      "shares": 12.5,
      "initial_risk": 54.5,
      "cost_basis": 15431.25,
      "rsi2_at_entry": 8.2,
      "atr14": 32.1,
      "is_paper": false
    }
  ],
  "closed_trades": []
}
```

---

### 3. `strategies/mean_reversion/live/signal_runner.py`

Function `run_daily_signals(today: date, state: PortfolioState, paper_state: PortfolioState) -> DailyResult`.

`DailyResult` dataclass:
- exits: list[dict]         — positions to exit (symbol, exit_price_est, reason)
- entries: list[dict]       — new signals (symbol, entry_price_est, stop, shares, cost_basis, sector)
- paper_exits: list[dict]
- paper_entries: list[dict]
- as_of_date: date

Logic:

**Step 1 — Load data:**
- Call `scripts/yf_daily_update.py` logic inline (or import) to ensure today's EOD bar is present
- Load OHLCV from parquet store for all Nifty 100 symbols + benchmarks
- Build symbol_data via `build_mr_symbol_data(ohlcv)`

**Step 2 — Check live exits:**
For each open position in `state`, get today's bar and call `evaluate_mr_exit()`. If `SELL_ALL`, add to exits list with `exit_price_est = today's close` (user will execute at tomorrow's open — note this in the message).

**Step 3 — Generate new entry signals:**
Call `scan_mean_reversion(ohlcv, rsi_threshold=35, stop_atr_mult=3.5, ...)` filtered to `signal_date == today`.
Filter out:
- Symbols already in open positions
- More than `max_positions` total
- Sector cap violations
- Open price at signal would be ≤ stop (can't check until tomorrow — flag as conditional)

Calculate position size: `(equity * risk_pct) / (entry_est - stop)` where `entry_est = today's close` (proxy for tomorrow's open).

**Step 4 — Repeat steps 2-3 for paper_state** (same logic, separate state).

**Step 5 — Paper auto-fill:**
For paper portfolio: auto-fill yesterday's pending entries at today's open price (if open > stop). This mimics next-day-open fill logic from backtest.

---

### 4. `strategies/mean_reversion/live/notifier.py`

Function `send_daily_report(result: DailyResult, bot_token: str, chat_id: str)`.

Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from env (same as SEPA scanner — reuse existing `.env`).

Uses `requests` directly (already a dep). Sends a single formatted message per run.

Message format:
```
📊 MR Daily Report — 2026-04-20

🔴 EXIT TOMORROW AT OPEN
• RELIANCE — close SMA3 cross | Est. exit: ₹1,245 | P&L est: +₹3,200

🟢 ENTER TOMORROW AT OPEN  
• HDFCBANK — RSI2=8.3 | Entry est: ₹1,680 | Stop: ₹1,610 | Size: 4 shares (₹6,720) | Risk: ₹280
• INFY — RSI2=12.1 | Entry est: ₹1,420 | Stop: ₹1,355 | Size: 5 shares (₹7,100) | Risk: ₹325

⚪ No action needed for other positions.

📋 PAPER PORTFOLIO
Equity: ₹6,24,300 | Positions: 3/5 | Cash: ₹2,10,000
Paper fills today: WIPRO @ ₹520 ✓

⚠️ Place orders manually on Dhan at market open tomorrow.
```

Paper section is a compact summary, not per-signal detail (to keep message readable).

---

### 5. `strategies/mean_reversion/live/paper_portfolio.py`

Thin wrapper around `PortfolioState` with auto-fill logic.

Function `process_paper_fills(paper_state: PortfolioState, symbol_data: dict, today: date, pending_signals: list[dict]) -> list[dict]`:
- For each pending signal from yesterday, check today's open price
- If open > signal's stop → fill at open price, add position to paper_state
- Return list of filled trades

Append fills to `data/live/mr_paper_trades.csv` (append mode, never overwrite).

---

### 6. `strategies/mean_reversion/live/run_daily.py`

Main entrypoint. Orchestrates everything.

```
Usage: python -m strategies.mean_reversion.live.run_daily [--date YYYY-MM-DD] [--dry-run]
```

- `--date`: override today's date (for backtesting the runner itself)
- `--dry-run`: print report to stdout, skip Telegram send and state save

Flow:
1. Load live portfolio state from `data/live/mr_portfolio.json`
2. Load paper portfolio state from `data/live/mr_paper_portfolio.json`
3. Run `run_daily_signals(today, state, paper_state)`
4. Print DailyResult to stdout
5. If not dry-run: save updated states, send Telegram
6. Append paper fills to `data/live/mr_paper_trades.csv`

---

## Data Files

Add to `.gitignore`:
```
data/live/mr_portfolio.json
data/live/mr_paper_portfolio.json
data/live/mr_paper_trades.csv
```

Create `data/live/` directory with a `.gitkeep`.

---

## Cron Setup (post-implementation, user configures)

```cron
30 16 * * 1-5  cd /path/to/multibagger && .venv/bin/python -m strategies.mean_reversion.live.run_daily
```

Runs at 4:30 PM IST on weekdays. Script handles market holidays gracefully (no new bars = no action, sends "no signals today" Telegram message).

---

## No Dhan API Integration

For semi-automated trading, Dhan API is not needed. User reads Telegram message and places orders manually on the Dhan app. Dhan API integration (for reading positions, order status) can be added in a future phase once paper trading is validated.

---

## Test Plan

1. `--dry-run` with a historical date to verify signal generation matches backtest signals for that date
2. Run for 5 consecutive days in paper mode, compare paper fills vs `simulate_mr_portfolio` output for same period
3. Verify Telegram message sends correctly
4. Verify state JSON survives round-trip (save → load → save)
5. Verify paper_trades.csv appends without overwriting on second run

---

## Assumptions

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` already set in `.env` (reused from SEPA scanner)
- `data/yf_adjusted/ohlcv_all.parquet` kept current by existing `yf_daily_update.py`
- Nifty 100 universe is static (same `load_nifty100_universe()` call)
- User runs `yf_daily_update.py` before `run_daily.py` each evening, or `run_daily.py` calls it internally
- No broker API needed for phase 1
- `MR_CAPITAL` env var overrides default ₹5L starting capital

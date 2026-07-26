# AGENTS.md

## BACKTEST GROUND TRUTH — read first

Before running, tuning, or "improving" any backtest, read **`docs/BACKTEST_TRUTH.md`**.
- `config/config.yaml` is the single source of truth for parameters — the primary config everywhere.
- The only authoritative backtest command is `uv run python optimize_joint_wf.py --parity`
  (committed NSE config = **12.48% CAGR / 24.28% MaxDD / 0.51 Calmar / 281 trades**, full history).
- **21.18% CAGR / 16.63% MaxDD / 1.27 Calmar / 348 trades** is the legacy
  survivor-biased yfinance result, not the current ground truth.
- Ignore the CLI `backtest` headline CAGR — it uses a serial-R-compound metric that inflates results.
- Do NOT create new backtest scripts or fork params into JSON sidecars. Any number not from the
  canonical harness is invalid and must not justify config changes.

## Learned User Preferences

- When given a `.plan.md` file, implement it exactly as specified — never edit the plan file itself
- Complete all todos in a single session; do not stop until every task is done
- Scanner and backtester must always operate on the full symbol universe — never add single-symbol filtering to the default UI flow
- Charts must always render all available historical data across the entire timeframe, not just a window around the signal
- SMA and indicator overlays on charts must be computed from all available data, not just the visible chart range
- Trade tables should prominently show return_pct, R-multiple, and holding_days with green/red row highlighting for wins/losses
- Dates displayed anywhere in the app must align with dates that actually have data — never show empty/mismatched dates
- When implementing plans, mark todos as in_progress sequentially and do not recreate existing todos
- User expects working results quickly and gets frustrated by broken UI states — always verify changes work end-to-end
- Historical data downloads should cover from 2010 to present
- Humor is important in this workspace: use concise dry British humor by default in responses, include at least a light witty touch in most replies, keep it subtle and workmanlike, and never let it undercut clarity, correctness, or urgency

## Learned Workspace Facts

- Project is an NSE (Indian stock market) SEPA/VCP-style scanner, backtester, and terminal
- Data pipeline: NSE bhavcopy archive -> `data/nse_raw/ohlcv_raw.parquet` -> corporate-action adjustment -> `data/nse_adjusted/ohlcv_all.parquet` -> `ParquetOhlcvStore` -> Signal Engine -> DuckDB (signals, runs) -> FastAPI -> sepa-terminal (React/TypeScript)
- The survivorship-free NSE bhavcopy panel is the single source of truth for OHLCV; yfinance is retained only as a legacy comparison/fallback panel
- Corporate-action adjustment is an active deterministic layer for the raw NSE panel; unresolved discontinuities remain quarantined for review
- Canonical strategy document: `docs/STRATEGY.md` — must be updated whenever strategy behavior changes
- UI documentation: `docs/UI_BIBLE.md` — should be updated when UI patterns change
- Python packages managed with `uv`; tests run via `uv run pytest tests/` (ignore `test_ui_*.py` files — dead Streamlit UI references)
- DuckDB requires `read_only=True` when accessed concurrently (e.g., API server + CLI)
- User's machine: macOS ARM, 8 cores, 16GB RAM
- NSE tickers use `.NS` suffix when calling yfinance (e.g., `HDFCBANK.NS`)
- Frontend uses MUI 7 with styled components, Node 22, TypeScript
- `turnover` column is computed as `close * volume` in download scripts since yfinance doesn't provide it natively

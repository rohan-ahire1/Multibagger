# Multibagger — NSE SEPA Scanner

A local, alert-only scanner for identifying Minervini/SEPA-inspired long swing-trading setups in NSE equities. It downloads adjusted OHLCV data, stores normalized history in Parquet, computes WATCH and BREAKOUT candidates with Polars, persists research data in DuckDB and exposes results through a local terminal-style web interface.

> Research and alerting software only. It does not place orders and is not investment advice.

## Highlights

- broad NSE symbol discovery from bhavcopy snapshots
- adjusted OHLCV downloads through yfinance
- local Parquet market-data store
- high-speed Polars indicator and signal calculation
- liquidity, trend, relative-strength and setup filters
- WATCH and one-time BREAKOUT signals
- DuckDB run history, signal persistence and candidate decisions
- optional Telegram alerts
- FastAPI-backed React/TypeScript research terminal
- persisted-signal backtesting with trade and drawdown inspection
- CLI health checks and scheduled-run support

## Architecture

```text
NSE bhavcopy snapshots ──► historical symbol universe
                                  │
yfinance adjusted OHLCV ─────────┘
              │
              ▼
   Parquet history store
              │
              ▼
 Polars indicator + SEPA scan
              │
              ├── signal snapshots
              ├── DuckDB history/watchlist
              ├── Telegram alerts
              └── local API + terminal UI
```

## Technology

- Python 3.12+
- Polars and NumPy
- Parquet and PyArrow
- DuckDB
- Typer and Rich
- Pydantic and YAML configuration
- yfinance
- Requests and Tenacity
- FastAPI and Uvicorn
- React/TypeScript frontend in `sepa-terminal/`
- pytest

## Repository structure

```text
Multibagger/
├── config/                  # Strategy, universe and alert configuration
├── data/                    # Local OHLCV, database and generated signals
├── docs/plans/              # Design and implementation plans
├── scripts/                 # Data utility wrappers and validation tools
├── sepa-terminal/           # React/TypeScript terminal UI
├── src/sepa_scanner/        # Python package and CLI
├── tests/
├── pyproject.toml
└── README.md
```

## Installation

```bash
git clone https://github.com/rohan-ahire1/Multibagger.git
cd Multibagger
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env
```

Edit `config/config.yaml` for strategy and storage settings.

Supported environment overrides include:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
POLARS_MAX_THREADS=
SEPA_DATA_DIR=
```

Telegram is optional. Without credentials, the scanner continues in console-only mode.

## CLI

```bash
./.venv/bin/sepa --help
```

### Build a historical symbol universe

```bash
./.venv/bin/sepa build-universe \
  --start 2005-01-01 \
  --end 2026-04-18 \
  --cadence monthly
```

The default output is `data/reference/nse_eq_universe.yaml`. Bhavcopy data is used for symbol discovery; runtime OHLCV continues to come from yfinance.

### Backfill adjusted OHLCV

```bash
./.venv/bin/sepa backfill --start 2025-01-01 --end 2025-03-31

./.venv/bin/sepa backfill \
  --start 2005-01-01 \
  --end 2026-04-18 \
  --symbols-from file \
  --symbols-file data/reference/nse_eq_universe.yaml
```

### Refresh recent data

```bash
./.venv/bin/sepa update
./.venv/bin/sepa update --days 7
./.venv/bin/sepa update --date 2026-03-06 --force
```

### Scan

```bash
./.venv/bin/sepa scan
./.venv/bin/sepa scan --date 2026-03-06 --export-csv
./.venv/bin/sepa scan --no-telegram
```

Signal snapshots are written to:

```text
data/signals/YYYY-MM-DD_signals.parquet
data/signals/YYYY-MM-DD_signals.csv   # with --export-csv
```

### Update and scan

```bash
./.venv/bin/sepa run
```

This refreshes recent data and scans the latest locally available adjusted trading date.

### Launch the research terminal

```bash
./.venv/bin/sepa ui
./.venv/bin/sepa ui --host 127.0.0.1 --port 8602
```

The terminal contains:

- **Scanner** — signal triage, chart review and persistent shortlist/reject decisions
- **Backtest** — persisted BREAKOUT evaluation, equity/drawdown charts and per-trade inspection

### Backtest persisted BREAKOUT signals

```bash
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --pending-days 5 --max-hold-days 20
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --target-r 2
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --symbol TCS --export-csv
```

Current assumptions:

- only persisted `BREAKOUT` signals are tradable
- trades are evaluated independently rather than as a sized portfolio
- later signals for an already-open symbol are skipped
- same-day stop/target conflicts resolve conservatively
- `minervini_phased` is the supported default exit engine
- legacy exit modes are comparison tools

### Health check

```bash
./.venv/bin/sepa health
```

Reports the latest run, latest stored date and current symbol count.

## Data flow and storage

1. Adjusted OHLCV is downloaded from yfinance.
2. Normalized history is stored in `data/yf_adjusted/ohlcv_all.parquet`.
3. Runs, signals and watchlist decisions are stored in `data/db/sepa.duckdb`.
4. Daily signal snapshots are written under `data/signals/`.
5. The local API and terminal read from DuckDB and Parquet.

Keep generated market data and databases out of version control unless intentionally adding small fixtures.

## Strategy behavior

- The effective universe is liquidity-filtered on each scan date.
- `NIFTYBEES` is used as the benchmark and excluded from trade candidates.
- WATCH requires current-day setup quality.
- BREAKOUT uses trend and relative strength on day `D`, with setup quality evaluated on day `D-1`.
- BREAKOUT alerts require the previous close below the breakout threshold to avoid repeated notifications.
- Runtime scanning uses yfinance-adjusted OHLC and split-adjusted volume.
- NSE bhavcopy is retained for universe discovery and optional validation, not the runtime price series.

Exact thresholds live in `config/config.yaml` and the scanner source. Treat configuration as part of the strategy definition and version it with every reported result.

## Telegram alerts

1. Create a bot with `@BotFather`.
2. Send the bot a message from the target chat.
3. Retrieve the chat ID through Telegram's `getUpdates` endpoint.
4. Add the bot token and chat ID to `.env`.
5. Run a scan and verify a test message before scheduling unattended runs.

Never commit the bot token.

## Helper scripts

- [`scripts/yf_bulk_download.py`](scripts/yf_bulk_download.py)
- [`scripts/yf_daily_update.py`](scripts/yf_daily_update.py)
- [`scripts/validate_bhavcopy_vs_yf.py`](scripts/validate_bhavcopy_vs_yf.py)

## Scheduling on macOS

A `launchd` job can run `sepa run` after the NSE session. Use absolute paths inside the local plist, but do not commit machine-specific paths to this repository.

Example command target:

```text
/path/to/Multibagger/.venv/bin/sepa run
```

Schedule after market close and ensure the machine is awake, connected and using the intended timezone.

## Testing

```bash
./.venv/bin/pytest -q
```

Before relying on a release, also run a small backfill, update, scan, health check, API launch and terminal smoke test.

## Research limitations

- yfinance is unofficial and may contain gaps or delayed adjustments.
- Historical symbol discovery does not guarantee perfect point-in-time membership.
- Backtests do not yet represent a capital-constrained portfolio.
- Persisted signals inherit the exact data and configuration available when the scan ran.
- Breakout fills, liquidity, slippage, taxes, circuit limits and partial executions need realistic modeling.
- Scanner rankings and exit rules can be overfit.
- Alert delivery does not prove that a signal was executable.

## Safety and intended use

The project is intentionally alert-only. Keep execution separate until the scanner has robust data validation, portfolio sizing, idempotent order handling, broker reconciliation and extensive paper-trading evidence.

## License

MIT, as declared in `pyproject.toml`.
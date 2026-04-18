# multibagger

`multibagger` is a local, alert-only NSE daily scanner for high-probability long swing setups. It ingests adjusted OHLCV from yfinance, stores normalized history locally in Parquet, computes Minervini/SEPA-inspired WATCH and BREAKOUT signals with Polars, persists runs, signals, and ranked watchlist candidates in DuckDB, and serves the data to a local terminal UI.

## Stack

- Python 3.12
- Polars for indicator and scan computation
- Parquet for OHLCV history
- DuckDB for run logs and signals
- Typer CLI
- FastAPI for the local API
- React/TypeScript terminal UI in `sepa-terminal/`
- Requests + Tenacity for NSE calendar and Telegram HTTP calls

## Project layout

```text
multibagger/
├─ config/
├─ data/
├─ docs/plans/
├─ scripts/
├─ src/sepa_scanner/
└─ tests/
```

## Installation

1. Create the environment:

```bash
uv venv --python 3.12 .venv
```

2. Install the package and dev dependencies:

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
```

3. Create your local environment file:

```bash
cp .env.example .env
```

4. Edit `config/config.yaml` if you want to override the defaults.

## Configuration

Primary settings live in [config/config.yaml](/Users/rohanahire/Documents/Personal_Projects/multibagger/config/config.yaml).

Environment overrides supported:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `POLARS_MAX_THREADS`
- `SEPA_DATA_DIR`

Optional files:

- [config/alert_channels.yaml](/Users/rohanahire/Documents/Personal_Projects/multibagger/config/alert_channels.yaml)
- [config/universe.yaml](/Users/rohanahire/Documents/Personal_Projects/multibagger/config/universe.yaml)

If Telegram credentials are missing, the scanner continues in console-only mode and logs that Telegram is disabled.

## Telegram setup

1. Open Telegram and talk to `@BotFather`.
2. Run `/newbot` and create the bot.
3. Copy the bot token into `.env` as `TELEGRAM_BOT_TOKEN`.
4. Send a message to the bot from the target chat.
5. Visit:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

6. Find the chat ID in the response and store it as `TELEGRAM_CHAT_ID`.

## CLI usage

Install the package, then use the `sepa` command from the project virtual environment:

```bash
./.venv/bin/sepa --help
```

### Build a broad historical universe

Builds a reusable NSE `EQ` symbol list from bhavcopy snapshots. This is for symbol
discovery only; OHLCV still comes from yfinance.

```bash
./.venv/bin/sepa build-universe --start 2005-01-01 --end 2026-04-18 --cadence monthly
```

The default output is `data/reference/nse_eq_universe.yaml`. Use `--cadence weekly`
for broader coverage, or `--cadence daily` if you have brought tea and patience.

### Backfill

Downloads adjusted yfinance OHLCV over a date range and upserts it into the local
parquet store. By default, historical backfills use every known symbol in the parquet
store, not just symbols present on the latest date.

```bash
./.venv/bin/sepa backfill --start 2025-01-01 --end 2025-03-31
./.venv/bin/sepa backfill --start 2005-01-01 --end 2026-04-18 --symbols-from file --symbols-file data/reference/nse_eq_universe.yaml
```

### Update

Refreshes the latest yfinance-adjusted bars for the configured full universe and then rewrites the affected local dates.

```bash
./.venv/bin/sepa update
./.venv/bin/sepa update --days 7
./.venv/bin/sepa update --date 2026-03-06 --force
```

### Scan

Loads the required lookback window from local Parquet, runs the full scanner, saves signals, updates DuckDB, and sends alerts.

```bash
./.venv/bin/sepa scan
./.venv/bin/sepa scan --date 2026-03-06 --export-csv
./.venv/bin/sepa scan --no-telegram
```

Signal snapshots are written to:

- `data/signals/YYYY-MM-DD_signals.parquet`
- `data/signals/YYYY-MM-DD_signals.csv` when `--export-csv` is used

### Run

Runs `update` followed by `scan` for the latest available local yfinance-adjusted date.

```bash
./.venv/bin/sepa run
```

### UI

Launches the local React terminal for a setup-first trading workspace.

```bash
./.venv/bin/sepa ui
./.venv/bin/sepa ui --host 127.0.0.1 --port 8602
```

The terminal is organized as:

- `Scanner` for full-universe daily signal triage, chart review, and persistent shortlist/reject decisions
- `Backtest` for full-range signal evaluation and per-trade review

The `Backtest` workspace simulates persisted `BREAKOUT` signals locally from DuckDB + Parquet with:

- the canonical `minervini_phased` exit engine by default
- full-history trade charts and full-data overlays
- equity and drawdown charts
- per-trade chart overlays for entry, stop, pivot, and exit

### Backtest

Runs the local trade simulator for persisted `BREAKOUT` signals over a date range.

```bash
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --pending-days 5 --max-hold-days 20
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --target-r 2
./.venv/bin/sepa backtest --start 2026-01-01 --end 2026-03-06 --symbol TCS --export-csv
```

Current V1 assumptions:

- only `BREAKOUT` signals are tradable
- trades are evaluated independently with no portfolio sizing
- a later signal for the same symbol is skipped while an earlier trade is still open
- same-day stop and target conflicts resolve conservatively to the worse outcome
- the supported default exit engine is `minervini_phased`; legacy exit modes are comparison-only

### Health

Prints the latest run status, latest OHLCV date, and the number of symbols currently stored for the latest date.

```bash
./.venv/bin/sepa health
```

## Helper scripts

The repository also includes thin wrappers:

- [scripts/yf_bulk_download.py](/Users/rohanahire/Documents/Personal_Projects/multibagger/scripts/yf_bulk_download.py)
- [scripts/yf_daily_update.py](/Users/rohanahire/Documents/Personal_Projects/multibagger/scripts/yf_daily_update.py)
- [scripts/validate_bhavcopy_vs_yf.py](/Users/rohanahire/Documents/Personal_Projects/multibagger/scripts/validate_bhavcopy_vs_yf.py) for optional raw-price validation

## Data flow

1. Adjusted OHLCV is downloaded from yfinance for the NSE universe.
2. Normalized OHLCV is stored in `data/yf_adjusted/ohlcv_all.parquet`.
3. Runs, signals, and watchlist candidates are stored in `data/db/sepa.duckdb`.
4. Signal snapshots are exported under `data/signals/`.

## Strategy notes

- Universe is liquidity-filtered from local yfinance history on each scan date.
- Broad historical symbol discovery can use NSE bhavcopy snapshots via `sepa build-universe`.
- Benchmark is `NIFTYBEES`.
- WATCH requires current-day setup quality.
- BREAKOUT requires trend and RS on day `D`, but setup quality on day `D-1`.
- BREAKOUT alerts are one-time only by requiring previous close below the breakout threshold.
- The scanner excludes `NIFTYBEES` from emitted trade candidates.

## Corporate actions

Runtime scanning/backtesting uses adjusted yfinance OHLC and split-adjusted volume. Bhavcopy is retained only as an optional validation source for same-day price checks.

## Scheduling with macOS launchd

Create a property list under `~/Library/LaunchAgents/` that runs after market close. Example program arguments:

```xml
<array>
  <string>/Users/rohanahire/Documents/Personal_Projects/multibagger/.venv/bin/sepa</string>
  <string>run</string>
</array>
```

Set `StartCalendarInterval` to a time after the NSE cash close, for example `16:10` IST converted to your machine timezone if needed, then load it with:

```bash
launchctl load ~/Library/LaunchAgents/com.sepa-scanner-nse.plist
```

## Testing

Run the full test suite with:

```bash
./.venv/bin/pytest -q
```

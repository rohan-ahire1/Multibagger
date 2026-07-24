# SEPA Scanner NSE Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a production-quality, local, alert-only NSE swing scanner that ingests official bhavcopy data, computes Minervini/SEPA-inspired daily signals, persists results locally, and emits console plus optional Telegram alerts.

**Architecture:** The project uses Polars as the primary analytics engine, Parquet as the historical OHLCV store, and DuckDB for run logs and emitted signals. The codebase is split into data ingestion, storage, indicators, strategy rules, alerts, and a Typer CLI so daily update and scan flows remain fast, modular, and testable.

**Tech Stack:** Python 3.12, Polars, DuckDB, Typer, Pydantic Settings, Requests, Tenacity, PyYAML, Rich, Pytest

---

### Task 1: Scaffold project structure and configuration loading

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `README.md`
- Create: `config/config.yaml`
- Create: `config/alert_channels.yaml`
- Create: `config/universe.yaml`
- Create: `src/sepa_scanner/__init__.py`
- Create: `src/sepa_scanner/settings.py`
- Create: `src/sepa_scanner/logging_setup.py`
- Create: `src/sepa_scanner/calendar.py`
- Create: `src/sepa_scanner/types.py`

**Step 1: Write the failing test**

Create `tests/test_settings.py` with a test that loads default config, applies environment overrides, and verifies Telegram settings are optional.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL because the package and config loader do not exist yet.

**Step 3: Write minimal implementation**

Add package metadata, settings models, YAML loading, env overrides, and basic logging helpers.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_settings.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml .env.example README.md config src tests/test_settings.py
git commit -m "feat: scaffold sepa scanner project"
```

### Task 2: Implement bhavcopy download, parse, and parquet upsert

**Files:**
- Create: `src/sepa_scanner/utils/http.py`
- Create: `src/sepa_scanner/utils/io.py`
- Create: `src/sepa_scanner/utils/validation.py`
- Create: `src/sepa_scanner/data_sources/nse_bhavcopy.py`
- Create: `src/sepa_scanner/data_sources/corporate_actions.py`
- Create: `src/sepa_scanner/storage/parquet_store.py`
- Test: `tests/fixtures/sample_bhavcopy.csv`
- Test: `tests/test_bhavcopy_parser.py`

**Step 1: Write the failing test**

Create parser tests for UDiFF column normalization, EQ filtering, bad-row dropping, and duplicate handling.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_bhavcopy_parser.py -v`
Expected: FAIL because downloader, parser, and store code do not exist yet.

**Step 3: Write minimal implementation**

Implement the NSE session bootstrap, merged-daily-reports discovery, direct archive URL fallback, zip caching, CSV parsing, normalized schema mapping, and Parquet partition upsert logic.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_bhavcopy_parser.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/utils src/sepa_scanner/data_sources src/sepa_scanner/storage tests
git commit -m "feat: add bhavcopy ingestion and parquet store"
```

### Task 3: Implement DuckDB persistence and health queries

**Files:**
- Create: `src/sepa_scanner/storage/duckdb_store.py`
- Create: `src/sepa_scanner/storage/schema.sql`
- Test: `tests/test_duckdb_store.py`

**Step 1: Write the failing test**

Create tests that initialize the database, insert run logs and signals, enforce uniqueness, and read health summaries.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_duckdb_store.py -v`
Expected: FAIL because the schema and persistence layer do not exist yet.

**Step 3: Write minimal implementation**

Implement schema initialization, run insertion, signal upsert, and health-query methods.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_duckdb_store.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/storage tests/test_duckdb_store.py
git commit -m "feat: add duckdb run and signal storage"
```

### Task 4: Implement indicator modules with exact formula tests

**Files:**
- Create: `src/sepa_scanner/indicators/sma.py`
- Create: `src/sepa_scanner/indicators/highs_lows.py`
- Create: `src/sepa_scanner/indicators/atr.py`
- Create: `src/sepa_scanner/indicators/volume.py`
- Create: `src/sepa_scanner/indicators/rs.py`
- Create: `src/sepa_scanner/indicators/tightness.py`
- Test: `tests/test_atr_wilder.py`
- Test: `tests/test_pivot_calc.py`
- Test: `tests/test_rs_rank.py`

**Step 1: Write the failing tests**

Add tests for exact ATR Wilder recurrence, pivot exclusion of the current bar, and RS score plus percentile ranking.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_atr_wilder.py tests/test_pivot_calc.py tests/test_rs_rank.py -v`
Expected: FAIL because the indicator modules do not exist yet.

**Step 3: Write minimal implementation**

Implement Polars expressions for rolling metrics and the NumPy-based per-symbol ATR routine.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_atr_wilder.py tests/test_pivot_calc.py tests/test_rs_rank.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/indicators tests/test_atr_wilder.py tests/test_pivot_calc.py tests/test_rs_rank.py
git commit -m "feat: add scanner indicators"
```

### Task 5: Implement trend template, setup, market filter, and ranking rules

**Files:**
- Create: `src/sepa_scanner/strategy/market_filter.py`
- Create: `src/sepa_scanner/strategy/trend_template.py`
- Create: `src/sepa_scanner/strategy/vcp_setup.py`
- Create: `src/sepa_scanner/strategy/ranking.py`
- Test: `tests/test_trend_template.py`
- Test: `tests/test_setup_gate.py`

**Step 1: Write the failing tests**

Create rule tests that toggle each trend-template condition independently and verify setup-gate behavior, including the `D-1` breakout setup requirement.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trend_template.py tests/test_setup_gate.py -v`
Expected: FAIL because the strategy modules do not exist yet.

**Step 3: Write minimal implementation**

Implement explicit boolean rule columns, setup gating, breakout helper predicates, and signal ranking expressions.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trend_template.py tests/test_setup_gate.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/strategy tests/test_trend_template.py tests/test_setup_gate.py
git commit -m "feat: add trend and setup rules"
```

### Task 6: Implement signal engine and end-to-end scan assembly

**Files:**
- Create: `src/sepa_scanner/strategy/signal_engine.py`
- Test: `tests/test_signal_engine.py`

**Step 1: Write the failing test**

Create an end-to-end unit test with synthetic symbol history that verifies universe filtering, benchmark exclusion, WATCH generation, BREAKOUT generation with `setup_pass(D-1)`, one-time breakout behavior, and invalid-risk suppression.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_signal_engine.py -v`
Expected: FAIL because the orchestration layer does not exist yet.

**Step 3: Write minimal implementation**

Implement the scan pipeline that loads history, computes features, applies gates, ranks signals, and returns serializable signal rows.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_signal_engine.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/strategy/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: add scan engine"
```

### Task 7: Implement alerts, CLI commands, and automation scripts

**Files:**
- Create: `src/sepa_scanner/alerts/base.py`
- Create: `src/sepa_scanner/alerts/console.py`
- Create: `src/sepa_scanner/alerts/telegram.py`
- Create: `src/sepa_scanner/alerts/formatters.py`
- Create: `src/sepa_scanner/main.py`
- Create: `scripts/backfill_bhavcopy.py`
- Create: `scripts/update_latest.py`
- Create: `scripts/run_daily.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Create CLI tests for `backfill`, `update`, `scan`, `run`, and `health`, including Telegram-disabled fallback behavior.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL because the CLI and alert transports do not exist yet.

**Step 3: Write minimal implementation**

Implement alert formatters, console transport, Telegram transport, Typer entrypoints, and thin script wrappers.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/alerts src/sepa_scanner/main.py scripts tests/test_cli.py
git commit -m "feat: add cli and alerts"
```

### Task 8: Finish documentation and perform full verification

**Files:**
- Modify: `README.md`
- Modify: `config/config.yaml`
- Modify: `.env.example`

**Step 1: Write the failing test**

If practical, add a smoke test that imports the CLI and validates `--help` output; otherwise use documentation review and manual verification as the acceptance gate.

**Step 2: Run verification to find gaps**

Run: `pytest -q`
Expected: Identify any remaining failures or missing coverage.

**Step 3: Write minimal implementation**

Complete README setup, configuration, Telegram instructions, macOS `launchd` scheduling notes, and any final config defaults needed to satisfy the acceptance criteria.

**Step 4: Run full verification**

Run: `pytest -q`
Expected: PASS with all tests green.

**Step 5: Commit**

```bash
git add README.md config .env.example tests
git commit -m "docs: finish scanner setup and usage"
```

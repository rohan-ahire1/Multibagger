# YFinance Runtime Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove bhavcopy from the active runtime path and make yfinance price and volume normalization internally consistent for scanner/backtest inputs.

**Architecture:** Introduce a shared yfinance normalization module that produces adjusted OHLC, split-adjusted volume, and raw-value turnover. Switch runtime update/backfill/scan dependencies to use this module and plain NSE HTTP sessions, then update docs to reflect the new source of truth.

**Tech Stack:** Python 3.12, Polars, pandas via yfinance, Typer, pytest

---

### Task 1: Add yfinance normalization regression tests

**Files:**
- Create: `tests/test_yfinance_adjusted.py`

**Step 1: Write failing tests**

- cover adjusted OHLC derivation from raw Yahoo columns
- cover split-adjusted historical volume and turnover behavior

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_yfinance_adjusted.py -q`

**Step 3: Implement minimal shared normalization module**

- create `src/sepa_scanner/data_sources/yfinance_adjusted.py`

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_yfinance_adjusted.py -q`

### Task 2: Switch runtime update flow to yfinance

**Files:**
- Modify: `src/sepa_scanner/main.py`
- Modify: `tests/test_cli.py`

**Step 1: Write failing test**

- add a focused test for `run_update_command()` using yfinance download helpers rather than bhavcopy client logic

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q`

**Step 3: Implement minimal runtime change**

- route `update` and `run` through the shared yfinance module
- resolve symbols from `config/universe.yaml` or the existing yfinance store

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q`

### Task 3: Remove scan-time bhavcopy coupling

**Files:**
- Modify: `src/sepa_scanner/main.py`
- Modify: `src/sepa_scanner/utils/http.py`

**Step 1: Add/adjust failing test coverage if needed**

- ensure scan-day holiday loading no longer requires bhavcopy client construction

**Step 2: Implement minimal change**

- use plain NSE HTTP session builder for holiday fetches

**Step 3: Re-run affected tests**

Run: `uv run pytest tests/test_cli.py tests/test_calendar_holidays.py -q`

### Task 4: Align scripts and docs

**Files:**
- Modify: `scripts/yf_bulk_download.py`
- Modify: `scripts/yf_daily_update.py`
- Modify: `README.md`
- Modify: `docs/STRATEGY.md`

**Step 1: Reuse shared yfinance normalization helper in scripts**

**Step 2: Update docs**

- describe yfinance-adjusted parquet as primary OHLCV source
- describe bhavcopy as validation-only

**Step 3: Re-run focused tests**

Run: `uv run pytest tests/test_settings.py tests/test_signal_engine.py -q`

### Task 5: Verification

**Files:**
- None

**Step 1: Run targeted verification**

Run: `uv run pytest tests/test_yfinance_adjusted.py tests/test_cli.py tests/test_calendar_holidays.py tests/test_signal_engine.py tests/test_settings.py -q`

**Step 2: Run broader regression**

Run: `uv run pytest tests/ -q -k 'not test_ui_'`

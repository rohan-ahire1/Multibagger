# Lorentzian Backtest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a separate Lorentzian workspace beside the current Backtest workspace, using a shared dashboard shell and an independent Lorentzian long-only backtest engine with a risk overlay.

**Architecture:** Refactor the current SEPA backtest workspace into reusable dashboard components, then add a second Lorentzian-specific frontend/store/API flow that reuses the shared shell while keeping state, params, cache files, and backend strategy logic isolated. The backend wraps `advanced-ta` behind an adapter, simulates trades from normalized buy/sell signals, and returns the same report shape used by the current dashboard.

**Tech Stack:** FastAPI, Polars, pandas, advanced-ta, pytest, React 19, TypeScript, Zustand, TanStack Query

---

### Task 1: Pin the Lorentzian dependency and add backend adapter tests

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_lorentzian_adapter.py`

**Step 1: Write the failing test**

```python
from datetime import date

import pandas as pd

from sepa_scanner.lorentzian.adapter import normalize_lorentzian_output


def test_normalize_lorentzian_output_maps_buy_sell_and_prediction() -> None:
    raw = pd.DataFrame(
        {
            "date": [date(2026, 3, 2), date(2026, 3, 3)],
            "isNewBuySignal": [False, True],
            "isNewSellSignal": [False, False],
            "prediction": [0, 6],
        }
    )

    out = normalize_lorentzian_output("ABC", raw)

    assert out.to_dicts() == [
        {
            "symbol": "ABC",
            "signal_date": date(2026, 3, 2),
            "buy_signal": False,
            "sell_signal": False,
            "prediction": 0,
        },
        {
            "symbol": "ABC",
            "signal_date": date(2026, 3, 3),
            "buy_signal": True,
            "sell_signal": False,
            "prediction": 6,
        },
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lorentzian_adapter.py::test_normalize_lorentzian_output_maps_buy_sell_and_prediction -v`
Expected: FAIL with import or missing function error for `sepa_scanner.lorentzian.adapter`

**Step 3: Write minimal implementation**

Add `advanced-ta==0.1.8` to dependencies and create `src/sepa_scanner/lorentzian/adapter.py` with a minimal `normalize_lorentzian_output()` implementation that converts pandas output to a Polars DataFrame with the stable internal columns.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lorentzian_adapter.py::test_normalize_lorentzian_output_maps_buy_sell_and_prediction -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml tests/test_lorentzian_adapter.py src/sepa_scanner/lorentzian/adapter.py src/sepa_scanner/lorentzian/__init__.py
git commit -m "feat: add lorentzian adapter scaffold"
```

### Task 2: Add failing trade-simulation tests for the Lorentzian engine

**Files:**
- Create: `tests/test_lorentzian_backtest.py`

**Step 1: Write the failing test**

```python
from datetime import date

import polars as pl

from sepa_scanner.lorentzian.backtest import simulate_lorentzian_backtest


def test_lorentzian_enters_on_next_open_and_exits_on_sell_signal() -> None:
    signals = pl.DataFrame(
        {
            "symbol": ["ABC", "ABC"],
            "signal_date": [date(2026, 3, 2), date(2026, 3, 5)],
            "buy_signal": [True, False],
            "sell_signal": [False, True],
            "prediction": [6, -5],
            "signal_low": [98.0, 103.0],
        }
    )
    prices = pl.DataFrame(
        {
            "symbol": ["ABC", "ABC", "ABC", "ABC", "ABC"],
            "date": [
                date(2026, 3, 3),
                date(2026, 3, 4),
                date(2026, 3, 5),
                date(2026, 3, 6),
                date(2026, 3, 9),
            ],
            "open": [101.0, 102.0, 103.0, 104.0, 105.0],
            "high": [102.0, 103.0, 104.0, 105.0, 106.0],
            "low": [100.0, 101.0, 102.0, 103.0, 104.0],
            "close": [101.5, 102.5, 103.5, 104.5, 105.5],
        }
    )

    report = simulate_lorentzian_backtest(signals, prices, max_hold_days=20)

    trade = report.trades[0]
    assert trade.entry_date == date(2026, 3, 3)
    assert trade.entry_price == 101.0
    assert trade.exit_date == date(2026, 3, 6)
    assert trade.exit_price == 104.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lorentzian_backtest.py::test_lorentzian_enters_on_next_open_and_exits_on_sell_signal -v`
Expected: FAIL because `simulate_lorentzian_backtest` does not exist yet

**Step 3: Write minimal implementation**

Create `src/sepa_scanner/lorentzian/backtest.py` with the minimal simulation needed to:
- enter on next-day open after a buy signal
- use signal-day low as stop
- exit on next-day open after a sell signal

Return a result shape compatible with the existing backtest models.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lorentzian_backtest.py::test_lorentzian_enters_on_next_open_and_exits_on_sell_signal -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_lorentzian_backtest.py src/sepa_scanner/lorentzian/backtest.py
git commit -m "feat: add basic lorentzian backtest simulation"
```

### Task 3: Expand Lorentzian engine coverage for stop, time stop, and incomplete data

**Files:**
- Modify: `tests/test_lorentzian_backtest.py`
- Modify: `src/sepa_scanner/lorentzian/backtest.py`

**Step 1: Write the failing test**

Add tests covering:

```python
def test_lorentzian_stops_out_when_low_breaks_stop() -> None:
    ...


def test_lorentzian_exits_at_time_stop_when_no_sell_signal() -> None:
    ...


def test_lorentzian_marks_incomplete_data_when_entry_day_missing() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lorentzian_backtest.py -v`
Expected: FAIL on the new scenarios

**Step 3: Write minimal implementation**

Extend `simulate_lorentzian_backtest()` to:
- exit at stop when the low breaches stop
- exit at the max-hold close when neither stop nor sell-signal exit occurs first
- mark signals as `incomplete_data` when required future bars are unavailable

Populate `holding_days`, `return_pct`, and `r_multiple`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lorentzian_backtest.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_lorentzian_backtest.py src/sepa_scanner/lorentzian/backtest.py
git commit -m "feat: complete lorentzian exit handling"
```

### Task 4: Add Lorentzian signal-generation service over the full parquet universe

**Files:**
- Create: `src/sepa_scanner/lorentzian/signals.py`
- Modify: `tests/test_lorentzian_adapter.py`

**Step 1: Write the failing test**

Add a service-level test that stubs the adapter and verifies the signal generator:
- loads full history from `2010-01-01`
- processes each symbol independently
- returns only normalized rows for requested symbols and dates

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lorentzian_adapter.py -v`
Expected: FAIL because the signal-generation service does not exist yet

**Step 3: Write minimal implementation**

Create `signals.py` with a function that:
- reads OHLCV from parquet across the full symbol universe
- groups by symbol
- converts each symbol to pandas
- runs the adapter
- concatenates normalized signals into one Polars DataFrame

Do not add any default single-symbol UI flow.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lorentzian_adapter.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_lorentzian_adapter.py src/sepa_scanner/lorentzian/signals.py
git commit -m "feat: generate lorentzian signals from parquet history"
```

### Task 5: Add FastAPI Lorentzian routes and cached-report tests

**Files:**
- Create: `src/sepa_scanner/api/routes/lorentzian_backtest.py`
- Modify: `src/sepa_scanner/api/app.py`
- Create: `tests/test_api_lorentzian_backtest.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from sepa_scanner.api.app import create_app


def test_lorentzian_backtest_last_returns_404_when_cache_missing(tmp_path) -> None:
    app = create_app(...)
    client = TestClient(app)

    res = client.get("/api/lorentzian-backtest/last")

    assert res.status_code == 404
```

Add a second test for `POST /api/lorentzian-backtest/run` that mocks the signal and simulation service and verifies the cached JSON response shape.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_lorentzian_backtest.py -v`
Expected: FAIL because the route is not mounted

**Step 3: Write minimal implementation**

Add the new router, request model, response serialization, and cache path `data/backtest/lorentzian_latest.json`. Mount the router in `api/app.py`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_lorentzian_backtest.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_api_lorentzian_backtest.py src/sepa_scanner/api/routes/lorentzian_backtest.py src/sepa_scanner/api/app.py
git commit -m "feat: expose lorentzian backtest api"
```

### Task 6: Extract a shared backtest workspace shell in the frontend

**Files:**
- Create: `sepa-terminal/src/components/backtest/BacktestWorkspaceShell.tsx`
- Modify: `sepa-terminal/src/components/workspaces/Backtest.tsx`

**Step 1: Write the failing test**

Add a frontend test that renders the shared shell through the existing `Backtest` workspace and verifies:
- empty state rendering when no result exists
- selected trade chart rendering when data exists

Use the repo's existing frontend test setup if present; otherwise create the smallest test harness needed for these components.

**Step 2: Run test to verify it fails**

Run: `cd sepa-terminal && npm test -- Backtest`
Expected: FAIL because the shared shell component does not exist yet

**Step 3: Write minimal implementation**

Extract the common layout, selection, and chart/detail rendering logic from `Backtest.tsx` into `BacktestWorkspaceShell.tsx`. Keep existing SEPA behavior unchanged.

**Step 4: Run test to verify it passes**

Run: `cd sepa-terminal && npm test -- Backtest`
Expected: PASS

**Step 5: Commit**

```bash
git add sepa-terminal/src/components/backtest/BacktestWorkspaceShell.tsx sepa-terminal/src/components/workspaces/Backtest.tsx
git commit -m "refactor: share backtest workspace shell"
```

### Task 7: Add Lorentzian frontend state, API hooks, and workspace navigation

**Files:**
- Create: `sepa-terminal/src/api/useLorentzianBacktest.ts`
- Create: `sepa-terminal/src/stores/lorentzianBacktest.ts`
- Create: `sepa-terminal/src/components/lorentzian/LorentzianParamBar.tsx`
- Create: `sepa-terminal/src/components/workspaces/LorentzianBacktest.tsx`
- Modify: `sepa-terminal/src/lib/types.ts`
- Modify: `sepa-terminal/src/App.tsx`
- Modify: `sepa-terminal/src/components/layout/TopBar.tsx`
- Modify: `sepa-terminal/src/hooks/useWorkspaceSwitch.ts`
- Modify: `sepa-terminal/src/components/shared/CommandPalette.tsx`
- Modify: `sepa-terminal/src/stores/ui.ts`

**Step 1: Write the failing test**

Add frontend tests that verify:
- workspace `5` switches to `Lorentzian`
- Lorentzian cached result loads into the workspace
- clicking Run calls the Lorentzian mutation hook

**Step 2: Run test to verify it fails**

Run: `cd sepa-terminal && npm test -- Lorentzian`
Expected: FAIL because the workspace and store do not exist yet

**Step 3: Write minimal implementation**

Implement:
- Lorentzian param and report types
- Lorentzian Zustand store
- Lorentzian query/mutation hooks
- Lorentzian param bar
- Lorentzian workspace using the shared shell
- top-level navigation and hotkey updates

Keep SEPA and Lorentzian stores, query keys, and cached results separate.

**Step 4: Run test to verify it passes**

Run: `cd sepa-terminal && npm test -- Lorentzian`
Expected: PASS

**Step 5: Commit**

```bash
git add sepa-terminal/src/api/useLorentzianBacktest.ts sepa-terminal/src/stores/lorentzianBacktest.ts sepa-terminal/src/components/lorentzian/LorentzianParamBar.tsx sepa-terminal/src/components/workspaces/LorentzianBacktest.tsx sepa-terminal/src/lib/types.ts sepa-terminal/src/App.tsx sepa-terminal/src/components/layout/TopBar.tsx sepa-terminal/src/hooks/useWorkspaceSwitch.ts sepa-terminal/src/components/shared/CommandPalette.tsx sepa-terminal/src/stores/ui.ts
git commit -m "feat: add lorentzian workspace"
```

### Task 8: Update docs for strategy and UI behavior

**Files:**
- Modify: `docs/STRATEGY.md`
- Modify: `docs/UI_BIBLE.md`

**Step 1: Write the failing test**

Write a review checklist documenting the missing required docs updates:
- strategy reference lacks Lorentzian workspace rules
- UI bible lacks the fifth workspace and shared-shell behavior

**Step 2: Run test to verify it fails**

Run: `rg -n "Lorentzian|workspace 5|shared shell" docs/STRATEGY.md docs/UI_BIBLE.md`
Expected: missing references for the new behavior

**Step 3: Write minimal implementation**

Update:
- `docs/STRATEGY.md` with a Lorentzian strategy section covering long-only behavior, entry, stop, and exit rules
- `docs/UI_BIBLE.md` with the fifth workspace and Lorentzian dashboard flow

**Step 4: Run test to verify it passes**

Run: `rg -n "Lorentzian|workspace 5|shared shell" docs/STRATEGY.md docs/UI_BIBLE.md`
Expected: matching lines returned from both docs

**Step 5: Commit**

```bash
git add docs/STRATEGY.md docs/UI_BIBLE.md
git commit -m "docs: describe lorentzian strategy and workspace"
```

### Task 9: Full verification and regression pass

**Files:**
- Modify: any files required to fix verification regressions

**Step 1: Write the failing test**

Run the full verification suite before making additional fixes.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ -k "not test_ui_" && cd sepa-terminal && npm run lint && npm run build`
Expected: identify any failing backend or frontend regressions

**Step 3: Write minimal implementation**

Fix only the issues surfaced by verification. Do not broaden scope.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ -k "not test_ui_" && cd sepa-terminal && npm run lint && npm run build`
Expected: all commands pass cleanly

**Step 5: Commit**

```bash
git add -A
git commit -m "test: verify lorentzian workspace end to end"
```

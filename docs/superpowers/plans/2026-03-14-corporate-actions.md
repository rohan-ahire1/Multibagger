# Corporate Actions Handling Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Handle stock splits and bonuses so that all rolling indicators use backward-adjusted prices, and the backtester skips trades affected by mid-trade splits.

**Architecture:** Two-part solution: (1) A `corporate_actions` DuckDB table + adjustment factor computation that produces adjusted OHLCV for indicator calculations, preserving raw prices for display. (2) A backtest-engine guard that marks trades overlapping a split as `skipped_split` and excludes them from metrics.

**Tech Stack:** Python 3.12, Polars, DuckDB, Pydantic, pytest, FastAPI, React/TypeScript (sepa-terminal)

**Spec:** `docs/superpowers/specs/2026-03-14-corporate-actions-design.md` (the user-provided spec pasted below the plan for reference)

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/sepa_scanner/data_sources/corporate_actions.py` | Replace stub. Load/query corporate actions from DuckDB, compute adjustment factors, build adjusted OHLCV. |
| `tests/test_corporate_actions.py` | Unit tests for adjustment factor computation, adjusted OHLCV building, and `has_split_in_window`. |
| `tests/test_backtest_skip_split.py` | Integration tests for `skipped_split` status in backtest engine. |
| `config/corporate_actions.csv` | Seed CSV with known large-cap splits/bonuses (IRCTC, BAJFINANCE, DMART, RELIANCE, TCS, HDFCBANK). |

### Modified files
| File | Changes |
|------|---------|
| `src/sepa_scanner/storage/schema.sql` | Add `corporate_actions` table DDL. |
| `src/sepa_scanner/storage/duckdb_store.py` | Add `upsert_corporate_actions()`, `fetch_corporate_actions()`, `fetch_corporate_actions_for_symbols()` methods. |
| `src/sepa_scanner/strategy/signal_engine.py` | Accept optional `corp_actions` in `_prepare_frame`, `scan_for_date`, `scan_batch`; call `build_adjusted_ohlcv` before indicator computation. |
| `src/sepa_scanner/backtest/engine.py` | Accept optional `corp_actions` DataFrame; skip signals/trades overlapping splits with `skipped_split` status. |
| `src/sepa_scanner/backtest/metrics.py` | Exclude `skipped_split` from closed-trade metrics; add `skipped_split_count` to summary. |
| `src/sepa_scanner/backtest/queries.py` | Add `get_corporate_actions()` query helper. |
| `src/sepa_scanner/api/routes/backtest.py` | Load corporate actions and pass to engine; include `skipped_split_count` in response. |
| `sepa-terminal/src/lib/types.ts` | Add `'skipped_split'` to `BacktestTrade.status` union; add `skippedSplitCount` to `BacktestSummary`. |
| `sepa-terminal/src/components/backtest/TradeList.tsx` | Add `skipped_split` to `statusPillProps`. |
| `sepa-terminal/src/components/backtest/StatsSummary.tsx` | Add "Skipped (split)" metric. |

---

## Chunk 1: Corporate Actions Data Layer

### Task 1: Add `corporate_actions` table to DuckDB schema

**Files:**
- Modify: `src/sepa_scanner/storage/schema.sql:126` (append at end)
- Modify: `src/sepa_scanner/storage/duckdb_store.py` (add methods)
- Test: `tests/test_duckdb_store.py` (add corporate actions tests)

- [ ] **Step 1: Write the failing test for upsert and fetch**

Add to `tests/test_duckdb_store.py`:

```python
from datetime import date

def test_upsert_and_fetch_corporate_actions(tmp_path):
    from sepa_scanner.storage.duckdb_store import DuckDbStore
    import polars as pl

    store = DuckDbStore(tmp_path / "test.duckdb")
    store.initialize()

    actions = pl.DataFrame({
        "symbol": ["IRCTC", "TCS"],
        "isin": [None, None],
        "action_type": ["split", "bonus"],
        "ex_date": [date(2022, 10, 28), date(2018, 7, 18)],
        "ratio_num": [None, 1],
        "ratio_den": [None, 1],
        "old_face_value": [10.0, None],
        "new_face_value": [2.0, None],
        "adjustment_factor": [0.2, 0.5],
        "description": ["5:1 split", "1:1 bonus"],
        "source": ["seed", "seed"],
    })
    store.upsert_corporate_actions(actions)
    result = store.fetch_corporate_actions()
    assert result.height == 2
    assert set(result["symbol"].to_list()) == {"IRCTC", "TCS"}

    # Fetch for specific symbols
    filtered = store.fetch_corporate_actions_for_symbols(["IRCTC"])
    assert filtered.height == 1
    assert filtered["symbol"].to_list() == ["IRCTC"]

    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_duckdb_store.py::test_upsert_and_fetch_corporate_actions -v`
Expected: FAIL with `AttributeError: 'DuckDbStore' object has no attribute 'upsert_corporate_actions'`

- [ ] **Step 3: Add schema DDL**

Append to `src/sepa_scanner/storage/schema.sql` at end:

```sql
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol TEXT NOT NULL,
    isin TEXT,
    action_type TEXT NOT NULL,
    ex_date DATE NOT NULL,
    ratio_num INTEGER,
    ratio_den INTEGER,
    old_face_value DOUBLE,
    new_face_value DOUBLE,
    adjustment_factor DOUBLE NOT NULL,
    description TEXT,
    source TEXT,
    UNIQUE(symbol, ex_date, action_type)
);
```

- [ ] **Step 4: Add DuckDbStore methods**

Add to `src/sepa_scanner/storage/duckdb_store.py`:

```python
def upsert_corporate_actions(self, df: pl.DataFrame) -> None:
    if df.is_empty():
        return
    self.initialize()
    conn = self._connect()
    conn.execute("INSERT OR REPLACE INTO corporate_actions SELECT * FROM df")

def fetch_corporate_actions(self) -> pl.DataFrame:
    self.initialize()
    return self._connect().execute(
        "SELECT * FROM corporate_actions ORDER BY symbol, ex_date"
    ).pl()

def fetch_corporate_actions_for_symbols(self, symbols: list[str]) -> pl.DataFrame:
    self.initialize()
    if not symbols:
        return pl.DataFrame(schema={
            "symbol": pl.String, "isin": pl.String,
            "action_type": pl.String, "ex_date": pl.Date,
            "ratio_num": pl.Int32, "ratio_den": pl.Int32,
            "old_face_value": pl.Float64, "new_face_value": pl.Float64,
            "adjustment_factor": pl.Float64,
            "description": pl.String, "source": pl.String,
        })
    return self._connect().execute(
        "SELECT * FROM corporate_actions WHERE symbol IN (SELECT UNNEST(?)) ORDER BY symbol, ex_date",
        [symbols],
    ).pl()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_duckdb_store.py::test_upsert_and_fetch_corporate_actions -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/storage/schema.sql src/sepa_scanner/storage/duckdb_store.py tests/test_duckdb_store.py
git commit -m "feat: add corporate_actions table and DuckDbStore CRUD methods"
```

---

### Task 2: Create seed CSV and CSV loader

**Files:**
- Create: `config/corporate_actions.csv`
- Modify: `src/sepa_scanner/data_sources/corporate_actions.py` (replace stub)
- Test: `tests/test_corporate_actions.py`

- [ ] **Step 1: Create seed CSV**

Create `config/corporate_actions.csv`:

```csv
symbol,isin,action_type,ex_date,ratio_num,ratio_den,old_face_value,new_face_value,adjustment_factor,description,source
IRCTC,,split,2022-10-28,,,10,2,0.2,5:1 split FV 10->2,seed
BAJFINANCE,,split,2022-09-15,,,2,1,0.5,2:1 split FV 2->1,seed
DMART,,split,2023-08-04,,,10,1,0.1,10:1 split FV 10->1,seed
RELIANCE,,bonus,2017-09-07,1,1,,,0.5,1:1 bonus,seed
TCS,,bonus,2018-07-18,1,1,,,0.5,1:1 bonus,seed
HDFCBANK,,split,2019-09-19,,,2,1,0.5,2:1 split FV 2->1,seed
```

- [ ] **Step 2: Write the failing test for CSV loading**

Create `tests/test_corporate_actions.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest


def test_load_corporate_actions_csv(tmp_path: Path) -> None:
    from sepa_scanner.data_sources.corporate_actions import load_corporate_actions_csv

    csv_content = (
        "symbol,isin,action_type,ex_date,ratio_num,ratio_den,"
        "old_face_value,new_face_value,adjustment_factor,description,source\n"
        "IRCTC,,split,2022-10-28,,,10,2,0.2,5:1 split FV 10->2,seed\n"
        "TCS,,bonus,2018-07-18,1,1,,,0.5,1:1 bonus,seed\n"
    )
    csv_path = tmp_path / "corp_actions.csv"
    csv_path.write_text(csv_content)

    df = load_corporate_actions_csv(csv_path)
    assert df.height == 2
    assert df["symbol"].to_list() == ["IRCTC", "TCS"]
    assert df["adjustment_factor"].to_list() == [0.2, 0.5]
    assert df["ex_date"].to_list() == [date(2022, 10, 28), date(2018, 7, 18)]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_corporate_actions.py::test_load_corporate_actions_csv -v`
Expected: FAIL with `ImportError` or `cannot import name 'load_corporate_actions_csv'`

- [ ] **Step 4: Implement CSV loader**

Replace `src/sepa_scanner/data_sources/corporate_actions.py` with:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl


def load_corporate_actions_csv(path: Path) -> pl.DataFrame:
    """Load corporate actions from a CSV file."""
    return pl.read_csv(
        path,
        schema_overrides={
            "ex_date": pl.Date,
            "ratio_num": pl.Int32,
            "ratio_den": pl.Int32,
            "old_face_value": pl.Float64,
            "new_face_value": pl.Float64,
            "adjustment_factor": pl.Float64,
        },
        null_values=[""],
    ).sort(["symbol", "ex_date"])


def has_split_in_window(
    symbol: str,
    start_date: date,
    end_date: date,
    corp_actions: pl.DataFrame,
) -> bool:
    """Check if any split/bonus ex_date falls strictly between start_date (exclusive)
    and end_date (inclusive)."""
    if corp_actions.is_empty():
        return False
    return (
        corp_actions.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("action_type").is_in(["split", "bonus"]))
            & (pl.col("ex_date") > pl.lit(start_date))
            & (pl.col("ex_date") <= pl.lit(end_date))
        ).height
        > 0
    )


def compute_adjustment_factors(
    ohlcv: pl.LazyFrame,
    corp_actions: pl.DataFrame,
) -> pl.LazyFrame:
    """Compute per-(symbol, date) cumulative adjustment factor.

    For each date, adj_factor = product of all split/bonus factors
    with ex_date AFTER that date.
    On and after the most recent ex_date, adj_factor = 1.0.
    """
    if corp_actions.is_empty():
        return ohlcv.select("symbol", "date").with_columns(
            pl.lit(1.0).alias("adj_factor")
        )

    dates_df = ohlcv.select("symbol", "date").collect()
    actions = corp_actions.select("symbol", "ex_date", "adjustment_factor")

    # Cross join each symbol's dates with its corporate actions
    joined = dates_df.join(actions, on="symbol", how="left", suffix="_ca")

    # For each (symbol, date), multiply factors where ex_date > date
    result = (
        joined.with_columns(
            pl.when(pl.col("ex_date").is_not_null() & (pl.col("ex_date") > pl.col("date")))
            .then(pl.col("adjustment_factor"))
            .otherwise(1.0)
            .alias("factor_contrib")
        )
        .group_by(["symbol", "date"])
        .agg(pl.col("factor_contrib").product().alias("adj_factor"))
        .sort(["symbol", "date"])
    )

    return result.lazy()


def build_adjusted_ohlcv(
    raw_ohlcv: pl.LazyFrame,
    adj_factors: pl.LazyFrame,
) -> pl.LazyFrame:
    """Apply adjustment factors to raw OHLCV.
    Preserves raw_close and raw_volume for display."""
    return (
        raw_ohlcv.join(adj_factors, on=["symbol", "date"], how="left")
        .with_columns(pl.col("adj_factor").fill_null(1.0))
        .with_columns(
            pl.col("close").alias("raw_close"),
            pl.col("volume").alias("raw_volume"),
            (pl.col("open") * pl.col("adj_factor")).alias("open"),
            (pl.col("high") * pl.col("adj_factor")).alias("high"),
            (pl.col("low") * pl.col("adj_factor")).alias("low"),
            (pl.col("close") * pl.col("adj_factor")).alias("close"),
            (pl.col("volume") / pl.col("adj_factor")).cast(pl.Int64).alias("volume"),
        )
        .drop("adj_factor")
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_corporate_actions.py::test_load_corporate_actions_csv -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add config/corporate_actions.csv src/sepa_scanner/data_sources/corporate_actions.py tests/test_corporate_actions.py
git commit -m "feat: corporate actions CSV loader and core functions"
```

---

### Task 3: Test adjustment factor computation

**Files:**
- Test: `tests/test_corporate_actions.py` (add tests)

- [ ] **Step 1: Write the failing test for adjustment factors**

Add to `tests/test_corporate_actions.py`:

```python
def test_compute_adjustment_factors_single_split() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 4,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28), date(2022, 10, 31)],
        "close": [4500.0, 4450.0, 900.0, 910.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    # Before ex_date: factor = 0.2
    assert factors[date(2022, 10, 26)] == pytest.approx(0.2)
    assert factors[date(2022, 10, 27)] == pytest.approx(0.2)
    # On and after ex_date: factor = 1.0
    assert factors[date(2022, 10, 28)] == pytest.approx(1.0)
    assert factors[date(2022, 10, 31)] == pytest.approx(1.0)


def test_compute_adjustment_factors_multiple_actions_stack() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    # Two actions for same symbol: factor stacks multiplicatively
    ohlcv = pl.DataFrame({
        "symbol": ["XYZ"] * 3,
        "date": [date(2020, 1, 1), date(2021, 6, 1), date(2023, 1, 1)],
        "close": [100.0, 50.0, 10.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["XYZ", "XYZ"],
        "ex_date": [date(2021, 1, 1), date(2022, 1, 1)],
        "adjustment_factor": [0.5, 0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    # Before both: 0.5 * 0.2 = 0.1
    assert factors[date(2020, 1, 1)] == pytest.approx(0.1)
    # After first, before second: 0.2
    assert factors[date(2021, 6, 1)] == pytest.approx(0.2)
    # After both: 1.0
    assert factors[date(2023, 1, 1)] == pytest.approx(1.0)


def test_adjustment_factor_irctc_real_world() -> None:
    """Verify IRCTC 5:1 split: pre-split price 4500 adjusts to 900."""
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["IRCTC"] * 3,
        "date": [date(2022, 10, 27), date(2022, 10, 28), date(2022, 10, 31)],
        "close": [4500.0, 900.0, 910.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["IRCTC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    # Pre-split: 4500 * 0.2 = 900, matching post-split price
    assert factors[date(2022, 10, 27)] == pytest.approx(0.2)
    assert 4500.0 * factors[date(2022, 10, 27)] == pytest.approx(900.0)
    # Post-split: factor = 1.0, no adjustment
    assert factors[date(2022, 10, 28)] == pytest.approx(1.0)


def test_compute_adjustment_factors_no_actions() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 2,
        "date": [date(2022, 1, 1), date(2022, 1, 2)],
        "close": [100.0, 101.0],
    }).lazy()

    corp_actions = pl.DataFrame(schema={
        "symbol": pl.String,
        "ex_date": pl.Date,
        "adjustment_factor": pl.Float64,
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    assert all(f == pytest.approx(1.0) for f in result["adj_factor"].to_list())
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_corporate_actions.py -v -k "adjustment_factor"`
Expected: PASS (all three)

- [ ] **Step 3: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add tests/test_corporate_actions.py
git commit -m "test: adjustment factor computation (single, stacked, empty)"
```

---

### Task 4: Test adjusted OHLCV building and has_split_in_window

**Files:**
- Test: `tests/test_corporate_actions.py` (add tests)

- [ ] **Step 1: Write the tests**

Add to `tests/test_corporate_actions.py`:

```python
def test_build_adjusted_ohlcv_applies_factors() -> None:
    from sepa_scanner.data_sources.corporate_actions import (
        build_adjusted_ohlcv,
        compute_adjustment_factors,
    )

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 3,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28)],
        "open": [4400.0, 4450.0, 900.0],
        "high": [4550.0, 4500.0, 920.0],
        "low": [4350.0, 4400.0, 880.0],
        "close": [4500.0, 4450.0, 910.0],
        "volume": [1000, 1200, 5500],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    adj_factors = compute_adjustment_factors(ohlcv, corp_actions)
    result = build_adjusted_ohlcv(ohlcv, adj_factors).collect()

    # Pre-split row should be adjusted
    pre_split = result.filter(pl.col("date") == date(2022, 10, 26))
    assert pre_split["close"].item() == pytest.approx(4500.0 * 0.2)
    assert pre_split["raw_close"].item() == pytest.approx(4500.0)
    assert pre_split["volume"].item() == int(1000 / 0.2)

    # Post-split row should be untouched
    post_split = result.filter(pl.col("date") == date(2022, 10, 28))
    assert post_split["close"].item() == pytest.approx(910.0)
    assert post_split["raw_close"].item() == pytest.approx(910.0)
    assert post_split["volume"].item() == 5500


def test_signal_day_adjusted_equals_raw() -> None:
    """Invariant: on the signal date (most recent date), adjusted close == raw close.
    This ensures signal-day prices (pivot, entry, stop) remain in raw terms."""
    from sepa_scanner.data_sources.corporate_actions import (
        build_adjusted_ohlcv,
        compute_adjustment_factors,
    )

    # Signal date is 2022-10-28 (on or after the last ex_date)
    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 3,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28)],
        "open": [4400.0, 4450.0, 900.0],
        "high": [4550.0, 4500.0, 920.0],
        "low": [4350.0, 4400.0, 880.0],
        "close": [4500.0, 4450.0, 910.0],
        "volume": [1000, 1200, 5500],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    adj_factors = compute_adjustment_factors(ohlcv, corp_actions)
    result = build_adjusted_ohlcv(ohlcv, adj_factors).collect()

    # On the signal date (ex_date or later), adjusted == raw
    signal_day = result.filter(pl.col("date") == date(2022, 10, 28))
    assert signal_day["close"].item() == signal_day["raw_close"].item()
    assert signal_day["volume"].item() == signal_day["raw_volume"].item()


def test_has_split_in_window_true() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    assert has_split_in_window("ABC", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is True


def test_has_split_in_window_false_wrong_symbol() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    assert has_split_in_window("XYZ", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is False


def test_has_split_in_window_false_outside_range() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    # Window ends before ex_date
    assert has_split_in_window("ABC", date(2022, 10, 20), date(2022, 10, 25), corp_actions) is False
    # Window starts on ex_date (start is exclusive)
    assert has_split_in_window("ABC", date(2022, 10, 28), date(2022, 11, 5), corp_actions) is False


def test_has_split_in_window_empty_actions() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame(schema={
        "symbol": pl.String,
        "action_type": pl.String,
        "ex_date": pl.Date,
        "adjustment_factor": pl.Float64,
    })

    assert has_split_in_window("ABC", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is False
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_corporate_actions.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add tests/test_corporate_actions.py
git commit -m "test: adjusted OHLCV building and has_split_in_window"
```

---

## Chunk 2: Scanner Pipeline Integration

### Task 5: Integrate adjusted OHLCV into the scanner pipeline

This is the core requirement: all rolling indicators must use backward-adjusted prices.

**Files:**
- Modify: `src/sepa_scanner/strategy/signal_engine.py`
- Test: `tests/test_signal_engine.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_signal_engine.py`:

```python
def test_prepare_frame_uses_adjusted_prices_when_corp_actions_provided() -> None:
    """When corp_actions is passed, _prepare_frame should adjust prices before computing indicators.
    Verify that SMA values are computed on adjusted (not raw) prices."""
    from sepa_scanner.strategy.signal_engine import _prepare_frame

    # Build a simple 2-symbol frame with a split for ABC on day 201
    # ABC has a 2:1 split, so pre-split prices are 2x
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(320)]
    abc_close = [200.0] * 200 + [100.0] * 120  # raw: 200 pre-split, 100 post-split
    abc_rows = []
    for i, d in enumerate(dates):
        c = abc_close[i]
        abc_rows.append({
            "date": d, "symbol": "ABC", "series": "EQ",
            "open": c, "high": c * 1.01, "low": c * 0.99,
            "close": c, "volume": 100000, "turnover": c * 100000,
        })

    frame = pl.DataFrame(abc_rows)
    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [dates[200]],
        "adjustment_factor": [0.5],
    })

    # Need a minimal settings object
    settings = _minimal_settings()  # helper defined in test file

    prepared_adj = _prepare_frame(frame, settings, corp_actions=corp_actions)
    prepared_raw = _prepare_frame(frame, settings)

    # With adjustment: pre-split prices are halved, so SMA200 on the last day
    # should be ~100 (all adjusted to 100). Without adjustment: SMA200 would
    # include raw 200 values and be higher.
    last_row_adj = prepared_adj.filter(pl.col("date") == dates[-1]).select("sma200").item()
    last_row_raw = prepared_raw.filter(pl.col("date") == dates[-1]).select("sma200").item()

    # Adjusted SMA200 should be approximately 100 (all values are ~100 after adjustment)
    assert last_row_adj == pytest.approx(100.0, rel=0.01)
    # Raw SMA200 should be higher than 100 (includes raw 200 values)
    assert last_row_raw > 120.0
```

Note: The test needs a `_minimal_settings()` helper — create one or use an existing test fixture from the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_signal_engine.py::test_prepare_frame_uses_adjusted_prices_when_corp_actions_provided -v`
Expected: FAIL — `_prepare_frame` doesn't accept `corp_actions`

- [ ] **Step 3: Modify `_prepare_frame` to accept and apply corp_actions**

In `src/sepa_scanner/strategy/signal_engine.py`:

1. Add import at top:
```python
from sepa_scanner.data_sources.corporate_actions import (
    build_adjusted_ohlcv,
    compute_adjustment_factors,
)
```

2. Add `corp_actions: pl.DataFrame | None = None` parameter to `_prepare_frame`:
```python
def _prepare_frame(frame: pl.DataFrame, settings: AppSettings, corp_actions: pl.DataFrame | None = None) -> pl.DataFrame:
```

3. At the very start of `_prepare_frame`, before `trend = settings.trend_template`, add adjustment logic:
```python
    # Apply corporate actions adjustment if provided.
    # All rolling indicators must be computed on adjusted prices.
    # raw_close and raw_volume are preserved for signal-day display.
    if corp_actions is not None and not corp_actions.is_empty():
        adj_factors = compute_adjustment_factors(frame.lazy(), corp_actions)
        frame = build_adjusted_ohlcv(frame.lazy(), adj_factors).collect()
```

4. Thread `corp_actions` through `scan_for_date`:
```python
def scan_for_date(
    frame: pl.DataFrame,
    settings: AppSettings,
    scan_date: date,
    *,
    _prepared: pl.DataFrame | None = None,
    _lifecycle: pl.DataFrame | None = None,
    _rs_scores: pl.DataFrame | None = None,
    corp_actions: pl.DataFrame | None = None,
) -> ScanResult:
    prepared = _prepared if _prepared is not None else _prepare_frame(frame, settings, corp_actions=corp_actions)
```

5. Thread `corp_actions` through `scan_batch`:
```python
def scan_batch(
    frame: pl.DataFrame,
    settings: AppSettings,
    scan_dates: list[date],
    corp_actions: pl.DataFrame | None = None,
) -> Iterator[tuple[date, ScanResult | None, str | None]]:
    prepared = _prepare_frame(frame, settings, corp_actions=corp_actions)
```

6. In the signal output section, ensure that when `raw_close` exists, the signal's `close` field uses `raw_close` for display (since `close` column is now adjusted). Add this after `enriched = scan_rows.with_columns(...)`:
```python
    # If adjusted, use raw_close for signal display fields
    if "raw_close" in enriched.columns:
        enriched = enriched.with_columns(pl.col("raw_close").alias("close"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_signal_engine.py::test_prepare_frame_uses_adjusted_prices_when_corp_actions_provided -v`
Expected: PASS

- [ ] **Step 5: Run existing signal engine tests to verify no regressions**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_signal_engine.py -v`
Expected: PASS (existing tests pass since `corp_actions` defaults to `None`, so no adjustment is applied)

- [ ] **Step 6: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/strategy/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: scanner pipeline uses adjusted OHLCV for indicator computation"
```

---

## Chunk 3: Backtest Engine Integration

### Task 6: Add corporate actions query helper

(Previously Task 5)

**Files:**
- Modify: `src/sepa_scanner/backtest/queries.py`
- Test: (tested implicitly via Task 6 integration tests)

- [ ] **Step 1: Add `get_corporate_actions` to queries.py**

Add to `src/sepa_scanner/backtest/queries.py`:

```python
def get_corporate_actions(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> pl.DataFrame:
    """Fetch corporate actions for the given symbols."""
    if not symbols:
        return pl.DataFrame(schema={
            "symbol": pl.String, "isin": pl.String,
            "action_type": pl.String, "ex_date": pl.Date,
            "ratio_num": pl.Int32, "ratio_den": pl.Int32,
            "old_face_value": pl.Float64, "new_face_value": pl.Float64,
            "adjustment_factor": pl.Float64,
            "description": pl.String, "source": pl.String,
        })
    return conn.execute(
        "SELECT * FROM corporate_actions WHERE symbol IN (SELECT UNNEST(?)) ORDER BY symbol, ex_date",
        [symbols],
    ).pl()
```

- [ ] **Step 2: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/backtest/queries.py
git commit -m "feat: add get_corporate_actions query helper"
```

---

### Task 7: Modify backtest engine to skip trades overlapping splits

**Files:**
- Modify: `src/sepa_scanner/backtest/engine.py`
- Test: `tests/test_backtest_skip_split.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_skip_split.py`:

```python
from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams


def _signal(
    *,
    symbol: str = "ABC",
    signal_date: date = date(2026, 3, 2),
    entry_ref: float = 101.0,
    stop: float = 95.0,
    pivot: float = 100.0,
) -> pl.DataFrame:
    return pl.DataFrame({
        "signal_date": [signal_date],
        "symbol": [symbol],
        "signal_type": ["BREAKOUT"],
        "pivot": [pivot],
        "entry_ref": [entry_ref],
        "stop": [stop],
    })


def _ohlcv(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _corp_actions(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema={
            "symbol": pl.String,
            "action_type": pl.String,
            "ex_date": pl.Date,
            "adjustment_factor": pl.Float64,
        })
    return pl.DataFrame(rows)


def test_skip_split_during_pending_window() -> None:
    """Signal with a split ex_date in the pending window => skipped_split."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 50.0, "high": 52.0, "low": 49.0, "close": 51.0},
        {"date": date(2026, 3, 5), "symbol": "ABC", "open": 51.0, "high": 52.5, "low": 50.5, "close": 52.0},
    ])
    corp = _corp_actions([{
        "symbol": "ABC", "action_type": "split",
        "ex_date": date(2026, 3, 4), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(pending_days=5), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status == "skipped_split"
    assert trade.entry_date is None


def test_skip_split_during_holding_period() -> None:
    """Trade entered, but split ex_date falls during max hold window => skipped_split."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
        {"date": date(2026, 3, 5), "symbol": "ABC", "open": 51.0, "high": 52.0, "low": 50.0, "close": 51.5},
        {"date": date(2026, 3, 6), "symbol": "ABC", "open": 51.5, "high": 52.5, "low": 51.0, "close": 52.0},
    ])
    corp = _corp_actions([{
        "symbol": "ABC", "action_type": "split",
        "ex_date": date(2026, 3, 5), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=10), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status == "skipped_split"


def test_no_skip_when_no_split() -> None:
    """Normal trade without any corporate action => proceeds normally."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])
    corp = _corp_actions([])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status != "skipped_split"


def test_no_skip_when_corp_actions_none() -> None:
    """Backward compat: corp_actions=None => no skip logic, normal simulation."""
    signals = _signal(signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2))
    trade = result.trades[0]
    assert trade.status != "skipped_split"


def test_skip_split_different_symbol_not_affected() -> None:
    """Split for different symbol doesn't affect this trade."""
    signals = _signal(symbol="ABC", signal_date=date(2026, 3, 2))
    prices = _ohlcv([
        {"date": date(2026, 3, 3), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
        {"date": date(2026, 3, 4), "symbol": "ABC", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8},
    ])
    corp = _corp_actions([{
        "symbol": "XYZ", "action_type": "split",
        "ex_date": date(2026, 3, 4), "adjustment_factor": 0.5,
    }])

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2), corp_actions=corp)
    trade = result.trades[0]
    assert trade.status != "skipped_split"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_backtest_skip_split.py -v`
Expected: FAIL — `simulate_breakout_backtest` doesn't accept `corp_actions` keyword

- [ ] **Step 3: Modify backtest engine**

Changes to `src/sepa_scanner/backtest/engine.py`:

1. Add import at top:
```python
from sepa_scanner.data_sources.corporate_actions import has_split_in_window
```

2. Add `corp_actions: pl.DataFrame | None = None` parameter to `_simulate_signal`:
```python
def _simulate_signal(
    signal: dict[str, object],
    symbol_prices: list[dict[str, object]],
    params: BacktestParams,
    corp_actions: pl.DataFrame | None = None,
) -> BacktestTrade:
```

3. After `entry_ref = float(signal["entry_ref"])` and `initial_stop = float(signal["stop"])` (around line 102-103), add the pending-window split check:

```python
    # Check 1: skip if split during pending window
    if corp_actions is not None and not corp_actions.is_empty():
        from datetime import timedelta
        pending_end = signal["signal_date"] + timedelta(days=int(params.pending_days * 1.5) + 5)
        if has_split_in_window(str(signal["symbol"]), signal["signal_date"], pending_end, corp_actions):
            return _empty_trade(signal, "skipped_split")
```

4. After the entry is confirmed (after `trade = BacktestTrade(...)` around line 137), add holding-period split check:

```python
    # Check 2: skip if split during holding period
    if corp_actions is not None and not corp_actions.is_empty():
        from datetime import timedelta
        max_exit = entry_date + timedelta(days=int(params.max_hold_days * 1.5) + 5)
        if has_split_in_window(str(signal["symbol"]), entry_date, max_exit, corp_actions):
            return _empty_trade(signal, "skipped_split")
```

5. Update `_simulate_symbol_group` to accept and pass `corp_actions`:

```python
def _simulate_symbol_group(
    symbol_signals: list[dict[str, object]],
    symbol_prices: list[dict[str, object]],
    params: BacktestParams,
    corp_actions: pl.DataFrame | None = None,
) -> list[BacktestTrade]:
```

And pass it through:
```python
        trade = _simulate_signal(signal, symbol_prices, params, corp_actions=corp_actions)
```

6. Update `simulate_breakout_backtest` to accept and pass `corp_actions`:

```python
def simulate_breakout_backtest(
    signals_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    params: BacktestParams,
    corp_actions: pl.DataFrame | None = None,
) -> BacktestResult:
```

And in both the parallel and sequential paths, pass `corp_actions`:

Parallel:
```python
                pool.submit(
                    _simulate_symbol_group,
                    sym_signals,
                    prices_by_symbol.get(sym, []),
                    params,
                    corp_actions,
                )
```

Sequential:
```python
            all_trades.extend(
                _simulate_symbol_group(sym_signals, prices_by_symbol.get(sym, []), params, corp_actions)
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_backtest_skip_split.py -v`
Expected: PASS (all 5)

- [ ] **Step 5: Run existing backtest tests to verify no regressions**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_backtest_engine.py -v`
Expected: PASS (all existing tests unchanged since `corp_actions` defaults to `None`)

- [ ] **Step 6: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/backtest/engine.py tests/test_backtest_skip_split.py
git commit -m "feat: backtest engine skips trades overlapping corporate actions"
```

---

### Task 8: Update backtest metrics to handle skipped_split

**Files:**
- Modify: `src/sepa_scanner/backtest/metrics.py`
- Modify: `src/sepa_scanner/backtest/models.py`
- Test: `tests/test_backtest_metrics.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backtest_metrics.py`:

```python
def test_skipped_split_excluded_from_metrics() -> None:
    from sepa_scanner.backtest.models import BacktestTrade
    from sepa_scanner.backtest.metrics import summarize_backtest

    trades = [
        BacktestTrade(
            symbol="ABC", signal_date=date(2026, 3, 2), status="stopped",
            entry_ref=101.0, stop=95.0, entry_date=date(2026, 3, 3),
            entry_price=101.0, exit_date=date(2026, 3, 5), exit_price=95.0,
            holding_days=2, return_pct=-5.94, r_multiple=-1.0,
        ),
        BacktestTrade(
            symbol="XYZ", signal_date=date(2026, 3, 4), status="skipped_split",
            entry_ref=200.0, stop=190.0,
        ),
    ]
    summary = summarize_backtest(trades)
    assert summary.total_signals == 2
    assert summary.filled_trades == 1
    assert summary.skipped_split_count == 1
    # skipped_split should NOT be counted in closed trades or affect metrics
    assert summary.stopped_trades == 1
    assert summary.win_rate == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_backtest_metrics.py::test_skipped_split_excluded_from_metrics -v`
Expected: FAIL — `BacktestSummary` has no `skipped_split_count` field

- [ ] **Step 3: Add `skipped_split_count` to BacktestSummary**

In `src/sepa_scanner/backtest/models.py`, add to `BacktestSummary` (after `avg_giveback_pct`):

```python
    skipped_split_count: int = 0
```

- [ ] **Step 4: Update `summarize_backtest` to count skipped_split**

In `src/sepa_scanner/backtest/metrics.py`, in `summarize_backtest()`, add before the `return BacktestSummary(...)`:

```python
    skipped_split_count = sum(1 for trade in trades if trade.status == "skipped_split")
```

And add `skipped_split_count=skipped_split_count` to the `BacktestSummary(...)` constructor call.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/test_backtest_metrics.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/backtest/models.py src/sepa_scanner/backtest/metrics.py tests/test_backtest_metrics.py
git commit -m "feat: add skipped_split_count to backtest summary metrics"
```

---

## Chunk 4: API + Frontend Integration

### Task 9: Wire corporate actions into backtest API route

**Files:**
- Modify: `src/sepa_scanner/api/routes/backtest.py`

- [ ] **Step 1: Update the backtest API route**

In `src/sepa_scanner/api/routes/backtest.py`:

1. Add import:
```python
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals, get_corporate_actions
```

2. In `run_backtest()`, after `ohlcv_df = get_backtest_ohlcv(...)`, add:
```python
    corp_actions = get_corporate_actions(conn, symbols)
```

3. Pass to engine:
```python
    result = simulate_breakout_backtest(signals_df, ohlcv_df, params, corp_actions=corp_actions)
```

4. Add `skipped_split_count` to summary serialization — it's already handled by `asdict(summary)` since we added the field to the dataclass.

- [ ] **Step 2: Run existing backtest tests**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/ --ignore-glob='tests/test_ui_*' -v -x`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add src/sepa_scanner/api/routes/backtest.py
git commit -m "feat: wire corporate actions into backtest API route"
```

---

### Task 10: Update frontend types and components

**Files:**
- Modify: `sepa-terminal/src/lib/types.ts`
- Modify: `sepa-terminal/src/components/backtest/TradeList.tsx`
- Modify: `sepa-terminal/src/components/backtest/StatsSummary.tsx`

- [ ] **Step 1: Update TypeScript types**

In `sepa-terminal/src/lib/types.ts`:

1. Add `'skipped_split'` to `BacktestTrade.status` union:
```typescript
status: 'not_filled' | 'stopped' | 'target' | 'time_stop' | 'incomplete_data' | 'skipped_overlap' | 'skipped_split'
```

2. Add `skippedSplitCount` to `BacktestSummary`:
```typescript
  skippedSplitCount: number
```

- [ ] **Step 2: Add skipped_split to TradeList status pill**

In `sepa-terminal/src/components/backtest/TradeList.tsx`, in `statusPillProps`:

Add a case before `default`:
```typescript
    case 'skipped_split':
      return { label: 'Split Skip', variant: 'purple' }
```

- [ ] **Step 3: Add skipped_split count to StatsSummary**

In `sepa-terminal/src/components/backtest/StatsSummary.tsx`, add a metric to the `metrics` array (after "Filled"):

```typescript
    ...(summary.skippedSplitCount > 0
      ? [{
          label: 'Split Skip',
          value: String(summary.skippedSplitCount),
          color: '#a78bfa',
        }]
      : []),
```

- [ ] **Step 4: Build the frontend to verify no errors**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
git add sepa-terminal/src/lib/types.ts sepa-terminal/src/components/backtest/TradeList.tsx sepa-terminal/src/components/backtest/StatsSummary.tsx
git commit -m "feat: frontend support for skipped_split backtest status"
```

---

## Chunk 5: Full Integration Test

### Task 11: Run all tests and verify end-to-end

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -m pytest tests/ --ignore-glob='tests/test_ui_*' -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 2: Run frontend type check**

Run: `cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Verify the seed CSV loads correctly**

Run a quick Python check:
```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger && python -c "
from sepa_scanner.data_sources.corporate_actions import load_corporate_actions_csv
from pathlib import Path
df = load_corporate_actions_csv(Path('config/corporate_actions.csv'))
print(df)
print(f'Loaded {df.height} corporate actions')
"
```
Expected: 6 rows printed, all parsed correctly

- [ ] **Step 4: Final commit if any fixups needed**

---

## Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | DuckDB schema + store CRUD | None |
| 2 | CSV loader + core functions | None |
| 3 | Adjustment factor tests (incl. IRCTC real-world) | Task 2 |
| 4 | Adjusted OHLCV + has_split + signal-day invariant tests | Task 2 |
| 5 | Scanner pipeline adjusted OHLCV integration | Tasks 2, 4 |
| 6 | Query helper | Task 1 |
| 7 | Backtest engine skip logic | Task 2 |
| 8 | Metrics skipped_split | Task 7 |
| 9 | API route wiring | Tasks 6, 7, 8 |
| 10 | Frontend types + components | Task 8 |
| 11 | Full integration test | All |

**Parallelizable batches:**
- Batch 1: Tasks 1, 2 (independent data layer work)
- Batch 2: Tasks 3, 4, 6 (tests + query, depend on Batch 1)
- Batch 3: Tasks 5, 7 (scanner pipeline + engine changes, can run in parallel)
- Batch 4: Tasks 8, 9, 10 (metrics + API + frontend, can partially parallel)
- Batch 5: Task 11 (integration verification)

**Design notes:**
- The heuristic `timedelta(days=int(N * 1.5) + 5)` in the backtest split-check converts trading days to calendar days conservatively (weekends + holidays). This avoids loading a trading calendar just for the skip check. Since we are *discarding* trades (not profiting from lookahead), the slight over-estimation is harmless.
- `compute_adjustment_factors` collects the lazy frame eagerly because the corp_actions table is small (~10-100 rows). The join is O(dates × actions_per_symbol), which is negligible.
- In `build_adjusted_ohlcv`, Polars `with_columns` reads all expressions from original column values, so `raw_close` correctly captures the pre-adjustment `close`. This is safe in all supported Polars versions.

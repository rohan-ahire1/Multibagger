# SEPA Setup Desk Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the Streamlit UI into a setup-first trading workspace with persistent review actions, improved SEPA chart review, and a secondary research area for backtests and performance analysis.

**Architecture:** Keep the existing single-page Streamlit architecture and current storage stack, but add a small persisted review-state table in DuckDB and a pure helper layer for setup queues so the UI stays testable. Reuse `queries.py` for read access, add a narrow queue-composition module for deterministic ranking/filtering, and upgrade `charts.py` with pure payload helpers before rewiring `app.py` into `Setups`, `Symbol`, and `Research`.

**Tech Stack:** Python 3.12, Streamlit, streamlit-lightweight-charts, Plotly, Polars, DuckDB, Pytest

---

### Task 1: Add persisted setup review state

**Files:**
- Modify: `src/sepa_scanner/types.py`
- Modify: `src/sepa_scanner/storage/schema.sql`
- Modify: `src/sepa_scanner/storage/duckdb_store.py`
- Test: `tests/test_duckdb_store.py`

**Step 1: Write the failing test**

Add a new store test for persisted setup reviews:

```python
from sepa_scanner.types import SetupReview


def test_duckdb_store_upserts_and_fetches_setup_reviews(tmp_path: Path) -> None:
    store = DuckDbStore(tmp_path / "sepa.duckdb")
    reviewed_at = datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc)

    first = SetupReview(
        scan_date=date(2026, 3, 7),
        symbol="RELIANCE",
        review_status="shortlisted",
        priority=2,
        note="Tight handle, volume dry-up looks clean",
        reviewed_at=reviewed_at,
        updated_at=reviewed_at,
    )
    replacement = replace(first, priority=3, note="Promote if market confirms")

    store.upsert_setup_reviews([first, replacement])
    rows = store.fetch_setup_reviews(scan_date=date(2026, 3, 7))

    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["review_status"] == "shortlisted"
    assert rows[0]["priority"] == 3
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_duckdb_store.py::test_duckdb_store_upserts_and_fetches_setup_reviews -v`
Expected: FAIL because `SetupReview`, `setup_reviews`, and the new store methods do not exist yet.

**Step 3: Write minimal implementation**

Implement:

- a `SetupReview` dataclass in `src/sepa_scanner/types.py`
- a new `setup_reviews` table in `src/sepa_scanner/storage/schema.sql`
- store methods in `src/sepa_scanner/storage/duckdb_store.py`

Use a schema like:

```sql
CREATE TABLE IF NOT EXISTS setup_reviews (
    scan_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    review_status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    reviewed_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (scan_date, symbol)
);
```

And add narrow store methods:

```python
def upsert_setup_reviews(self, reviews: list[SetupReview]) -> None:
    ...


def fetch_setup_reviews(self, scan_date: date) -> list[dict]:
    ...
```

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_duckdb_store.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/types.py src/sepa_scanner/storage/schema.sql src/sepa_scanner/storage/duckdb_store.py tests/test_duckdb_store.py
git commit -m "feat: persist setup review state"
```

### Task 2: Add setup-review queries and queue composition helpers

**Files:**
- Modify: `src/sepa_scanner/ui/queries.py`
- Create: `src/sepa_scanner/ui/setup_models.py`
- Modify: `tests/test_ui_queries.py`
- Create: `tests/test_ui_setup_models.py`

**Step 1: Write the failing tests**

Extend `tests/test_ui_queries.py` with a read-path test:

```python
def test_ui_queries_return_setup_reviews_for_scan_date(tmp_path: Path) -> None:
    store = DuckDbStore(tmp_path / "sepa.duckdb")
    reviewed_at = datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc)
    store.upsert_setup_reviews(
        [
            SetupReview(
                scan_date=date(2026, 3, 7),
                symbol="RELIANCE",
                review_status="shortlisted",
                priority=2,
                note="Tight shelf",
                reviewed_at=reviewed_at,
                updated_at=reviewed_at,
            )
        ]
    )

    with duckdb.connect(str(tmp_path / "sepa.duckdb")) as conn:
        reviews = get_setup_reviews(conn, date(2026, 3, 7))

    assert reviews["symbol"].to_list() == ["RELIANCE"]
```

Create `tests/test_ui_setup_models.py` for deterministic queue composition:

```python
def test_build_setup_rows_merges_reviews_and_assigns_queues() -> None:
    rows = build_setup_rows(signals_df, watchlist_df, reviews_df)

    assert rows.filter(pl.col("queue") == "Breakout Now")["symbol"].to_list() == ["TCS"]
    assert rows.filter(pl.col("queue") == "Shortlisted")["symbol"].to_list() == ["RELIANCE"]
    assert rows.filter(pl.col("queue") == "Rejected")["symbol"].to_list() == ["INFY"]
```

**Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_ui_queries.py tests/test_ui_setup_models.py -v`
Expected: FAIL because `get_setup_reviews()` and the queue-composition helpers do not exist yet.

**Step 3: Write minimal implementation**

Implement:

- `get_setup_reviews(conn, scan_date)` in `src/sepa_scanner/ui/queries.py`
- a pure queue-composition module in `src/sepa_scanner/ui/setup_models.py`

Recommended helpers:

```python
def build_setup_rows(
    signals_df: pl.DataFrame,
    watchlist_df: pl.DataFrame,
    reviews_df: pl.DataFrame,
) -> pl.DataFrame:
    ...


def filter_setup_queue(rows: pl.DataFrame, queue: str) -> pl.DataFrame:
    ...
```

Use `build_setup_rows()` to:

- outer-merge watchlist and signal rows by `symbol`
- preserve `BREAKOUT` urgency over watchlist-only rows
- fold in persisted `review_status`, `priority`, and `note`
- derive queue labels: `Breakout Now`, `Near Pivot`, `Shortlisted`, `Rejected`
- derive card-ready fields like `risk_pct`, `distance_from_pivot_pct`, and `has_note`

Keep this logic pure and testable. Do not bury queue rules inside Streamlit widgets.

**Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_ui_queries.py tests/test_ui_setup_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/ui/queries.py src/sepa_scanner/ui/setup_models.py tests/test_ui_queries.py tests/test_ui_setup_models.py
git commit -m "feat: add setup review queries and queues"
```

### Task 3: Add SEPA review chart payload helpers and presets

**Files:**
- Modify: `src/sepa_scanner/ui/charts.py`
- Modify: `tests/test_ui_charts.py`
- Modify: `docs/UI_BIBLE.md`

**Step 1: Write the failing tests**

`tests/test_ui_charts.py` currently targets an old Plotly candlestick helper. Replace or extend it so the tests target pure payload helpers that match the current lightweight chart implementation:

```python
from sepa_scanner.ui.charts import build_review_chart_payload


def test_build_review_chart_payload_adds_review_window_and_levels() -> None:
    payload, meta = build_review_chart_payload(
        ohlcv_df=ohlcv,
        signals_df=signals,
        selected_signal_row=selected_signal,
        timeframe="D",
        window_key="6M",
        show_sma50=True,
        show_sma150=True,
        show_sma200=True,
        show_ref_lines=True,
    )

    assert meta["window_key"] == "6M"
    assert round(meta["volume_ratio"], 2) == 2.00
    assert meta["has_extension_band"] is True
    assert len(payload) == 2
```

**Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_ui_charts.py -v`
Expected: FAIL because the new helper and metadata do not exist yet.

**Step 3: Write minimal implementation**

In `src/sepa_scanner/ui/charts.py`:

- extract a pure `build_review_chart_payload(...)` helper from the current `render_symbol_chart()` logic
- add window presets `8W`, `6M`, `1Y`, `2Y`
- separate `Daily` vs `Weekly` review mode
- compute chart metadata for:
  - `volume_ratio`
  - `risk_pct`
  - pivot zone
  - extension band

Wire the helper back into `render_symbol_chart()`:

```python
payload, meta = build_review_chart_payload(
    ohlcv_df,
    signals_df,
    selected_signal_row,
    timeframe=timeframe,
    window_key=window_key,
    show_sma50=show_sma50,
    show_sma150=show_sma150,
    show_sma200=show_sma200,
    show_ref_lines=show_ref_lines,
)
renderLightweightCharts(payload, key)
```

Update `docs/UI_BIBLE.md` in the same commit for:

- top-level chart controls
- setup-review chart behavior
- new time-window presets
- new annotations and chart emphasis

**Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_ui_charts.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/ui/charts.py tests/test_ui_charts.py docs/UI_BIBLE.md
git commit -m "feat: upgrade setup review charts"
```

### Task 4: Build the `Setups` workspace and persistent review actions

**Files:**
- Modify: `src/sepa_scanner/ui/app.py`
- Create: `tests/test_ui_app.py`
- Modify: `docs/UI_BIBLE.md`

**Step 1: Write the failing test**

Add a pure-selection regression test so the queue view behaves deterministically even as Streamlit reruns:

```python
from sepa_scanner.ui.app import choose_active_setup


def test_choose_active_setup_prefers_selected_symbol_then_falls_back_to_first_row() -> None:
    rows = pl.DataFrame(
        {
            "symbol": ["TCS", "RELIANCE"],
            "queue": ["Breakout Now", "Breakout Now"],
            "priority": [3, 1],
        }
    )

    assert choose_active_setup(rows, "RELIANCE")["symbol"] == "RELIANCE"
    assert choose_active_setup(rows, "SBIN")["symbol"] == "TCS"
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_ui_app.py -v`
Expected: FAIL because `choose_active_setup()` and the new setup-workspace helpers do not exist yet.

**Step 3: Write minimal implementation**

Refactor `src/sepa_scanner/ui/app.py` so the top-level UI is:

```python
setup_tab, symbol_tab, research_tab = st.tabs(["Setups", "Symbol", "Research"])
```

Inside `Setups`:

- render the compact summary strip
- add a queue selector for `Breakout Now`, `Near Pivot`, `Shortlisted`, `Rejected`
- build the left table from `build_setup_rows()`
- build the right detail panel with:
  - entry
  - stop
  - `R/share`
  - distance from pivot
  - volume vs `vol50`
  - review flags
- persist actions immediately with `DuckDbStore.upsert_setup_reviews()`

Also update `_load_resources()` so the app has both:

- a writable `DuckDbStore`
- a read connection for cached query helpers

For example:

```python
@st.cache_resource(show_spinner=False)
def _load_resources(config_dir_value: str | None) -> tuple[ParquetOhlcvStore, DuckDbStore, duckdb.DuckDBPyConnection]:
    ...
```

Update `docs/UI_BIBLE.md` in the same commit for:

- new top-level navigation
- `Setups` replacing `Dashboard`, `Signals`, and `Watchlist`
- review actions and queue states
- the new default workflow

**Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_ui_app.py tests/test_ui_queries.py tests/test_ui_setup_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/ui/app.py tests/test_ui_app.py docs/UI_BIBLE.md
git commit -m "feat: add setup desk workspace"
```

### Task 5: Consolidate `Research`, clean up `Symbol`, and finish docs

**Files:**
- Modify: `src/sepa_scanner/ui/app.py`
- Modify: `tests/test_ui_backtest.py`
- Modify: `README.md`
- Modify: `docs/UI_BIBLE.md`
- Modify: `docs/plans/2026-03-08-sepa-setup-desk-design.md` only if implementation reveals a design correction

**Step 1: Write the failing test**

If you extract a small navigation helper, add a regression test for it. Otherwise, extend the existing UI regression tests so the research helpers remain importable after the tab restructure:

```python
def test_load_backtest_signals_still_queries_full_universe(monkeypatch) -> None:
    ...
```

Add one more assertion if you introduce helper constants:

```python
assert get_root_tab_labels() == ["Setups", "Symbol", "Research"]
```

**Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_ui_backtest.py tests/test_ui_app.py -v`
Expected: FAIL if helper names or imports changed during the refactor, exposing the remaining integration gaps.

**Step 3: Write minimal implementation**

Finalize `src/sepa_scanner/ui/app.py`:

- move `Backtest` and `Performance` under the `Research` top-level tab
- keep research controls functional but visually secondary
- simplify `Symbol` into a cleaner lookup + review-history page
- remove redundant top-level dashboard/watchlist/signal entry points

Update docs in the same task:

- `README.md`: replace the UI description with `Setups`, `Symbol`, and `Research`
- `docs/UI_BIBLE.md`: update architecture, navigation, tab definitions, chart controls, setup-review actions, and known limitations
- `docs/plans/2026-03-08-sepa-setup-desk-design.md`: only if the implementation forces a design correction

**Step 4: Run full verification**

Run: `./.venv/bin/pytest tests/test_duckdb_store.py tests/test_ui_queries.py tests/test_ui_setup_models.py tests/test_ui_charts.py tests/test_ui_app.py tests/test_ui_backtest.py -v`
Expected: PASS

Then run:

Run: `./.venv/bin/pytest -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/sepa_scanner/ui/app.py tests/test_ui_backtest.py README.md docs/UI_BIBLE.md docs/plans/2026-03-08-sepa-setup-desk-design.md
git commit -m "feat: finish setup desk redesign"
```

### Task 6: Final verification and code review handoff

**Files:**
- No planned code changes

**Step 1: Run the full UI workflow manually**

Manual verification checklist:

- open `sepa ui`
- confirm the root tabs are `Setups`, `Symbol`, `Research`
- review a breakout-heavy day
- review a watchlist-only day
- review an empty day
- verify `Shortlist`, `Reject`, `Priority`, and notes persist after rerun
- verify the chart presets and review overlays match the latest `docs/UI_BIBLE.md`

**Step 2: Verify CLI/UI docs match reality**

Check:

- `README.md`
- `docs/UI_BIBLE.md`
- `docs/plans/2026-03-08-sepa-setup-desk-design.md`

Expected: the docs describe the shipped navigation and setup-review workflow accurately.

**Step 3: Request a review**

Use `@requesting-code-review` before merge. Focus the review on:

- review persistence correctness
- queue classification rules
- chart usability regressions
- navigation simplification

**Step 4: Fix review findings**

If review feedback surfaces regressions or weak assumptions, use `@receiving-code-review` before changing behavior.

**Step 5: Finish the branch**

Use `@finishing-a-development-branch` after tests pass and the review is clean.

# SEPA Local UI Design

**Date:** 2026-03-07

## Goal

Add a local browser UI for the existing scanner so a user can inspect scan-day signals, browse a ranked daily watchlist even on quiet days, and review symbol history on annotated price charts without changing the existing update/scan workflow.

## Scope

- Persist daily watchlist candidates into DuckDB during scan.
- Add a Streamlit + Plotly UI under `src/sepa_scanner/ui/`.
- Add a `sepa ui` CLI entrypoint that launches the local app.
- Keep the UI read-only and local-only for V1.

## Architecture

### Scan and persistence

The existing `scan_for_date()` pipeline already computes the full set of inputs needed for a watchlist candidate snapshot. V1 will extend the scan result with a second dataframe, `watchlist_candidates`, built from the same `enriched` rows that currently produce WATCH and BREAKOUT alerts. The candidate definition on date `D` is:

1. `trend_template_pass(D) == True`
2. `rs_pass(D) == True`
3. `setup_pass(D) == True`

Candidates are ranked by `near_pivot ASC`, `rs_rank DESC`, `adv50 DESC`, truncated to a configurable `max_candidates` defaulting to 300, and written to DuckDB with `INSERT OR REPLACE`.

### UI read path

The UI stays thin:

- `queries.py` owns all DuckDB and Parquet reads and returns Polars dataframes.
- `charts.py` owns Plotly figure construction.
- `app.py` owns Streamlit session state, tab layout, and control wiring.

This keeps UI code from depending on write-path internals while still reusing `resolve_settings()`, `DuckDbStore`, and `ParquetOhlcvStore`.

### Chart behavior

Charts use a two-row Plotly figure:

- top row: candlesticks
- bottom row: volume bars

Signal dates are shown with separate WATCH and BREAKOUT scatter traces at exact trading dates. When a specific signal row is selected, the figure also draws horizontal lines for `pivot`, `entry_ref`, and `stop`, plus a highlight marker on the selected signal day.

## UX

### Sidebar

- scan date selector, defaulting to the latest available scan/run date
- symbol search box used by the Symbol tab and as a quick jump target

### Tabs

1. `Dashboard`
   - latest run metadata for the selected date
   - counts for WATCH, BREAKOUT, and watchlist candidates
   - top 20 watchlist candidates
2. `Signals`
   - selected-date signal table
   - type filter for all/WATCH/BREAKOUT
   - symbol selection from the filtered table
   - annotated chart below the table
3. `Watchlist`
   - selected-date watchlist candidate table
   - filters for max near-pivot threshold and minimum RS rank
   - friendly empty-state message when no rows match
4. `Symbol`
   - symbol selector driven by search/current context
   - last ~250 trading days of OHLCV
   - all historical signals for that symbol

## Error handling

- Empty signals/watchlist results render a user-facing info message instead of a stack trace.
- Missing OHLCV or missing symbol history renders a chart placeholder message.
- If DuckDB has no scan history yet, the UI should still load and explain that scan data is not available.

## Testing

- Storage tests for watchlist candidate schema, upsert, and fetch ordering.
- Scan-engine test coverage for candidate generation and ranking.
- CLI tests for the new `ui` command.
- Query/helper tests for key UI data reads where they protect behavior.

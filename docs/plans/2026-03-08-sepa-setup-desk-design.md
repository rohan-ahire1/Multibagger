# SEPA Setup Desk Redesign

**Date:** 2026-03-08

## Goal

Refocus the Streamlit UI around one primary job: reviewing SEPA-style setups quickly enough to make a buy, watch, or reject decision from the chart and setup context, with research workflows pushed into a secondary area.

## Product Direction

The current UI is strong at displaying scanner output, but it spreads the setup-review workflow across `Dashboard`, `Signals`, `Watchlist`, and `Symbol`. That forces the user to keep reconstructing context across tabs when the real question is simpler: "Is this a valid Minervini-style setup worth acting on?"

The redesign adopts a `Setup Desk` model:

- `Setups` becomes the primary working surface.
- `Symbol` remains available for deep single-ticker inspection.
- `Research` becomes a secondary destination that contains `Backtest` and `Performance`.

This keeps the scanner focused on setup triage instead of general analytics.

## Scope

- Replace the six current top-level tabs with three workflow-oriented destinations.
- Merge `Dashboard`, `Signals`, and `Watchlist` into a single `Setups` workspace.
- Move `Backtest` and `Performance` into a combined `Research` area.
- Add persistent trader review actions: `Shortlist`, `Reject`, `Priority`, and free-form notes.
- Upgrade the chart UX so the chart becomes the center of setup review rather than a secondary output.

Out of scope for this redesign:

- Real-time or intraday data
- Portfolio simulation
- Mobile-first redesign
- React or multi-page migration away from Streamlit

## Information Architecture

### Top-level navigation

The app should expose three top-level destinations:

1. `Setups`
2. `Symbol`
3. `Research`

### Setups

`Setups` is the default landing surface and replaces the current separation between `Dashboard`, `Signals`, and `Watchlist`.

The page structure is:

- compact summary strip at the top
- queue selector for setup state
- ranked setup table on the left
- setup detail card and large chart on the right

Recommended queues:

- `Breakout Now`
- `Near Pivot`
- `Shortlisted`
- `Rejected`

Each queue reflects a different stage in the trading decision process rather than a different database table.

### Symbol

`Symbol` remains for unrestricted lookup of any ticker, but should reuse the upgraded chart system and show any persisted review state if it exists for the selected date/symbol.

### Research

`Research` contains:

- `Backtest`
- `Performance`
- parameter sweep tooling

These remain useful for strategy development, but they should no longer compete with daily setup review for top-level attention.

## Setups Workflow

The `Setups` screen should optimize for the flow:

1. find candidate
2. inspect chart
3. judge setup quality
4. store decision

### Summary strip

The current dashboard metrics should become a compact header strip inside `Setups`:

- selected scan date
- market filter status
- WATCH count
- BREAKOUT count
- watchlist count
- shortlisted count

The existing scan history chart and pipeline gate breakdown should not disappear, but they should move into secondary panels such as expanders.

### Ranked queue table

The left-side table should support the main review queues and prioritize names by urgency:

- `Breakout Now` first by signal quality and risk cleanliness
- `Near Pivot` first by closeness to pivot, RS, and liquidity
- `Shortlisted` and `Rejected` sorted by user priority and recency

The table should remain row-click driven, because that is already lower-friction than a separate selectbox. The table should add review-state columns such as:

- review status
- priority
- note indicator
- decision flags

### Setup detail card

Above the chart, the selected row should render a `Setup Card` that translates raw data into decision context.

It should include:

- symbol and setup type
- entry, stop, and `R/share`
- percent risk to stop
- distance from pivot
- volume versus 50-day average
- RS rank
- liquidity snapshot
- concise `why it passed`
- concise `risk flags`
- action buttons for `Shortlist`, `Reject`, `Priority`, and `Add note`

This section should stay rules-based and transparent. It should not use an opaque scoring model.

## Chart Design

The chart should become the visual center of setup review.

### Core changes

- make the chart larger by default in the main setup workflow
- use review presets like `8W`, `6M`, `1Y`, `2Y`
- keep a separate `Daily / Weekly` mode toggle
- keep SMA 50, 150, and 200 as first-class overlays
- display pivot as a zone instead of only a single line
- show an extension zone above the pivot to reveal whether a breakout is already too extended
- surface volume expansion versus the 50-day average in the chart header
- add optional RS-line context versus the benchmark

### Annotation priorities

Default annotations should emphasize decision-making:

- pivot zone
- entry reference
- stop
- breakout candle
- entry/exit markers where relevant
- recent signal context

Historical markers should remain available but visually quieter than the active setup.

### Toolbar simplification

The current toolbar exposes too many equal-weight controls for a daily workflow. The redesign should:

- keep the most important controls visible
- move secondary chart options into an expander or popover
- remove the need for a dedicated `Expand` checkbox if the chart is already large

## Persistence And Data Model

The current UI relies on `st.session_state` for transient interface state. That is not sufficient for a review workflow where the user needs decisions to persist across reruns and scan dates.

Add a new DuckDB table for persisted review state.

### Proposed table

`setup_reviews`

Suggested fields:

- `scan_date`
- `symbol`
- `review_status` (`new`, `shortlisted`, `rejected`)
- `priority`
- `note`
- `reviewed_at`
- `updated_at`

Primary key:

- `(scan_date, symbol)`

This keeps user judgment separate from generated signals and watchlist snapshots while still allowing the UI to merge them efficiently for queue building.

## Data Flow

The redesigned `Setups` page should:

1. load selected-date signals
2. load selected-date watchlist candidates
3. load selected-date review state
4. build queue-specific views from the merged data
5. update the selected chart/detail panel from table row selection
6. persist review actions immediately on button click

`Symbol` should be able to read and show the same review metadata when the active date/symbol pair exists.

## Redundant Or Demoted Elements

The redesign intentionally removes or demotes several current structures.

### Remove as top-level tabs

- `Dashboard`
- `Signals`
- `Watchlist`
- `Backtest`
- `Performance`

### Demote to secondary surfaces

- scan history chart
- pipeline gate breakdown
- duplicate performance control groups
- standalone signal/watchlist browsing without decision state

### Replace

- free-text symbol-first review flow with queue-first review in `Setups`
- separate watchlist and signal tabs with a single ranked queue model
- chart-select widgets with row-click and setup actions

## Error Handling

- missing OHLCV should not block review-state actions
- review write failures should show explicit inline errors
- empty queues should use purposeful copy like `No actionable breakouts today`
- `Research` failures should not degrade the primary `Setups` workflow

## Testing

Required coverage for implementation:

- DuckDB schema and query tests for `setup_reviews`
- query tests for merged queue generation
- UI tests for review actions and queue filtering where practical
- chart tests for preset windows and new annotations where practical
- manual verification on:
  - breakout-heavy day
  - watchlist-only day
  - empty day

## Rollout Plan

Recommended implementation order:

1. add `setup_reviews` schema and persistence helpers
2. add merged review queries
3. collapse navigation to `Setups`, `Symbol`, `Research`
4. build new `Setups` layout
5. upgrade chart presets and annotations
6. move backtest and performance into `Research`
7. update `docs/UI_BIBLE.md` alongside code changes so the document stays canonical

## Success Criteria

The redesign is successful when:

- the user can review the day from one primary screen
- chart inspection is the dominant workflow, not tab switching
- manual decisions persist reliably
- research tooling remains available but stops distracting from setup selection
- the UI feels like a trading helper for SEPA setups rather than a generic signal dashboard


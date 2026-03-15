# Lorentzian Backtest Workspace Design

**Date:** 2026-03-15

## Goal

Add a separate `Lorentzian` workspace beside the existing `Backtest` workspace in `sepa-terminal`, reusing the same dashboard structure while backing it with an independent Lorentzian long-only strategy, API, cache, and state flow.

## Scope

- Add a fifth top-level workspace tab: `Lorentzian`
- Keep the existing `Backtest` workspace working as-is for the current SEPA breakout backtest flow
- Refactor the current backtest workspace layout into a shared shell that both strategies can render into
- Add a Lorentzian long-only backtest engine backed by the `advanced-ta` package
- Run the Lorentzian strategy across the full symbol universe only
- Preserve the existing backtest-style outputs:
  - summary metrics
  - equity curve
  - trade table
  - per-trade chart detail
  - `return_pct`, `r_multiple`, and `holding_days`
- Cache Lorentzian results separately from SEPA results
- Update strategy and UI documentation

## Non-Goals

- Do not modify the existing SEPA scanner signal generation pipeline
- Do not mix Lorentzian trades into the Analysis workspace in V1
- Do not expose the full `advanced-ta` feature tuning surface in V1
- Do not add short-side Lorentzian trading in V1
- Do not create a generic multi-strategy plugin framework beyond what is needed for the shared workspace shell

## Approaches Considered

### 1. Hard clone of the current Backtest workspace

Duplicate the existing workspace, store, query hooks, and API route, then swap the engine to Lorentzian.

**Pros**
- Fastest path to a visible result
- Low immediate regression risk to the current Backtest tab

**Cons**
- Creates a large amount of duplicate UI and state code
- Future changes to tables, charts, or keyboard navigation would need to be made twice
- High drift risk between the two workspaces

### 2. Shared backtest shell with separate strategy engines

Refactor the current Backtest workspace into reusable layout components while keeping strategy-specific backend routes, stores, params, and cache files separate.

**Pros**
- Delivers an almost identical second workspace without duplicating the whole feature
- Keeps SEPA and Lorentzian results isolated
- Makes future dashboard changes cheaper and safer

**Cons**
- Requires a small refactor before the new workspace appears

### 3. Fully generalized strategy registry

Build a complete multi-strategy framework for all backtest flows before adding Lorentzian.

**Pros**
- Cleanest long-term architecture if many more strategies are planned

**Cons**
- Too much abstraction for the current request
- Highest delivery time and regression risk

## Selected Approach

Use **Approach 2**: a shared backtest workspace shell with separate strategy engines.

This gives the new `Lorentzian` tab the same user experience as the current `Backtest` tab while avoiding large-scale duplication and keeping the Lorentzian strategy isolated from the existing SEPA backtest flow.

## Product Behavior

### Workspace layout

Top bar workspaces become:

1. `Market + Scanner`
2. `Analysis`
3. `Portfolio`
4. `Backtest`
5. `Lorentzian`

The `Lorentzian` workspace uses the same visual structure as `Backtest`:

- top parameter bar
- left column with metrics summary and trade table
- right column with equity curve and chart
- keyboard navigation through trades
- cached result auto-load on mount

### Shared shell boundaries

The shared shell owns:

- loading cached result into a strategy-specific store
- resolving the active trade from the selected trade key
- keyboard navigation (`j` / `k`)
- empty-state rendering
- left/right dashboard layout
- chart toolbar reuse
- price chart rendering using the selected trade

Strategy-specific wrappers own:

- params UI
- query hooks
- store
- result type
- route path
- cache key
- marker builder input
- any strategy-specific labels

## Lorentzian Strategy Design

### Package dependency

Use `advanced-ta==0.1.8`, pinned in project dependencies.

The package is community-maintained, so the application must isolate it behind an internal adapter rather than relying on raw library column names throughout the app.

### Data input

For each symbol:

- load full OHLCV history from `2010-01-01` through the requested `end_date`
- sort by date ascending
- convert to pandas with lower-case OHLCV columns

The Lorentzian engine must operate on the full symbol universe only. No single-symbol filter is added to the default UI flow.

### Signal extraction

The adapter normalizes the Lorentzian package output into an internal signal table with fields equivalent to:

- `symbol`
- `signal_date`
- `buy_signal`
- `sell_signal`
- `prediction`

The exact raw library output columns are intentionally hidden behind the adapter. If the package output shape changes, tests must fail loudly.

### Trade model

#### Entry

- The strategy is long-only in V1
- A `buy_signal` on date `D` becomes actionable on the next trading day
- Entry is taken at the next trading day `open`
- If there is no next trading day in the available data, the signal is marked `incomplete_data`

#### Initial stop

- The initial stop is the low of the buy-signal candle on `D`
- If the next-day open gaps below the stop, the trade is recorded as stopped at that open

This stop rule was selected because the UI and metrics rely on deterministic `R` framing, and the user explicitly preferred the Lorentzian strategy with a risk overlay.

#### Exit

Exit occurs at the earliest of:

1. stop breach
2. next-day open after a `sell_signal`
3. max holding period time stop

For time stops, exit at the final holding day close.

#### Metrics

Lorentzian trades must populate the same core outputs as the existing backtest workspace:

- `return_pct`
- `r_multiple`
- `holding_days`
- `mae_r`
- `mfe_r`

Trade table rows must continue to support win/loss highlighting based on realized return.

## Backend Architecture

### New package

Add a dedicated Lorentzian package:

- `src/sepa_scanner/lorentzian/__init__.py`
- `src/sepa_scanner/lorentzian/adapter.py`
- `src/sepa_scanner/lorentzian/signals.py`
- `src/sepa_scanner/lorentzian/backtest.py`
- optionally `src/sepa_scanner/lorentzian/models.py` if shared internal structures are needed

### Responsibilities

`adapter.py`
- wrap `advanced-ta`
- convert pandas input/output
- normalize raw columns to internal signal fields

`signals.py`
- load symbol histories from parquet
- generate Lorentzian signal rows across the full universe

`backtest.py`
- simulate Lorentzian entries and exits
- produce trade rows compatible with the existing dashboard expectations

### API

Add a separate router:

- `src/sepa_scanner/api/routes/lorentzian_backtest.py`

Endpoints:

- `POST /api/lorentzian-backtest/run`
- `GET /api/lorentzian-backtest/last`

Cache file:

- `data/backtest/lorentzian_latest.json`

The SEPA backtest cache remains:

- `data/backtest/latest.json`

These result files must never be mixed.

### Response shape

Return the same structural payload shape as the current backtest API so the shared shell can reuse the existing visualization components:

- `params`
- `summary`
- `trades`
- `equity_curve`

Lorentzian can define its own frontend param type while still sharing the common result structure.

## Frontend Architecture

### Navigation and workspace state

Update:

- `sepa-terminal/src/App.tsx`
- `sepa-terminal/src/components/layout/TopBar.tsx`
- `sepa-terminal/src/hooks/useWorkspaceSwitch.ts`
- `sepa-terminal/src/components/shared/CommandPalette.tsx`
- `sepa-terminal/src/stores/ui.ts`

Changes:

- widen workspace type to include `5`
- render the new `Lorentzian` workspace
- add hotkey `5`
- add command-palette navigation entry

### Shared components

Refactor reusable dashboard structure into shared backtest workspace components under:

- `sepa-terminal/src/components/backtest/`

Expected reusable pieces:

- shared workspace shell
- trade selection helpers if needed
- common chart-detail block

The existing `StatsSummary`, `TradeList`, `EquityCurve`, `ChartToolbar`, and `PriceChart` should be reused where possible.

### New Lorentzian UI files

- `sepa-terminal/src/components/workspaces/LorentzianBacktest.tsx`
- `sepa-terminal/src/components/lorentzian/LorentzianParamBar.tsx`
- `sepa-terminal/src/api/useLorentzianBacktest.ts`
- `sepa-terminal/src/stores/lorentzianBacktest.ts`

### Lorentzian parameters in V1

Expose:

- range presets
- `from`
- `to`
- `max hold`
- `stop model` with default `signal_candle_low`
- run button

Do not expose advanced package internals in V1:

- custom feature lists
- neighbor count
- kernel controls
- regime or ADX tuning

This keeps the first version stable and easier to validate visually.

## Testing Strategy

### Backend

Add unit tests for:

- adapter output normalization
- next-day-open entry after buy signal
- stop exit
- sell-signal exit
- max-hold exit
- incomplete trailing data handling

Add API tests for:

- Lorentzian run endpoint
- Lorentzian cached-result endpoint
- empty-state behavior

### Frontend

Protect:

- workspace navigation to the fifth tab
- shared shell rendering for both strategies
- Lorentzian run mutation and cached result load
- existing Backtest workspace behavior after the refactor

### Verification

Before claiming completion:

- run backend tests with `uv run pytest tests/` while ignoring `test_ui_*.py`
- run frontend lint and build
- manually verify both `Backtest` and `Lorentzian` workspaces load, run, and render charts without broken empty states

## Documentation Updates

Update:

- `docs/STRATEGY.md`
- `docs/UI_BIBLE.md`

`docs/STRATEGY.md` must clearly state:

- Lorentzian is a separate backtest workspace, not part of the SEPA signal engine
- V1 is long-only
- entry/exit/stop rules

`docs/UI_BIBLE.md` must clearly state:

- the fifth workspace tab
- shared-shell backtest dashboard behavior
- Lorentzian-specific parameter controls

## Risks And Safeguards

### Package output drift

Risk:
- `advanced-ta` is community-maintained and may change output shape

Safeguard:
- pin the version
- isolate output mapping behind the adapter
- add tests that fail if required columns disappear

### Performance across full universe

Risk:
- Lorentzian classification across the full NSE universe may be slow if recomputed naively

Safeguard:
- load only required columns
- process symbol-by-symbol with a narrow adapter boundary
- keep V1 parameter count small
- rely on cached last-result loading for the normal revisit flow

### UI regression in existing Backtest workspace

Risk:
- refactoring into a shared shell could break the current SEPA backtest workspace

Safeguard:
- keep strategy-specific stores and hooks separate
- reuse current result shape
- add frontend tests and manual verification for both tabs

# Backtest Panel + Chart Signal Markers — Design Spec

**Date:** 2026-03-14
**Status:** Approved

## Goal

Add a W4 Backtest Dashboard workspace with a master-detail layout for analyzing historical backtest results, a per-symbol Backtest History card in W2 Analysis (replacing the mock SignalHistory), and color-coded signal markers on charts across all workspaces (W1, W2, W4) showing the full trade lifecycle: Watch → Breakout → Entry → Exit.

## Architecture

The existing Python backtest engine (`simulate_breakout_backtest`, `summarize_backtest`, `build_equity_curve`) is exposed via new FastAPI endpoints. The frontend consumes these through TanStack Query hooks, stores backtest state in a new Zustand store, and renders markers on charts using the Lightweight Charts v5 Markers API. No new database tables are needed — signals live in DuckDB, backtest results are cached as JSON files on disk.

## Tech Stack

- Existing: FastAPI, DuckDB, Parquet OHLCV store, backtest engine (`src/sepa_scanner/backtest/`)
- Frontend: React 19, Lightweight Charts v5 (markers), Recharts (equity curve), TanStack Table (trade list), Zustand (backtest store), TanStack Query (data fetching)

---

## 1. Chart Signal Markers

### 1.1 Marker Types

Six color-coded markers rendered as upward arrows below candles:

| Marker | Label | Color | Hex | Data Source |
|--------|-------|-------|-----|-------------|
| Watch | W | Amber | `#f59e0b` | `signals` table, `signal_type = 'WATCH'` |
| Breakout | B | Green | `#22c55e` | `signals` table, `signal_type = 'BREAKOUT'` |
| Entry | E | Blue | `#3b82f6` | Backtest trade `entry_date` |
| Win Exit | X+ | Teal | `#10b981` | Backtest trade `exit_date` where `r_multiple > 0` |
| Loss Exit | X- | Red | `#ef4444` | Backtest trade `exit_date` where `r_multiple <= 0` |
| Not Filled | NF | Gray | `#6b7280` | Backtest trade `status = 'not_filled'`, placed on `signal_date` |

### 1.2 Where Markers Appear

All three chart locations:
- **W1 SetupDetailCard** — past signals for the selected setup's symbol
- **W2 Analysis** — full signal + backtest trade history for selected symbol
- **W4 Backtest** — selected trade's symbol with full lifecycle markers

### 1.3 Lightweight Charts v5 Integration

Markers are set on the candlestick series via `series.setMarkers(markers)`. Each marker is a `SeriesMarker<Time>` object:

```typescript
{
  time: '2025-06-15',       // ISO date string
  position: 'belowBar',
  shape: 'arrowUp',
  color: '#22c55e',          // from marker type
  text: 'B',                 // short label
}
```

When multiple markers fall on the same candle (e.g., Breakout + Entry on same day), they stack. Lightweight Charts handles this natively.

### 1.4 Data Flow

1. `useSymbolSignalHistory(symbol)` hook fetches `GET /api/analysis/{symbol}/signal-history`
2. Response: `{ signals: Signal[], trades: BacktestTrade[] }`
3. `buildChartMarkers(signals, trades)` mapper in `lib/chart-markers.ts` converts to `SeriesMarker[]`
4. `PriceChart` component receives `markers` prop, calls `candlestickSeries.setMarkers(markers)` after data is set

### 1.5 PriceChart Changes

The `PriceChart` component gains a new optional prop:

```typescript
import { type SeriesMarker, type Time } from 'lightweight-charts'

interface PriceChartProps {
  // ... existing props
  markers?: SeriesMarker<Time>[]
}
```

When `markers` changes, call `setMarkers()` on the candlestick series. Markers must be sorted by time ascending (Lightweight Charts requirement).

---

## 2. W4 Backtest Dashboard

### 2.1 Layout

Master-detail with parameter bar:

```
┌─────────────────────────────────────────────────┐
│ PARAM BAR: DateRange | ExitMode | TargetR |      │
│            PendingDays | MaxHold | [▶ Run]       │
├──────────────────┬──────────────────────────────┤
│ STATS SUMMARY    │ EQUITY CURVE (cumulative R)  │
│ WR, AvgR, CumR   │ + DRAWDOWN (underwater plot) │
│ DD, Payoff, etc  │                              │
├──────────────────┤──────────────────────────────┤
│ TRADE LIST       │ SELECTED TRADE CHART         │
│ (scrollable)     │ with signal markers           │
│                  │ (W → B → E → X lifecycle)    │
│ Click row →      │ + SMA overlays               │
│ updates chart    │ + entry/stop/target lines    │
└──────────────────┴──────────────────────────────┘
```

Left column: ~40% width. Right column: ~60% width. Panels are resizable via react-resizable-panels.

### 2.2 Parameter Bar

Controls across the top:

| Control | Type | Default | Maps to |
|---------|------|---------|---------|
| Start Date | Date input | 1 year ago | `BacktestParams` date range filter |
| End Date | Date input | Today | `BacktestParams` date range filter |
| Exit Mode | Dropdown | V2 Trail 21EMA | `BacktestParams.exit_mode` |
| Target R | Number input | null (disabled) | `BacktestParams.target_r` |
| Pending Days | Number input | 5 | `BacktestParams.pending_days` |
| Max Hold Days | Number input | 90 | `BacktestParams.max_hold_days` |
| Trail Activation R | Number input | 1.0 | `BacktestParams.trail_activation_r` |
| Run Backtest | Button (green) | — | Triggers POST `/api/backtest/run` |

**Hybrid behavior**: On first visit to W4, load last cached result via `GET /api/backtest/last`. User can tweak params and hit Run to re-compute. Button shows spinner while running.

### 2.3 Stats Summary

Compact grid showing metrics from `BacktestSummary`:

| Metric | Display |
|--------|---------|
| Total Signals | count |
| Filled Trades | count |
| Win Rate | percentage |
| Avg R | R-multiple |
| Cumulative R | R-multiple |
| Max Drawdown | R-multiple (negative) |
| Payoff Ratio | ratio |
| Longest Losing Streak | count |
| Avg MFE | R-multiple |
| Avg MAE | R-multiple |
| Avg Giveback | percentage |

Color coding: Win rate green if >40%, drawdown red, cumulative R green if positive.

### 2.4 Equity Curve

Two stacked Recharts charts:

**Top — Cumulative R** (AreaChart, ~120px):
- X axis: trade exit dates
- Y axis: cumulative R-multiple
- Green fill when positive, red when negative
- From `equity_curve.cumulative_r` column

**Bottom — Drawdown** (AreaChart, ~60px):
- X axis: same as above
- Y axis: drawdown (always ≤ 0)
- Red fill
- From `equity_curve.drawdown` column

### 2.5 Trade List

Scrollable TanStack Table with columns:

| Column | Width | Content |
|--------|-------|---------|
| Symbol | 80px | Ticker |
| Signal Date | 90px | YYYY-MM-DD |
| Entry | 70px | Entry price (₹) or "—" if not filled |
| Exit | 70px | Exit price (₹) or "—" |
| R | 50px | R-multiple, green/red colored |
| Status | 70px | Pill: stopped (red), target (green), time_stop (amber), not_filled (gray) |

Click a row → right panel loads that trade's symbol chart with OHLCV + signal markers + entry/stop/target price lines.

Sortable by: Signal Date (default), R-multiple, Status, Symbol.

### 2.6 Selected Trade Chart

Reuses `PriceChart` component with:
- OHLCV data for the trade's symbol (fetched via existing `useOHLCV` hook)
- Signal markers from `useSymbolSignalHistory`
- Price reference lines: entry (blue dashed), stop (red dashed), target (green dashed, if target_r set)
- SMA overlays (50/150/200)
- ChartToolbar for timeframe/overlay toggles

### 2.7 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `4` | Switch to W4 (global) |
| `J` / `K` | Navigate trade list up/down |
| `R` | Focus Run Backtest button |
| `Escape` | Return to previous workspace |

---

## 3. W2 Backtest History Card

### 3.1 Replaces SignalHistory

`SignalHistory.tsx` is deleted. `BacktestHistory.tsx` takes its place in the Analysis info column, same position.

### 3.2 Card Content

**Summary line** (top):
```
3W / 2L — Win Rate 60% — Avg R: +0.6
```

**Past trades table** (middle):

| Signal Date | Entry | Exit | R | Status |
|-------------|-------|------|---|--------|
| 2025-12-03 | ₹5420 | ₹5890 | +1.8R | target |
| 2025-09-15 | ₹4980 | ₹4650 | -1.0R | stopped |

Compact rows, max ~10 visible with scroll. Color-coded R values.

**Last signal** (bottom):
```
Last: BREAKOUT on 2026-01-15
```

### 3.3 Cross-Navigation

Clicking a trade row in the W2 Backtest History card:
1. Sets `backtest.selectedTradeIndex` in the backtest store (stores the trade's `symbol` + `signalDate` as a stable identifier, not array index)
2. Sets `ui.activeWorkspace = 4`
3. W4 opens, loads cached backtest via `GET /api/backtest/last` if not already loaded, finds matching trade by symbol+signalDate, highlights row, loads chart

**Edge case — no backtest result cached:** If `GET /api/backtest/last` returns 404, W4 shows an empty state prompting the user to run a backtest. The cross-nav still switches to W4 but the trade won't be pre-selected. The BacktestHistory card in W2 should show a subtle "(run backtest to see trade details)" hint when no cached result exists — the signals portion still works regardless.

---

## 4. Backend API

### 4.1 New Endpoints

**`POST /api/backtest/run`**

Request body:
```json
{
  "start_date": "2025-01-01",
  "end_date": "2026-03-14",
  "exit_mode": "v2_trail_21ema",
  "target_r": null,
  "pending_days": 5,
  "max_hold_days": 90,
  "trail_activation_r": 1.0
}
```

**Important:** The Python `BacktestParams` dataclass does NOT have `start_date`/`end_date` fields — those are separate query parameters for `get_backtest_signals()`. Create a new Pydantic request model `BacktestRunRequest` that wraps the date range + engine params:

```python
class BacktestRunRequest(BaseModel):
    start_date: date
    end_date: date
    exit_mode: str = "v2_trail_21ema"
    target_r: float | None = None
    pending_days: int = 5
    max_hold_days: int = 90
    trail_activation_r: float = 1.0
```

Flow:
1. Parse `BacktestRunRequest` from request body
2. Construct `BacktestParams` from the engine fields (exit_mode, target_r, pending_days, max_hold_days, trail_activation_r)
3. `get_backtest_signals(conn, start_date, end_date)` from DuckDB
4. Extract unique symbols, compute extended date range (end_date + max_hold_days)
5. `get_backtest_ohlcv(parquet_store, symbols, start_date, extended_end)` from Parquet
6. `simulate_breakout_backtest(signals_df, ohlcv_df, params)` → `BacktestResult`
7. Serialize trades from `BacktestResult.trades` (which is `list[BacktestTrade]` — NOT from `BacktestReport.trades` which is a `pl.DataFrame`)
8. `summarize_backtest(result.trades)` → `BacktestSummary`
9. `build_equity_curve(result.trades)` → equity curve DataFrame → serialize via `.to_dicts()`
10. Cache result as JSON to `data/backtest/latest.json`
11. Return JSON response

Response shape:
```json
{
  "params": { ... },
  "summary": { "total_signals": 156, "win_rate": 0.42, ... },
  "trades": [ { "symbol": "TATAELXSI", "signal_date": "2025-06-15", ... }, ... ],
  "equity_curve": [ { "exit_date": "2025-07-01", "cumulative_r": 1.2, "drawdown": 0, ... }, ... ]
}
```

**`GET /api/backtest/last`**

Reads `data/backtest/latest.json`. Returns same shape as `/run`. Returns 404 if no cached result.

**`GET /api/analysis/{symbol}/signal-history`** (replaces existing endpoint)

The existing `analysis.py` router already has `GET /{symbol}/signal-history` that returns only signals. This endpoint is **replaced** (not a new URL) to return both signals and backtest trades. The existing `SignalHistoryEntry` frontend type is retired — replaced by `SymbolSignalHistory`.

Queries DuckDB `signals` table for all signals for that symbol (all dates, both WATCH and BREAKOUT). Reads cached backtest trades from `data/backtest/latest.json`, filters by symbol, and includes matching trades. If no cached backtest exists, `trades` is an empty array.

Response:
```json
{
  "signals": [
    { "signal_date": "2025-06-10", "signal_type": "WATCH", "close": 5200, "pivot": 5400, ... },
    { "signal_date": "2025-06-15", "signal_type": "BREAKOUT", "close": 5420, "pivot": 5400, ... }
  ],
  "trades": [
    { "symbol": "TATAELXSI", "signal_date": "2025-06-15", "entry_date": "2025-06-16", "entry_price": 5430, "exit_date": "2025-07-20", "exit_price": 5890, "r_multiple": 1.8, "status": "target" }
  ]
}
```

**Edge case:** If `data/backtest/latest.json` does not exist (no backtest has ever been run), the endpoint returns `trades: []`. Signal markers (Watch/Breakout) still display; trade markers (Entry/Exit/NotFilled) are simply absent.

### 4.2 Router Mount

New router at `src/sepa_scanner/api/routes/backtest.py`, mounted in `app.py` with prefix `/api/backtest`. The signal history endpoint stays in the existing `analysis.py` router at its current URL path `/{symbol}/signal-history` (full URL: `/api/analysis/{symbol}/signal-history`).

### 4.3 Caching

Backtest results cached as `data/backtest/latest.json`. Simple file-based cache — each `POST /run` overwrites it. No TTL needed, user controls when to re-run. The backtest router needs file system write access to the data directory (injected via `AppSettings.runtime.data_dir`).

### 4.4 snake_case → camelCase Translation

The Python backend returns snake_case keys (e.g., `signal_date`, `entry_ref`, `r_multiple`). The frontend expects camelCase (e.g., `signalDate`, `entryRef`, `rMultiple`).

**Translation happens in the frontend API layer.** Each TanStack Query hook includes a `select` transform that converts snake_case response keys to camelCase using a simple recursive mapper utility. This is consistent with the existing approach in the codebase (e.g., `useOHLCV` already does key mapping).

Add a shared utility `lib/case.ts`:

```typescript
export function snakeToCamel(obj: any): any {
  if (Array.isArray(obj)) return obj.map(snakeToCamel)
  if (obj && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [
        k.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
        snakeToCamel(v),
      ])
    )
  }
  return obj
}
```

### 4.5 Serialization Notes

- `BacktestTrade` Python dataclass fields `mae_price` and `mfe_price` (raw prices) are **excluded** from the JSON response — only `mae_r` and `mfe_r` (R-multiple versions) are serialized. The frontend type intentionally omits these fields.
- `BacktestResult.trades` is `list[BacktestTrade]` (dataclass instances). Serialize via `dataclasses.asdict()`. Do NOT use `BacktestReport.trades` which is a `pl.DataFrame`.
- Equity curve `pl.DataFrame` is serialized via `.to_dicts()` which returns `list[dict]` with snake_case keys.

---

## 5. Frontend State & Data Flow

### 5.1 New Store: `stores/backtest.ts`

```typescript
interface SelectedTrade {
  symbol: string
  signalDate: string
}

interface BacktestState {
  params: BacktestParams
  result: BacktestReport | null
  selectedTrade: SelectedTrade | null
  isRunning: boolean
  setParams: (params: Partial<BacktestParams>) => void
  setResult: (report: BacktestReport) => void
  selectTrade: (trade: SelectedTrade) => void
  setRunning: (v: boolean) => void
}
```

Uses `symbol + signalDate` as a stable trade identifier rather than array index (which breaks if the trade list is re-sorted).

Not persisted — backtest results are large and cached server-side.

### 5.2 New Hooks

- `useBacktestLast()` — TanStack Query for `GET /api/backtest/last`, loads on W4 mount
- `useRunBacktest()` — TanStack Query mutation for `POST /api/backtest/run`
- `useSymbolSignalHistory(symbol)` — TanStack Query for `GET /api/analysis/{symbol}/signal-history`, 5min staleTime

### 5.3 Chart Markers Mapper

`lib/chart-markers.ts` exports `buildChartMarkers(signals, trades)`:

```typescript
import { type SeriesMarker, type Time } from 'lightweight-charts'

function buildChartMarkers(
  signals: Signal[],
  trades: BacktestTrade[]
): SeriesMarker<Time>[]
```

Maps each signal/trade event to the appropriate marker type, color, and label. Returns array sorted by time ascending.

### 5.4 Cross-Navigation (W2 → W4)

BacktestHistory card click handler:
1. Calls `backtest.selectTrade({ symbol, signalDate })` with the clicked trade's identifiers
2. Calls `ui.setWorkspace(4)`
3. W4 loads cached backtest if needed, finds matching trade by symbol+signalDate, highlights row, loads chart

---

## 6. TypeScript Types

New types in `lib/types.ts`:

```typescript
export interface BacktestParams {
  startDate: string
  endDate: string
  exitMode: 'v1_fixed' | 'v2_trail_10d' | 'v2_trail_21ema'
  targetR: number | null
  pendingDays: number
  maxHoldDays: number
  trailActivationR: number
}

export interface BacktestTrade {
  symbol: string
  signalDate: string
  status: 'not_filled' | 'stopped' | 'target' | 'time_stop' | 'incomplete_data' | 'skipped_overlap'
  entryRef: number
  stop: number
  pivot: number | null
  entryDate: string | null
  entryPrice: number | null
  exitDate: string | null
  exitPrice: number | null
  holdingDays: number | null
  returnPct: number | null
  rMultiple: number | null
  maeR: number | null
  mfeR: number | null
  givebackPct: number | null
}

export interface BacktestSummary {
  totalSignals: number
  filledTrades: number
  notFilledSignals: number
  stoppedTrades: number
  targetExits: number
  timeStopExits: number
  winRate: number
  averageReturnPct: number | null
  averageR: number | null
  medianR: number | null
  expectancy: number | null
  payoffRatio: number | null
  cumulativeR: number
  maxDrawdown: number
  longestLosingStreak: number
  avgMfeR: number | null
  avgMaeR: number | null
  avgGivebackPct: number | null
  exitMode: string
}

export interface EquityCurvePoint {
  exitDate: string
  symbol: string
  rMultiple: number
  cumulativeR: number
  runningPeak: number
  drawdown: number
}

export interface BacktestReport {
  params: BacktestParams
  summary: BacktestSummary
  trades: BacktestTrade[]
  equityCurve: EquityCurvePoint[]
}

export interface SymbolSignalHistory {
  signals: Signal[]
  trades: BacktestTrade[]
}
```

---

## 7. File Changes Summary

### New Files

| File | Purpose |
|------|---------|
| `src/sepa_scanner/api/routes/backtest.py` | FastAPI backtest endpoints |
| `sepa-terminal/src/stores/backtest.ts` | Backtest Zustand store |
| `sepa-terminal/src/api/useBacktest.ts` | TanStack Query hooks for backtest |
| `sepa-terminal/src/api/useSignalHistory.ts` | Signal history hook for markers |
| `sepa-terminal/src/lib/chart-markers.ts` | Signal/trade → marker mapper |
| `sepa-terminal/src/lib/case.ts` | snake_case → camelCase recursive mapper |
| `sepa-terminal/src/components/workspaces/Backtest.tsx` | W4 workspace composition |
| `sepa-terminal/src/components/backtest/ParamBar.tsx` | Backtest parameter controls |
| `sepa-terminal/src/components/backtest/StatsSummary.tsx` | Metrics grid |
| `sepa-terminal/src/components/backtest/EquityCurve.tsx` | Cumulative R + drawdown charts |
| `sepa-terminal/src/components/backtest/TradeList.tsx` | Scrollable trade table |
| `sepa-terminal/src/components/analysis/BacktestHistory.tsx` | W2 per-symbol card |

### Modified Files

| File | Change |
|------|--------|
| `sepa-terminal/src/components/chart/PriceChart.tsx` | Add `markers` prop, call `setMarkers()` |
| `sepa-terminal/src/stores/ui.ts` | Extend `activeWorkspace` to `1 \| 2 \| 3 \| 4` |
| `sepa-terminal/src/components/layout/TopBar.tsx` | Add 4th tab |
| `sepa-terminal/src/components/layout/ShortcutBar.tsx` | Add W4 shortcuts |
| `sepa-terminal/src/hooks/useWorkspaceSwitch.ts` | Add `4` hotkey |
| `sepa-terminal/src/components/workspaces/MarketScanner.tsx` | Pass markers to chart |
| `sepa-terminal/src/components/workspaces/Analysis.tsx` | Pass markers to chart, swap SignalHistory → BacktestHistory |
| `sepa-terminal/src/App.tsx` | Add W4 conditional render |
| `sepa-terminal/src/lib/types.ts` | Add backtest TypeScript types |
| `src/sepa_scanner/api/app.py` | Mount backtest router |
| `src/sepa_scanner/api/routes/analysis.py` | Replace `signal-history` endpoint to return signals + trades |

### Deleted Files

| File | Reason |
|------|--------|
| `sepa-terminal/src/components/analysis/SignalHistory.tsx` | Replaced by BacktestHistory |

### Retired Types

| Type | Location | Reason |
|------|----------|--------|
| `SignalHistoryEntry` | `sepa-terminal/src/lib/types.ts` | Replaced by `SymbolSignalHistory` (signals + backtest trades) |

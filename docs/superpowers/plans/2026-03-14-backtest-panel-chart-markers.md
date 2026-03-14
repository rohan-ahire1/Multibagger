# Backtest Panel + Chart Signal Markers — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a W4 Backtest Dashboard, replace SignalHistory with BacktestHistory in W2, and render color-coded signal markers on charts across all workspaces.

**Architecture:** FastAPI endpoints expose the existing Python backtest engine. Frontend consumes via TanStack Query, stores state in Zustand, renders markers via Lightweight Charts v5 `setMarkers()` API. snake_case→camelCase conversion happens in the frontend API layer.

**Tech Stack:** FastAPI, DuckDB, existing backtest engine, React 19, Lightweight Charts v5, Recharts, TanStack Table/Query, Zustand

**Spec:** `docs/superpowers/specs/2026-03-14-backtest-panel-chart-markers-design.md`

---

## File Structure

### New Files (Backend)
| File | Responsibility |
|------|----------------|
| `src/sepa_scanner/api/routes/backtest.py` | FastAPI router: POST /run, GET /last |

### New Files (Frontend)
| File | Responsibility |
|------|----------------|
| `sepa-terminal/src/lib/case.ts` | `snakeToCamel()` recursive key mapper |
| `sepa-terminal/src/lib/chart-markers.ts` | `buildChartMarkers()` — signals+trades → Lightweight Charts markers |
| `sepa-terminal/src/stores/backtest.ts` | Zustand store for backtest params, result, selected trade |
| `sepa-terminal/src/api/useBacktest.ts` | `useBacktestLast()`, `useRunBacktest()` hooks |
| `sepa-terminal/src/api/useSignalHistory.ts` | `useSymbolSignalHistory()` hook |
| `sepa-terminal/src/components/backtest/ParamBar.tsx` | Backtest parameter controls bar |
| `sepa-terminal/src/components/backtest/StatsSummary.tsx` | Backtest metrics grid |
| `sepa-terminal/src/components/backtest/EquityCurve.tsx` | Cumulative R + drawdown Recharts |
| `sepa-terminal/src/components/backtest/TradeList.tsx` | TanStack Table trade list |
| `sepa-terminal/src/components/workspaces/Backtest.tsx` | W4 workspace composition |
| `sepa-terminal/src/components/analysis/BacktestHistory.tsx` | W2 per-symbol backtest card |

### Modified Files
| File | Change |
|------|--------|
| `src/sepa_scanner/api/app.py` | Mount backtest router |
| `src/sepa_scanner/api/routes/analysis.py` | Replace signal-history to return signals+trades |
| `sepa-terminal/src/lib/types.ts` | Add backtest types, remove `SignalHistoryEntry` |
| `sepa-terminal/src/components/chart/PriceChart.tsx` | Add `markers` prop |
| `sepa-terminal/src/stores/ui.ts` | Extend `activeWorkspace` to `1 \| 2 \| 3 \| 4` |
| `sepa-terminal/src/components/layout/TopBar.tsx` | Add 4th tab |
| `sepa-terminal/src/components/layout/ShortcutBar.tsx` | Add W4 shortcuts |
| `sepa-terminal/src/hooks/useWorkspaceSwitch.ts` | Add `4` hotkey |
| `sepa-terminal/src/App.tsx` | Add W4 conditional render |
| `sepa-terminal/src/components/workspaces/Analysis.tsx` | Swap SignalHistory → BacktestHistory, pass markers |
| `sepa-terminal/src/components/workspaces/MarketScanner.tsx` | Pass markers to chart |

### Deleted Files
| File | Reason |
|------|--------|
| `sepa-terminal/src/components/analysis/SignalHistory.tsx` | Replaced by BacktestHistory |

---

## Chunk 1: Backend + Foundation

### Task 1: Backend — Backtest API Router

**Files:**
- Create: `src/sepa_scanner/api/routes/backtest.py`
- Modify: `src/sepa_scanner/api/app.py:17-19`
- Modify: `src/sepa_scanner/api/routes/analysis.py:39-55`

**Context for implementer:**
- The existing backtest engine lives at `src/sepa_scanner/backtest/engine.py` with `simulate_breakout_backtest(signals_df, ohlcv_df, params) -> BacktestResult`
- `BacktestResult.trades` is `list[BacktestTrade]` (dataclass instances) — use this, NOT `BacktestReport.trades` which is a DataFrame
- `summarize_backtest(trades) -> BacktestSummary` and `build_equity_curve(trades) -> pl.DataFrame` are in `src/sepa_scanner/backtest/metrics.py`
- `get_backtest_signals(conn, start_date, end_date)` and `get_backtest_ohlcv(parquet_store, symbols, start, end)` are in `src/sepa_scanner/backtest/queries.py`
- `BacktestParams` does NOT have start_date/end_date — create a separate Pydantic request model
- Dependencies are injected via `src/sepa_scanner/api/deps.py`: `get_settings() -> AppSettings`, `get_duckdb_store() -> DuckDbStore` (read-only), `get_parquet_store() -> ParquetOhlcvStore`
- DuckDB store is read-only — backtest cache goes to filesystem (`data/backtest/latest.json`)
- The existing `analysis.py` router has prefix in app.py. Read `app.py` lines 17-19 to see how routers are mounted.
- Existing `analysis.py` has `_serialize_dates()` helper and `GET /{symbol}/signal-history` endpoint at lines 39-55 that returns `list[dict]` from signals table. Replace this endpoint to also include backtest trades from cached JSON.

- [ ] **Step 1: Create `backtest.py` router**

Create `src/sepa_scanner/api/routes/backtest.py` with:

```python
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sepa_scanner.api.deps import get_duckdb_store, get_parquet_store, get_settings
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.metrics import build_equity_curve, summarize_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.backtest.queries import get_backtest_ohlcv, get_backtest_signals
from sepa_scanner.settings import AppSettings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    start_date: date
    end_date: date
    exit_mode: str = "v2_trail_21ema"
    target_r: float | None = None
    pending_days: int = 5
    max_hold_days: int = 90
    trail_activation_r: float = 1.0


def _cache_path(settings: AppSettings) -> Path:
    return settings.runtime.data_dir / "backtest" / "latest.json"


def _serialize_trade(trade) -> dict:
    d = asdict(trade)
    for key in ("signal_date", "entry_date", "exit_date"):
        val = d.get(key)
        if val is not None:
            d[key] = val.isoformat()
    # Exclude raw price excursion fields — frontend only needs R-multiple versions
    d.pop("mae_price", None)
    d.pop("mfe_price", None)
    return d


@router.post("/run")
def run_backtest(
    req: BacktestRunRequest,
    settings: AppSettings = Depends(get_settings),
    store: DuckDbStore = Depends(get_duckdb_store),
    parquet: ParquetOhlcvStore = Depends(get_parquet_store),
) -> dict:
    params = BacktestParams(
        pending_days=req.pending_days,
        max_hold_days=req.max_hold_days,
        target_r=req.target_r,
        exit_mode=ExitMode(req.exit_mode),
        trail_activation_r=req.trail_activation_r,
    )
    store.initialize()
    conn = store._connect()
    signals_df = get_backtest_signals(conn, req.start_date, req.end_date)
    if signals_df.is_empty():
        raise HTTPException(status_code=404, detail="No breakout signals in date range")

    symbols = signals_df["symbol"].unique().to_list()
    extended_end = req.end_date + timedelta(days=int(req.max_hold_days * 1.5))
    ohlcv_df = get_backtest_ohlcv(parquet, symbols, req.start_date, extended_end)

    result = simulate_breakout_backtest(signals_df, ohlcv_df, params)
    summary = summarize_backtest(result.trades, exit_mode=params.exit_mode)
    equity_curve = build_equity_curve(result.trades)

    trades_json = [_serialize_trade(t) for t in result.trades]
    equity_json = []
    for row in equity_curve.to_dicts():
        row["exit_date"] = row["exit_date"].isoformat() if row.get("exit_date") else None
        equity_json.append(row)

    summary_dict = asdict(summary)
    response = {
        "params": {
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "exit_mode": req.exit_mode,
            "target_r": req.target_r,
            "pending_days": req.pending_days,
            "max_hold_days": req.max_hold_days,
            "trail_activation_r": req.trail_activation_r,
        },
        "summary": summary_dict,
        "trades": trades_json,
        "equity_curve": equity_json,
    }

    cache = _cache_path(settings)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(response, default=str), encoding="utf-8")

    return response


@router.get("/last")
def get_last_backtest(
    settings: AppSettings = Depends(get_settings),
) -> dict:
    cache = _cache_path(settings)
    if not cache.exists():
        raise HTTPException(status_code=404, detail="No cached backtest result")
    return json.loads(cache.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Update `analysis.py` signal-history endpoint**

In `src/sepa_scanner/api/routes/analysis.py`, replace the existing `signal-history` endpoint (around lines 39-55) to also return backtest trades from cached JSON:

The existing endpoint:
```python
@router.get("/{symbol}/signal-history")
def get_signal_history(...):
    # currently returns list[dict] of signals only
```

Replace with:
```python
@router.get("/{symbol}/signal-history")
def get_signal_history(
    symbol: str,
    store: DuckDbStore = Depends(get_duckdb_store),
    settings: AppSettings = Depends(get_settings),
) -> dict:
    store.initialize()
    conn = store._connect()
    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE symbol = ?
        ORDER BY signal_date DESC
        """,
        [symbol],
    ).fetchall()
    columns = [desc[0] for desc in conn.description]
    signals = [_serialize_dates(dict(zip(columns, row, strict=False))) for row in rows]

    # Load backtest trades for this symbol from cache
    cache_path = settings.runtime.data_dir / "backtest" / "latest.json"
    trades: list[dict] = []
    if cache_path.exists():
        import json
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        trades = [t for t in cached.get("trades", []) if t["symbol"] == symbol]

    return {"signals": signals, "trades": trades}
```

Add `get_settings` to the imports from deps if not already there. Add `from sepa_scanner.settings import AppSettings` if needed.

- [ ] **Step 3: Mount backtest router in `app.py`**

In `src/sepa_scanner/api/app.py`, add after the existing router includes:

```python
from sepa_scanner.api.routes import backtest
app.include_router(backtest.router, prefix="/api")
```

- [ ] **Step 4: Verify backend starts**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
uv run python -c "from sepa_scanner.api.app import create_app; app = create_app(); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/sepa_scanner/api/routes/backtest.py src/sepa_scanner/api/app.py src/sepa_scanner/api/routes/analysis.py
git commit -m "feat(api): add backtest run/last endpoints, update signal-history to include trades"
```

---

### Task 2: Frontend Types + Utilities

**Files:**
- Modify: `sepa-terminal/src/lib/types.ts:124-130` (remove `SignalHistoryEntry`, add backtest types)
- Create: `sepa-terminal/src/lib/case.ts`
- Create: `sepa-terminal/src/lib/chart-markers.ts`

**Context for implementer:**
- `types.ts` currently has `SignalHistoryEntry` at lines 124-130 — remove it
- The existing `Signal` interface (around lines 42-60) is used by `buildChartMarkers`
- Lightweight Charts v5 exports `SeriesMarker` and `Time` types from `'lightweight-charts'`
- The marker colors are defined in spec §1.1:
  - Watch: `#f59e0b` (amber), label "W"
  - Breakout: `#22c55e` (green), label "B"
  - Entry: `#3b82f6` (blue), label "E"
  - Win Exit: `#10b981` (teal), label "X+"
  - Loss Exit: `#ef4444` (red), label "X-"
  - Not Filled: `#6b7280` (gray), label "NF"

- [ ] **Step 1: Add backtest types to `types.ts`**

Remove the `SignalHistoryEntry` interface (lines 124-130). Add at the end of the file:

```typescript
// --- Backtest types ---

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

- [ ] **Step 2: Create `lib/case.ts`**

```typescript
export function snakeToCamel(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(snakeToCamel)
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
        snakeToCamel(v),
      ])
    )
  }
  return obj
}
```

- [ ] **Step 3: Create `lib/chart-markers.ts`**

```typescript
import type { SeriesMarker, Time } from 'lightweight-charts'
import type { BacktestTrade, Signal } from './types'

const MARKER_CONFIG = {
  watch: { color: '#f59e0b', text: 'W' },
  breakout: { color: '#22c55e', text: 'B' },
  entry: { color: '#3b82f6', text: 'E' },
  winExit: { color: '#10b981', text: 'X+' },
  lossExit: { color: '#ef4444', text: 'X-' },
  notFilled: { color: '#6b7280', text: 'NF' },
} as const

function marker(time: string, config: { color: string; text: string }): SeriesMarker<Time> {
  return {
    time: time as unknown as Time,
    position: 'belowBar',
    shape: 'arrowUp',
    color: config.color,
    text: config.text,
  }
}

export function buildChartMarkers(
  signals: Signal[],
  trades: BacktestTrade[]
): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = []

  for (const s of signals) {
    if (s.signalType === 'WATCH') {
      markers.push(marker(s.signalDate, MARKER_CONFIG.watch))
    } else if (s.signalType === 'BREAKOUT') {
      markers.push(marker(s.signalDate, MARKER_CONFIG.breakout))
    }
  }

  for (const t of trades) {
    if (t.status === 'not_filled') {
      markers.push(marker(t.signalDate, MARKER_CONFIG.notFilled))
    } else {
      if (t.entryDate) {
        markers.push(marker(t.entryDate, MARKER_CONFIG.entry))
      }
      if (t.exitDate) {
        const isWin = t.rMultiple !== null && t.rMultiple > 0
        markers.push(marker(t.exitDate, isWin ? MARKER_CONFIG.winExit : MARKER_CONFIG.lossExit))
      }
    }
  }

  // Lightweight Charts requires markers sorted by time ascending
  markers.sort((a, b) => (a.time as string).localeCompare(b.time as string))
  return markers
}
```

- [ ] **Step 4: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/lib/types.ts src/lib/case.ts src/lib/chart-markers.ts
git commit -m "feat: add backtest types, case converter, and chart markers mapper"
```

---

### Task 3: Backtest Store + API Hooks

**Files:**
- Create: `sepa-terminal/src/stores/backtest.ts`
- Create: `sepa-terminal/src/api/useBacktest.ts`
- Create: `sepa-terminal/src/api/useSignalHistory.ts`

**Context for implementer:**
- Follow the pattern in `sepa-terminal/src/stores/ui.ts` for Zustand store creation (uses `create` from `zustand`)
- Follow the pattern in `sepa-terminal/src/api/useOHLCV.ts` and `sepa-terminal/src/api/useScannerData.ts` for TanStack Query hooks
- `apiFetch` is in `sepa-terminal/src/api/client.ts` — generic fetch wrapper against `/api` base
- `snakeToCamel` from `lib/case.ts` is used in `select` transforms to convert API response keys
- Backtest store is NOT persisted (results are large, cached server-side)
- Default params: startDate = 1 year ago, endDate = today, exitMode = 'v2_trail_21ema', targetR = null, pendingDays = 5, maxHoldDays = 90, trailActivationR = 1.0

- [ ] **Step 1: Create `stores/backtest.ts`**

```typescript
import { create } from 'zustand'
import type { BacktestParams, BacktestReport } from '@/lib/types'

export interface SelectedTrade {
  symbol: string
  signalDate: string
}

function defaultParams(): BacktestParams {
  const now = new Date()
  const yearAgo = new Date(now)
  yearAgo.setFullYear(yearAgo.getFullYear() - 1)
  return {
    startDate: yearAgo.toISOString().slice(0, 10),
    endDate: now.toISOString().slice(0, 10),
    exitMode: 'v2_trail_21ema',
    targetR: null,
    pendingDays: 5,
    maxHoldDays: 90,
    trailActivationR: 1.0,
  }
}

interface BacktestState {
  params: BacktestParams
  result: BacktestReport | null
  selectedTrade: SelectedTrade | null
  isRunning: boolean
  setParams: (update: Partial<BacktestParams>) => void
  setResult: (report: BacktestReport) => void
  selectTrade: (trade: SelectedTrade | null) => void
  setRunning: (v: boolean) => void
}

export const useBacktestStore = create<BacktestState>((set) => ({
  params: defaultParams(),
  result: null,
  selectedTrade: null,
  isRunning: false,
  setParams: (update) => set((s) => ({ params: { ...s.params, ...update } })),
  setResult: (report) => set({ result: report }),
  selectTrade: (trade) => set({ selectedTrade: trade }),
  setRunning: (v) => set({ isRunning: v }),
}))
```

- [ ] **Step 2: Create `api/useBacktest.ts`**

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { snakeToCamel } from '@/lib/case'
import type { BacktestParams, BacktestReport } from '@/lib/types'

export function useBacktestLast() {
  return useQuery<BacktestReport | null>({
    queryKey: ['backtest', 'last'],
    queryFn: async () => {
      try {
        const raw = await apiFetch<unknown>('/backtest/last')
        return snakeToCamel(raw) as BacktestReport
      } catch {
        return null
      }
    },
    staleTime: Infinity, // only refetch manually
  })
}

export function useRunBacktest() {
  const queryClient = useQueryClient()
  return useMutation<BacktestReport, Error, BacktestParams>({
    mutationFn: async (params) => {
      const body = {
        start_date: params.startDate,
        end_date: params.endDate,
        exit_mode: params.exitMode,
        target_r: params.targetR,
        pending_days: params.pendingDays,
        max_hold_days: params.maxHoldDays,
        trail_activation_r: params.trailActivationR,
      }
      const raw = await apiFetch<unknown>('/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return snakeToCamel(raw) as BacktestReport
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['backtest', 'last'], data)
    },
  })
}
```

- [ ] **Step 3: Create `api/useSignalHistory.ts`**

```typescript
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { snakeToCamel } from '@/lib/case'
import type { SymbolSignalHistory } from '@/lib/types'

export function useSymbolSignalHistory(symbol: string | undefined) {
  return useQuery<SymbolSignalHistory>({
    queryKey: ['signal-history', symbol],
    queryFn: async () => {
      const raw = await apiFetch<unknown>(`/analysis/${symbol}/signal-history`)
      return snakeToCamel(raw) as SymbolSignalHistory
    },
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}
```

- [ ] **Step 4: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/stores/backtest.ts src/api/useBacktest.ts src/api/useSignalHistory.ts
git commit -m "feat: add backtest store and API hooks"
```

---

### Task 4: PriceChart Markers Support

**Files:**
- Modify: `sepa-terminal/src/components/chart/PriceChart.tsx`

**Context for implementer:**
- Read `PriceChart.tsx` fully first. The chart is created in a `useEffect` that depends on `[data, overlays, timeframe, height, entryRef, stop, pivot]`.
- The candlestick series is created with `chart.addSeries(CandlestickSeries, {...})` and stored in a local variable `candleSeries`.
- After `candleSeries.setData(...)` is called (around line 137-145), add a call to `candleSeries.setMarkers(markers)` if markers are provided.
- Add `markers` to the useEffect dependency array.
- The `markers` prop is optional — when undefined, don't call `setMarkers`.
- Import `SeriesMarker` and `Time` from `'lightweight-charts'`.

- [ ] **Step 1: Add markers prop to PriceChart**

Add to the props interface:
```typescript
import type { SeriesMarker, Time } from 'lightweight-charts'

// Add to existing interface:
markers?: SeriesMarker<Time>[]
```

Add `markers` to the component's destructured props.

- [ ] **Step 2: Apply markers after candlestick data is set**

After the line that calls `candleSeries.setData(...)`, add:
```typescript
if (markers && markers.length > 0) {
  candleSeries.setMarkers(markers)
}
```

- [ ] **Step 3: Add `markers` to useEffect dependency array**

The existing dependency array is `[data, overlays, timeframe, height, entryRef, stop, pivot]`. Add `markers` to it.

- [ ] **Step 4: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/components/chart/PriceChart.tsx
git commit -m "feat(chart): add markers prop to PriceChart for signal lifecycle display"
```

---

## Chunk 2: App Shell + W4 Components

### Task 5: App Shell Updates

**Files:**
- Modify: `sepa-terminal/src/stores/ui.ts:6` (activeWorkspace type)
- Modify: `sepa-terminal/src/components/layout/TopBar.tsx:33-51` (add 4th tab)
- Modify: `sepa-terminal/src/components/layout/ShortcutBar.tsx:8-31` (add W4 shortcuts)
- Modify: `sepa-terminal/src/hooks/useWorkspaceSwitch.ts` (add '4' hotkey)
- Modify: `sepa-terminal/src/App.tsx:32-34` (add W4 render)

**Context for implementer:**
- `ui.ts` line 6 has `activeWorkspace: 1 | 2 | 3` — change to `1 | 2 | 3 | 4`
- Also update `setWorkspace` type signature (the method that sets activeWorkspace)
- `TopBar.tsx` lines 33-51 defines 3 workspace objects in an array — add `{ id: 4 as const, label: 'Backtest', key: '4' }`
- `ShortcutBar.tsx` lines 8-31 has shortcut arrays keyed by workspace number — add workspace 4 shortcuts: `J K` (navigate), `R` (run), `Esc` (back), `⌘K` (command)
- `useWorkspaceSwitch.ts` has 3 hotkey bindings — add a 4th for key '4'
- `App.tsx` lines 32-34 conditionally renders workspace components — add case for workspace 4. Import a placeholder `Backtest` component (will be created in Task 7). For now, create a minimal stub that renders `<div>Backtest workspace</div>`.

- [ ] **Step 1: Update `ui.ts` — extend workspace type**

Change line 6 from:
```typescript
activeWorkspace: 1 | 2 | 3
```
to:
```typescript
activeWorkspace: 1 | 2 | 3 | 4
```

Update the `setWorkspace` method type similarly.

- [ ] **Step 2: Update `TopBar.tsx` — add 4th tab**

Find the workspaces array and add:
```typescript
{ id: 4 as const, label: 'Backtest', key: '4' }
```

- [ ] **Step 3: Update `ShortcutBar.tsx` — add W4 shortcuts**

Add to the shortcuts object:
```typescript
4: [
  { keys: 'J K', label: 'navigate' },
  { keys: 'R', label: 'run backtest' },
  { keys: 'Esc', label: 'back' },
  { keys: '⌘K', label: 'command' },
]
```

- [ ] **Step 4: Update `useWorkspaceSwitch.ts` — add '4' hotkey**

Add:
```typescript
useHotkeys('4', () => setWorkspace(4), { preventDefault: true })
```

- [ ] **Step 5: Update `App.tsx` — add W4 placeholder render**

Add import for Backtest component (stub for now). Add to the workspace switch:
```typescript
{activeWorkspace === 4 && <Backtest />}
```

Create a minimal stub `sepa-terminal/src/components/workspaces/Backtest.tsx`:
```typescript
export function Backtest() {
  return (
    <div className="flex-1 flex items-center justify-center text-zinc-500">
      Backtest workspace — loading...
    </div>
  )
}
```

- [ ] **Step 6: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 7: Commit**

```bash
git add src/stores/ui.ts src/components/layout/TopBar.tsx src/components/layout/ShortcutBar.tsx src/hooks/useWorkspaceSwitch.ts src/App.tsx src/components/workspaces/Backtest.tsx
git commit -m "feat: add W4 Backtest tab to app shell with workspace switching"
```

---

### Task 6: W4 Backtest Components — ParamBar, StatsSummary, EquityCurve, TradeList

**Files:**
- Create: `sepa-terminal/src/components/backtest/ParamBar.tsx`
- Create: `sepa-terminal/src/components/backtest/StatsSummary.tsx`
- Create: `sepa-terminal/src/components/backtest/EquityCurve.tsx`
- Create: `sepa-terminal/src/components/backtest/TradeList.tsx`

**Context for implementer:**
- These are 4 independent child components used by the W4 Backtest workspace
- Follow existing component patterns in the codebase — functional components, Tailwind classes, dark theme (#0F1117 bg, zinc color palette)
- Use `@/lib/formatters` for `formatPrice`, `formatPct`, `formatR`, `formatDate` (already exist — read `sepa-terminal/src/lib/formatters.ts`)
- Use `@/components/shared/StatusPill` for trade status pills (already exists — read `sepa-terminal/src/components/shared/StatusPill.tsx` for variant names)
- Use `@/components/shared/MetricCard` for stats (already exists)
- Colors reference: `@/lib/colors` has the COLORS object and helper functions
- Recharts is already a dependency (used by RSPane.tsx and VolumePane.tsx)
- TanStack Table is already a dependency (used by ScannerTable.tsx — read it for the pattern)
- `useBacktestStore` provides `params`, `setParams`, `selectedTrade`, `selectTrade`

- [ ] **Step 1: Create `ParamBar.tsx`**

Parameter controls bar across the top of W4. Controls: start date, end date, exit mode dropdown, target R, pending days, max hold days, trail activation R, Run button.

```typescript
import { useBacktestStore } from '@/stores/backtest'
import { useRunBacktest } from '@/api/useBacktest'

export function ParamBar() {
  const { params, setParams, setResult, isRunning, setRunning } = useBacktestStore()
  const mutation = useRunBacktest()

  const handleRun = () => {
    setRunning(true)
    mutation.mutate(params, {
      onSuccess: (data) => {
        setResult(data)
        setRunning(false)
      },
      onError: () => setRunning(false),
    })
  }

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-zinc-900/50 flex-wrap">
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        From
        <input
          type="date"
          value={params.startDate}
          onChange={(e) => setParams({ startDate: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200"
        />
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        To
        <input
          type="date"
          value={params.endDate}
          onChange={(e) => setParams({ endDate: e.target.value })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200"
        />
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        Exit
        <select
          value={params.exitMode}
          onChange={(e) => setParams({ exitMode: e.target.value as BacktestParams['exitMode'] })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200"
        >
          <option value="v2_trail_21ema">Trail 21 EMA</option>
          <option value="v2_trail_10d">Trail 10D Low</option>
          <option value="v1_fixed">Fixed Stop</option>
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        Target R
        <input
          type="number"
          step="0.5"
          value={params.targetR ?? ''}
          onChange={(e) => setParams({ targetR: e.target.value ? Number(e.target.value) : null })}
          placeholder="—"
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 w-16"
        />
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        Pending
        <input
          type="number"
          value={params.pendingDays}
          onChange={(e) => setParams({ pendingDays: Number(e.target.value) })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 w-14"
        />
      </label>
      <label className="flex items-center gap-1 text-xs text-zinc-400">
        Max Hold
        <input
          type="number"
          value={params.maxHoldDays}
          onChange={(e) => setParams({ maxHoldDays: Number(e.target.value) })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 w-14"
        />
      </label>
      <button
        onClick={handleRun}
        disabled={isRunning}
        className="ml-auto px-4 py-1.5 rounded text-xs font-medium bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isRunning ? 'Running...' : '▶ Run Backtest'}
      </button>
    </div>
  )
}
```

Note: add `import type { BacktestParams } from '@/lib/types'` for the type cast.

- [ ] **Step 2: Create `StatsSummary.tsx`**

Compact grid of backtest metrics.

```typescript
import type { BacktestSummary } from '@/lib/types'
import { formatPct, formatR } from '@/lib/formatters'

interface Props {
  summary: BacktestSummary
}

export function StatsSummary({ summary }: Props) {
  const metrics = [
    { label: 'Signals', value: summary.totalSignals },
    { label: 'Filled', value: summary.filledTrades },
    { label: 'Win Rate', value: formatPct(summary.winRate), color: summary.winRate > 0.4 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Avg R', value: formatR(summary.averageR), color: (summary.averageR ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Cum R', value: formatR(summary.cumulativeR), color: summary.cumulativeR > 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Max DD', value: formatR(summary.maxDrawdown), color: 'text-red-400' },
    { label: 'Payoff', value: summary.payoffRatio?.toFixed(1) ?? '—' },
    { label: 'Lose Streak', value: summary.longestLosingStreak },
    { label: 'Avg MFE', value: formatR(summary.avgMfeR) },
    { label: 'Avg MAE', value: formatR(summary.avgMaeR) },
    { label: 'Giveback', value: summary.avgGivebackPct != null ? formatPct(summary.avgGivebackPct) : '—' },
  ]

  return (
    <div className="grid grid-cols-3 gap-2 p-3">
      {metrics.map((m) => (
        <div key={m.label} className="text-center">
          <div className="text-[10px] text-zinc-500 uppercase">{m.label}</div>
          <div className={`text-sm font-mono ${m.color ?? 'text-zinc-200'}`}>{m.value}</div>
        </div>
      ))}
    </div>
  )
}
```

Note: `formatR` may not exist yet in formatters.ts — check. If not, it should format as `+1.2R` / `-0.5R`. The `formatPct` already exists. If `formatR` doesn't exist, create it inline or add to formatters.

- [ ] **Step 3: Create `EquityCurve.tsx`**

Two stacked Recharts AreaCharts.

```typescript
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { EquityCurvePoint } from '@/lib/types'

interface Props {
  data: EquityCurvePoint[]
}

export function EquityCurve({ data }: Props) {
  if (data.length === 0) return null

  return (
    <div className="flex flex-col gap-1 p-2">
      {/* Cumulative R */}
      <div className="text-[10px] text-zinc-500 uppercase px-1">Equity Curve (Cumulative R)</div>
      <ResponsiveContainer width="100%" height={120}>
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <XAxis dataKey="exitDate" hide />
          <YAxis width={40} tick={{ fontSize: 10, fill: '#71717a' }} />
          <Tooltip
            contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', fontSize: 11 }}
            labelStyle={{ color: '#a1a1aa' }}
          />
          <Area
            type="monotone"
            dataKey="cumulativeR"
            stroke="#22c55e"
            fill="#22c55e"
            fillOpacity={0.15}
          />
        </AreaChart>
      </ResponsiveContainer>

      {/* Drawdown */}
      <div className="text-[10px] text-zinc-500 uppercase px-1">Drawdown</div>
      <ResponsiveContainer width="100%" height={60}>
        <AreaChart data={data} margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
          <XAxis dataKey="exitDate" hide />
          <YAxis width={40} tick={{ fontSize: 10, fill: '#71717a' }} />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="#ef4444"
            fill="#ef4444"
            fillOpacity={0.2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 4: Create `TradeList.tsx`**

Scrollable TanStack Table of trades.

```typescript
import { useMemo, useEffect } from 'react'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortingRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import type { BacktestTrade } from '@/lib/types'
import { useBacktestStore, type SelectedTrade } from '@/stores/backtest'
import { formatPrice } from '@/lib/formatters'
import { StatusPill } from '@/components/shared/StatusPill'

const col = createColumnHelper<BacktestTrade>()

const STATUS_VARIANT: Record<string, 'green' | 'red' | 'amber' | 'gray' | 'blue'> = {
  target: 'green',
  stopped: 'red',
  time_stop: 'amber',
  not_filled: 'gray',
  incomplete_data: 'gray',
  skipped_overlap: 'gray',
}

const columns = [
  col.accessor('symbol', { header: 'Symbol', size: 80 }),
  col.accessor('signalDate', { header: 'Signal', size: 90 }),
  col.accessor('entryPrice', {
    header: 'Entry',
    size: 70,
    cell: (info) => info.getValue() != null ? formatPrice(info.getValue()!) : '—',
  }),
  col.accessor('exitPrice', {
    header: 'Exit',
    size: 70,
    cell: (info) => info.getValue() != null ? formatPrice(info.getValue()!) : '—',
  }),
  col.accessor('rMultiple', {
    header: 'R',
    size: 50,
    cell: (info) => {
      const v = info.getValue()
      if (v == null) return '—'
      const color = v > 0 ? 'text-emerald-400' : 'text-red-400'
      return <span className={color}>{v > 0 ? '+' : ''}{v.toFixed(1)}R</span>
    },
  }),
  col.accessor('status', {
    header: 'Status',
    size: 70,
    cell: (info) => {
      const status = info.getValue()
      return <StatusPill variant={STATUS_VARIANT[status] ?? 'gray'} label={status.replace('_', ' ')} />
    },
  }),
]

interface Props {
  trades: BacktestTrade[]
}

export function TradeList({ trades }: Props) {
  const { selectedTrade, selectTrade } = useBacktestStore()
  const [sorting, setSorting] = useState<SortingState>([{ id: 'signalDate', desc: true }])

  const table = useReactTable({
    data: trades,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortingRowModel: getSortingRowModel(),
  })

  const isSelected = (trade: BacktestTrade) =>
    selectedTrade?.symbol === trade.symbol && selectedTrade?.signalDate === trade.signalDate

  return (
    <div className="overflow-auto flex-1">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-zinc-900 z-10">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  onClick={h.column.getToggleSortingHandler()}
                  className="text-left px-2 py-1.5 text-zinc-500 font-medium cursor-pointer hover:text-zinc-300"
                  style={{ width: h.getSize() }}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {h.column.getIsSorted() === 'asc' ? ' ↑' : h.column.getIsSorted() === 'desc' ? ' ↓' : ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              onClick={() => selectTrade({ symbol: row.original.symbol, signalDate: row.original.signalDate })}
              className={`cursor-pointer border-b border-zinc-800/50 hover:bg-zinc-800/50 ${
                isSelected(row.original) ? 'bg-zinc-800' : ''
              }`}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-2 py-1.5 text-zinc-300">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 5: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add src/components/backtest/
git commit -m "feat: add W4 backtest child components — ParamBar, StatsSummary, EquityCurve, TradeList"
```

---

### Task 7: W4 Backtest Workspace Composition

**Files:**
- Modify: `sepa-terminal/src/components/workspaces/Backtest.tsx` (replace stub)

**Context for implementer:**
- Replace the stub created in Task 5 with the full workspace
- Layout: ParamBar across top, then master-detail split with react-resizable-panels
- Left column (~40%): StatsSummary at top, TradeList below
- Right column (~60%): EquityCurve at top, PriceChart with markers below
- On mount, call `useBacktestLast()` to load cached result. Set it into the backtest store.
- When `selectedTrade` changes, load OHLCV for that symbol and signal history for markers
- Uses `react-resizable-panels` — in this codebase v4.7.2 uses `Group` / `Panel` / `Separator` (NOT `PanelGroup` / `PanelResizeHandle`). Use `orientation="horizontal"` for left/right split.
- Keyboard shortcuts: J/K for trade list nav, R to focus run button, Escape to go back
- When no result loaded and no cache: show empty state "No backtest results. Configure parameters and click Run Backtest."
- Read existing workspaces (`MarketScanner.tsx`, `Analysis.tsx`) for the panel pattern.

- [ ] **Step 1: Implement full Backtest workspace**

Replace the stub with the full composition:

```typescript
import { useEffect, useMemo, useRef } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
// NOTE: check actual imports — if v4.7.2 uses Group/Separator, use those instead
import { useHotkeys } from 'react-hotkeys-hook'
import { useBacktestStore } from '@/stores/backtest'
import { useBacktestLast } from '@/api/useBacktest'
import { useOHLCV } from '@/api/useOHLCV'
import { useSymbolSignalHistory } from '@/api/useSignalHistory'
import { buildChartMarkers } from '@/lib/chart-markers'
import { useUIStore } from '@/stores/ui'
import { ParamBar } from '@/components/backtest/ParamBar'
import { StatsSummary } from '@/components/backtest/StatsSummary'
import { EquityCurve } from '@/components/backtest/EquityCurve'
import { TradeList } from '@/components/backtest/TradeList'
import { PriceChart } from '@/components/chart/PriceChart'
import { ChartToolbar } from '@/components/chart/ChartToolbar'

export function Backtest() {
  const { result, setResult, selectedTrade, selectTrade } = useBacktestStore()
  const { chartTimeframe, chartOverlays } = useUIStore()
  const { data: cachedResult } = useBacktestLast()

  // Load cached result on mount
  useEffect(() => {
    if (cachedResult && !result) {
      setResult(cachedResult)
    }
  }, [cachedResult, result, setResult])

  const trades = result?.trades ?? []

  // Find the selected trade object
  const activeTrade = useMemo(
    () =>
      selectedTrade
        ? trades.find((t) => t.symbol === selectedTrade.symbol && t.signalDate === selectedTrade.signalDate)
        : trades[0] ?? null,
    [trades, selectedTrade]
  )

  // Auto-select first trade if none selected
  useEffect(() => {
    if (trades.length > 0 && !selectedTrade) {
      selectTrade({ symbol: trades[0].symbol, signalDate: trades[0].signalDate })
    }
  }, [trades, selectedTrade, selectTrade])

  // Fetch OHLCV and signal history for selected trade's symbol
  const { data: ohlcvData } = useOHLCV(activeTrade?.symbol)
  const { data: signalHistory } = useSymbolSignalHistory(activeTrade?.symbol)

  const markers = useMemo(
    () => signalHistory ? buildChartMarkers(signalHistory.signals, signalHistory.trades) : [],
    [signalHistory]
  )

  // Keyboard nav
  const runBtnRef = useRef<HTMLButtonElement>(null)
  useHotkeys('j', () => {
    if (!trades.length) return
    const idx = trades.findIndex((t) => t.symbol === selectedTrade?.symbol && t.signalDate === selectedTrade?.signalDate)
    const next = Math.min(idx + 1, trades.length - 1)
    selectTrade({ symbol: trades[next].symbol, signalDate: trades[next].signalDate })
  }, { preventDefault: true })
  useHotkeys('k', () => {
    if (!trades.length) return
    const idx = trades.findIndex((t) => t.symbol === selectedTrade?.symbol && t.signalDate === selectedTrade?.signalDate)
    const prev = Math.max(idx - 1, 0)
    selectTrade({ symbol: trades[prev].symbol, signalDate: trades[prev].signalDate })
  }, { preventDefault: true })
  useHotkeys('r', () => runBtnRef.current?.click(), { preventDefault: true })

  if (!result) {
    return (
      <div className="flex-1 flex flex-col">
        <ParamBar />
        <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
          No backtest results. Configure parameters and click Run Backtest.
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <ParamBar />
      {/* Use the correct react-resizable-panels API — check MarketScanner.tsx for actual import names */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left column — 40% */}
        <div className="w-[40%] flex flex-col border-r border-zinc-800 overflow-hidden">
          <StatsSummary summary={result.summary} />
          <div className="border-t border-zinc-800" />
          <TradeList trades={trades} />
        </div>
        {/* Right column — 60% */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <EquityCurve data={result.equityCurve} />
          <div className="border-t border-zinc-800" />
          {activeTrade && ohlcvData ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              <div className="px-3 py-1.5 flex items-center gap-2 text-sm border-b border-zinc-800">
                <span className="font-medium text-zinc-200">{activeTrade.symbol}</span>
                <span className="text-zinc-500">{activeTrade.signalDate}</span>
                {activeTrade.rMultiple != null && (
                  <span className={activeTrade.rMultiple > 0 ? 'text-emerald-400' : 'text-red-400'}>
                    {activeTrade.rMultiple > 0 ? '+' : ''}{activeTrade.rMultiple.toFixed(1)}R
                  </span>
                )}
              </div>
              <ChartToolbar />
              <div className="flex-1">
                <PriceChart
                  data={ohlcvData}
                  entryRef={activeTrade.entryRef}
                  stop={activeTrade.stop}
                  pivot={activeTrade.pivot ?? undefined}
                  overlays={chartOverlays}
                  timeframe={chartTimeframe}
                  markers={markers}
                />
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
              Select a trade to view chart
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

**IMPORTANT:** Check how `react-resizable-panels` is imported in `MarketScanner.tsx` — the API might use `Group`/`Separator` instead of `PanelGroup`/`PanelResizeHandle`. If so, you may want to use a simpler flex-based layout (as shown above with `w-[40%]` and `flex-1`) to avoid API confusion. The simpler flex layout works fine for a fixed 40/60 split.

- [ ] **Step 2: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add src/components/workspaces/Backtest.tsx
git commit -m "feat: implement W4 Backtest workspace with master-detail layout"
```

---

## Chunk 3: W2 Integration + Chart Markers Wiring

### Task 8: W2 BacktestHistory Card + Wire Markers

**Files:**
- Create: `sepa-terminal/src/components/analysis/BacktestHistory.tsx`
- Modify: `sepa-terminal/src/components/workspaces/Analysis.tsx:22,47,230`
- Delete: `sepa-terminal/src/components/analysis/SignalHistory.tsx`
- Modify: `sepa-terminal/src/components/workspaces/MarketScanner.tsx`

**Context for implementer:**
- Read `Analysis.tsx` fully. Line 22 imports `SignalHistory`, line 47 generates mock data with `generateMockSignalHistory()`, line 230 renders `<SignalHistory entries={signalHistory} />`
- Replace all three: remove the import, remove the mock data generation, replace the JSX with `<BacktestHistory symbol={selectedSymbol} />`
- The `BacktestHistory` component receives just the `symbol` prop — it fetches its own data via `useSymbolSignalHistory(symbol)` and reads backtest result from store
- Also in Analysis.tsx: pass `markers` prop to the `PriceChart` component. The chart is rendered in the Analysis component — find where `<PriceChart` is rendered and add the markers prop
- For MarketScanner.tsx: The chart is rendered inside `SetupDetailCard` component. Read `SetupDetailCard.tsx` to understand the prop interface. The simplest approach: pass markers down from MarketScanner → SetupDetailCard → PriceChart. Add `useSymbolSignalHistory` in MarketScanner and pass markers through.
- `mock-data.ts` has `generateMockSignalHistory` — after removing usage, also clean up the export if it's the only consumer. (Check if anything else uses it.)
- Cross-navigation: clicking a trade row calls `backtest.selectTrade(...)` and `ui.setWorkspace(4)`

- [ ] **Step 1: Create `BacktestHistory.tsx`**

```typescript
import { useMemo } from 'react'
import { useSymbolSignalHistory } from '@/api/useSignalHistory'
import { useBacktestStore } from '@/stores/backtest'
import { useUIStore } from '@/stores/ui'
import { formatPrice, formatDate } from '@/lib/formatters'
import { StatusPill } from '@/components/shared/StatusPill'
import type { BacktestTrade } from '@/lib/types'

const STATUS_VARIANT: Record<string, 'green' | 'red' | 'amber' | 'gray'> = {
  target: 'green',
  stopped: 'red',
  time_stop: 'amber',
  not_filled: 'gray',
}

interface Props {
  symbol: string | undefined
}

export function BacktestHistory({ symbol }: Props) {
  const { data, isLoading } = useSymbolSignalHistory(symbol)
  const { selectTrade } = useBacktestStore()
  const { setWorkspace } = useUIStore()

  const trades = data?.trades ?? []
  const signals = data?.signals ?? []

  const summary = useMemo(() => {
    const closed = trades.filter((t) => t.rMultiple != null)
    const wins = closed.filter((t) => t.rMultiple! > 0).length
    const losses = closed.length - wins
    const avgR = closed.length > 0
      ? closed.reduce((sum, t) => sum + (t.rMultiple ?? 0), 0) / closed.length
      : null
    const winRate = closed.length > 0 ? wins / closed.length : null
    return { wins, losses, avgR, winRate }
  }, [trades])

  const lastSignal = signals[0] // signals are returned DESC from API

  const handleTradeClick = (trade: BacktestTrade) => {
    selectTrade({ symbol: trade.symbol, signalDate: trade.signalDate })
    setWorkspace(4)
  }

  if (!symbol) return null

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
      <h3 className="text-xs font-medium text-zinc-400 mb-2">Backtest History</h3>

      {isLoading ? (
        <div className="text-xs text-zinc-500">Loading...</div>
      ) : (
        <>
          {/* Summary line */}
          {trades.length > 0 ? (
            <div className="text-xs text-zinc-300 mb-2 font-mono">
              {summary.wins}W / {summary.losses}L
              {summary.winRate != null && <> — WR {(summary.winRate * 100).toFixed(0)}%</>}
              {summary.avgR != null && (
                <span className={summary.avgR > 0 ? ' text-emerald-400' : ' text-red-400'}>
                  {' '}— Avg {summary.avgR > 0 ? '+' : ''}{summary.avgR.toFixed(1)}R
                </span>
              )}
            </div>
          ) : (
            <div className="text-xs text-zinc-500 mb-2">No backtest trades for this symbol</div>
          )}

          {/* Trades table */}
          {trades.length > 0 && (
            <div className="max-h-[200px] overflow-auto mb-2">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-zinc-500">
                    <th className="text-left py-1">Signal</th>
                    <th className="text-right py-1">Entry</th>
                    <th className="text-right py-1">Exit</th>
                    <th className="text-right py-1">R</th>
                    <th className="text-right py-1">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.slice(0, 10).map((t, i) => (
                    <tr
                      key={i}
                      onClick={() => handleTradeClick(t)}
                      className="cursor-pointer hover:bg-zinc-800/50 border-b border-zinc-800/30"
                    >
                      <td className="py-1 text-zinc-400">{t.signalDate}</td>
                      <td className="py-1 text-right text-zinc-300">
                        {t.entryPrice != null ? formatPrice(t.entryPrice) : '—'}
                      </td>
                      <td className="py-1 text-right text-zinc-300">
                        {t.exitPrice != null ? formatPrice(t.exitPrice) : '—'}
                      </td>
                      <td className="py-1 text-right">
                        {t.rMultiple != null ? (
                          <span className={t.rMultiple > 0 ? 'text-emerald-400' : 'text-red-400'}>
                            {t.rMultiple > 0 ? '+' : ''}{t.rMultiple.toFixed(1)}R
                          </span>
                        ) : '—'}
                      </td>
                      <td className="py-1 text-right">
                        <StatusPill variant={STATUS_VARIANT[t.status] ?? 'gray'} label={t.status.replace('_', ' ')} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Last signal */}
          {lastSignal && (
            <div className="text-[10px] text-zinc-500">
              Last: {lastSignal.signalType} on {lastSignal.signalDate}
            </div>
          )}

          {trades.length === 0 && signals.length > 0 && (
            <div className="text-[10px] text-zinc-500 italic">
              (run backtest to see trade details)
            </div>
          )}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Update `Analysis.tsx` — swap SignalHistory for BacktestHistory + add markers**

In `Analysis.tsx`:

1. Remove import: `import { SignalHistory } from '@/components/analysis/SignalHistory'`
2. Add imports:
   ```typescript
   import { BacktestHistory } from '@/components/analysis/BacktestHistory'
   import { useSymbolSignalHistory } from '@/api/useSignalHistory'
   import { buildChartMarkers } from '@/lib/chart-markers'
   ```
3. Remove the mock data line: `const signalHistory = generateMockSignalHistory()` (line 47 area)
4. Add signal history hook and markers computation:
   ```typescript
   const { data: signalHistory } = useSymbolSignalHistory(selectedSymbol)
   const markers = useMemo(
     () => signalHistory ? buildChartMarkers(signalHistory.signals, signalHistory.trades) : [],
     [signalHistory]
   )
   ```
   (Add `useMemo` to React imports if not there)
5. Replace `<SignalHistory entries={signalHistory} />` (line 230) with:
   ```typescript
   <BacktestHistory symbol={selectedSymbol} />
   ```
6. Find the `<PriceChart` JSX and add `markers={markers}` prop
7. Remove `generateMockSignalHistory` import from `@/lib/mock-data` if present

- [ ] **Step 3: Update `MarketScanner.tsx` — pass markers to chart**

In `MarketScanner.tsx`:

1. Add imports:
   ```typescript
   import { useSymbolSignalHistory } from '@/api/useSignalHistory'
   import { buildChartMarkers } from '@/lib/chart-markers'
   ```
2. After `useOHLCV(selectedSymbol)` call, add:
   ```typescript
   const { data: signalHistory } = useSymbolSignalHistory(selectedSymbol)
   const markers = useMemo(
     () => signalHistory ? buildChartMarkers(signalHistory.signals, signalHistory.trades) : [],
     [signalHistory]
   )
   ```
   (Add `useMemo` to React imports if needed)
3. Find where `SetupDetailCard` or `PriceChart` is rendered and pass `markers` prop down. If the chart is inside `SetupDetailCard`, you'll need to thread the prop through. Read `SetupDetailCard.tsx` to see how the chart is rendered and add a `markers` prop to its interface, passing it through to `PriceChart`.

- [ ] **Step 4: Delete `SignalHistory.tsx`**

```bash
rm sepa-terminal/src/components/analysis/SignalHistory.tsx
```

Also check if `SignalHistoryEntry` type in `types.ts` was already removed in Task 2. If `generateMockSignalHistory` in `mock-data.ts` is no longer used anywhere, remove that export too.

- [ ] **Step 5: Verify build**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npx tsc --noEmit
```

- [ ] **Step 6: Verify dev server starts and renders**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npm run dev
```

Open http://localhost:5173 and verify:
- All 4 workspace tabs appear in TopBar
- Pressing `4` switches to Backtest workspace
- W4 shows empty state with ParamBar
- W2 Analysis shows BacktestHistory card (may show "No backtest trades" if no cache exists)
- No console errors

- [ ] **Step 7: Commit**

```bash
git add -A sepa-terminal/src/components/analysis/ sepa-terminal/src/components/workspaces/Analysis.tsx sepa-terminal/src/components/workspaces/MarketScanner.tsx
git commit -m "feat: add BacktestHistory card in W2, wire signal markers into all charts"
```

---

## Task Dependencies

```
Task 1 (Backend API) ─────────────────────────────────────┐
Task 2 (Types + Utils) ───┬── Task 3 (Store + Hooks) ──┐  │
                           ├── Task 4 (Chart Markers)────┤  │
                           │                              │  │
                           └── Task 5 (App Shell) ────── Task 7 (W4 Workspace)
                                                          │
                                                    Task 6 (W4 Components) ──── Task 7
                                                          │
                                                    Task 8 (W2 + Wiring)
```

**Parallelizable batches:**
- Batch 1: Task 1 (backend) + Task 2 (types/utils) — fully independent
- Batch 2: Task 3 (store/hooks) + Task 4 (chart markers) + Task 5 (app shell) — all depend only on Task 2
- Batch 3: Task 6 (W4 components) — depends on Task 3
- Batch 4: Task 7 (W4 composition) + Task 8 (W2 + wiring) — depend on Tasks 3-6

# SEPA Terminal v2 — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a desktop-first React trading workstation that replaces the existing Streamlit UI, serving OHLCV + signal data from the existing Python backend via FastAPI.

**Architecture:** Vite + React 19 SPA with 3 workspace views (Market+Scanner, Analysis, Portfolio). FastAPI wraps the existing `sepa_scanner` Python package for OHLCV and signal data. Market context, fundamentals, and portfolio are mock/frontend-only for now. TanStack Table for data grids, TradingView Lightweight Charts for candlestick charts, Zustand for client state.

**Tech Stack:** React 19, TypeScript 5, Vite 6, Tailwind CSS 4, shadcn/ui, TradingView Lightweight Charts 5, TanStack Table 8, Recharts 2, react-resizable-panels, cmdk, react-hotkeys-hook, Zustand 5, TanStack Query 5, React Router 7, FastAPI, DuckDB, Parquet

**Reference spec:** `docs/UI_BIBLE.md` (full design spec with colors, layouts, component details)

---

## File Structure

### Frontend (`sepa-terminal/`)

```
sepa-terminal/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
├── src/
│   ├── main.tsx                         # Entry: React root + providers
│   ├── App.tsx                          # Router + workspace layout
│   ├── styles/
│   │   └── globals.css                  # Tailwind + dark theme CSS vars
│   ├── lib/
│   │   ├── types.ts                     # All TypeScript interfaces
│   │   ├── formatters.ts                # INR, price, %, R-multiple formatters
│   │   ├── colors.ts                    # Semantic color constants
│   │   ├── mock-data.ts                 # Mock generators for market context
│   │   └── cn.ts                        # clsx + tailwind-merge utility
│   ├── stores/
│   │   ├── ui.ts                        # Workspace, chart, panel prefs
│   │   ├── scanner.ts                   # Queue, selection, filters
│   │   ├── reviews.ts                   # Shortlist/reject/watch + undo
│   │   ├── portfolio.ts                 # Positions, lifecycle, P&L
│   │   └── market.ts                    # Regime, breadth, sectors
│   ├── api/
│   │   ├── client.ts                    # Base fetch + FastAPI URL
│   │   ├── useOHLCV.ts                  # OHLCV chart data query
│   │   ├── useScannerData.ts            # Signals + watchlist queries
│   │   └── useMarketData.ts             # Market context (mock for now)
│   ├── hooks/
│   │   ├── useKeyboardTriage.ts         # J/K/S/X/W shortcuts
│   │   ├── useWorkspaceSwitch.ts        # 1/2/3 workspace hotkeys
│   │   └── useLinkedSymbol.ts           # Cross-panel symbol linking
│   ├── components/
│   │   ├── layout/
│   │   │   ├── TopBar.tsx               # App header + workspace tabs
│   │   │   ├── RegimeStrip.tsx          # Market regime indicator bar
│   │   │   └── ShortcutBar.tsx          # Bottom context-aware hints
│   │   ├── market/
│   │   │   ├── SectorHeatmap.tsx        # Treemap sector performance
│   │   │   └── BreadthPanel.tsx         # Breadth gauges + FII/DII
│   │   ├── scanner/
│   │   │   ├── QueueTabs.tsx            # 4 queue tabs with counts
│   │   │   ├── ScannerTable.tsx         # TanStack Table setup list
│   │   │   └── SetupDetailCard.tsx      # Right-panel detail view
│   │   ├── chart/
│   │   │   ├── PriceChart.tsx           # Lightweight Charts wrapper
│   │   │   ├── ChartToolbar.tsx         # Timeframe + overlay toggles
│   │   │   ├── RSPane.tsx               # RS line sub-chart
│   │   │   └── VolumePane.tsx           # Volume bars sub-chart
│   │   ├── analysis/
│   │   │   ├── TradePlanCard.tsx        # Entry/stop/risk grid
│   │   │   ├── TrendTemplate.tsx        # 9-condition checklist
│   │   │   ├── VCPMetrics.tsx           # Contraction details
│   │   │   ├── SectorContext.tsx        # Sector RS + flows (mock)
│   │   │   ├── Fundamentals.tsx         # P/E, growth, ROE (mock)
│   │   │   ├── SignalHistory.tsx        # Past signals + win rate
│   │   │   └── TradeThesis.tsx          # Editable thesis textarea
│   │   ├── portfolio/
│   │   │   ├── PortfolioStats.tsx       # 6-metric strip
│   │   │   ├── HeatGauge.tsx            # Risk thermometer
│   │   │   ├── KanbanBoard.tsx          # 5-column pipeline
│   │   │   └── KanbanCard.tsx           # Trade lifecycle card
│   │   ├── shared/
│   │   │   ├── CommandPalette.tsx        # Cmd+K global search
│   │   │   ├── MetricCard.tsx           # Reusable stat card
│   │   │   ├── StatusPill.tsx           # BREAK/WATCH/Stage2 pills
│   │   │   └── QualificationPanel.tsx   # Green pass / amber warn
│   │   └── workspaces/
│   │       ├── MarketScanner.tsx        # Workspace 1 composition
│   │       ├── Analysis.tsx             # Workspace 2 composition
│   │       └── Portfolio.tsx            # Workspace 3 composition
│   └── (shadcn components installed via npx shadcn@latest add)
```

### Backend (`src/sepa_scanner/api/`)

```
src/sepa_scanner/api/
├── __init__.py
├── app.py                # FastAPI app, CORS, lifespan
├── deps.py               # Dependency injection (DuckDB conn, ParquetStore)
├── routes/
│   ├── __init__.py
│   ├── ohlcv.py          # GET /api/ohlcv/{symbol}
│   ├── scanner.py         # GET /api/scanner/signals, /queues
│   ├── analysis.py        # GET /api/analysis/{symbol}/trend-template, /vcp, /signal-history
│   └── backtest.py        # POST /api/backtest/run
└── schemas.py             # Pydantic response models
```

---

## Batch 1: Project Scaffold + Design System

### Task 1.1: Initialize Vite + React + TypeScript project

**Files:**
- Create: `sepa-terminal/` (entire scaffold)
- Create: `sepa-terminal/src/styles/globals.css`
- Create: `sepa-terminal/src/lib/cn.ts`

- [ ] **Step 1: Create Vite project and install all dependencies**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
npm create vite@latest sepa-terminal -- --template react-ts
cd sepa-terminal
npm install
npm install tailwindcss @tailwindcss/vite
npm install lightweight-charts
npm install @tanstack/react-table @tanstack/react-query
npm install recharts
npm install react-resizable-panels
npm install cmdk
npm install react-hotkeys-hook
npm install zustand
npm install react-router
npm install lucide-react
npm install clsx tailwind-merge
```

- [ ] **Step 2: Configure Tailwind in vite.config.ts**

```typescript
// sepa-terminal/vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
```

- [ ] **Step 3: Write globals.css with dark theme tokens**

```css
/* sepa-terminal/src/styles/globals.css */
@import "tailwindcss";

@theme {
  --color-bg-primary: #0F1117;
  --color-bg-secondary: #13151D;
  --color-bg-surface: #1A1D27;
  --color-bg-elevated: #252830;

  --color-text-primary: #E8EAED;
  --color-text-secondary: #C0C2CC;
  --color-text-muted: #8B8D97;
  --color-text-dim: #6B6D77;
  --color-text-faint: #4A4C56;

  --color-border-default: #1E2030;
  --color-border-subtle: #252830;
  --color-border-strong: #2A2D3A;
  --color-border-hover: #3A3D4A;

  --color-green: #26A69A;
  --color-green-bg: #26A69A20;
  --color-red: #EF5350;
  --color-red-bg: #EF535020;
  --color-amber: #F5A623;
  --color-amber-bg: #F5A62320;
  --color-blue: #3B82F6;
  --color-blue-bg: #3B82F620;
  --color-purple: #8B5CF6;
  --color-purple-bg: #8B5CF620;
}

:root {
  --candle-bull: rgba(38,166,154,0.9);
  --candle-bear: rgba(239,83,80,0.9);
  --sma-50: #e8b03e;
  --sma-150: #3b82f6;
  --sma-200: #ef4444;
  --line-pivot: #2563eb;
  --line-entry: #16a34a;
  --line-stop: #dc2626;
  --line-exit: #b45309;
  --line-extended: #f59e0b;
}

@font-face {
  font-family: 'Inter';
  src: url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500&display=swap');
}

body {
  @apply bg-bg-primary text-text-primary;
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 13px;
  -webkit-font-smoothing: antialiased;
}

/* Tabular nums for all numeric content */
.tabular-nums {
  font-variant-numeric: tabular-nums lining-nums;
}
```

- [ ] **Step 4: Create cn utility**

```typescript
// sepa-terminal/src/lib/cn.ts
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 5: Update index.html with Google Fonts**

Add to `<head>`:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

- [ ] **Step 6: Initialize shadcn/ui**

```bash
cd sepa-terminal
npx shadcn@latest init
# Choose: New York style, Zinc base color, CSS variables: yes
npx shadcn@latest add button badge input textarea tabs scroll-area separator dialog tooltip
```

- [ ] **Step 7: Verify dev server starts**

```bash
cd sepa-terminal && npm run dev
```

- [ ] **Step 8: Commit**

```bash
git add sepa-terminal/
git commit -m "scaffold: init Vite + React + Tailwind dark theme + shadcn/ui"
```

### Task 1.2: TypeScript types and formatters

**Files:**
- Create: `sepa-terminal/src/lib/types.ts`
- Create: `sepa-terminal/src/lib/formatters.ts`
- Create: `sepa-terminal/src/lib/colors.ts`
- Create: `sepa-terminal/src/lib/mock-data.ts`

- [ ] **Step 1: Write all TypeScript interfaces**

```typescript
// sepa-terminal/src/lib/types.ts

// --- Signal / Scanner ---
export interface Signal {
  signalDate: string        // YYYY-MM-DD
  symbol: string
  signalType: 'BREAKOUT' | 'WATCH'
  close: number
  pivot: number
  nearPivot: number         // % distance from pivot
  volume: number
  vol50: number
  volMult: number
  rsRank: number
  range10: number
  voldry: number
  atrPct: number
  adv50: number
  entryRef: number
  stop: number
  rPerShare: number
}

export interface WatchlistCandidate {
  watchDate: string
  symbol: string
  close: number
  pivot: number
  nearPivot: number
  rsRank: number
  range10: number
  voldry: number
  atrPct: number
  adv50: number
  vol50: number
}

export interface SetupRow {
  id: string                // "symbol:scanDate"
  scanDate: string
  symbol: string
  signalType: 'BREAKOUT' | 'WATCH' | null
  close: number
  pivot: number
  nearPivot: number
  rsRank: number
  volMult: number
  entryRef: number | null
  stop: number | null
  rPerShare: number | null
  range10: number | null
  voldry: number | null
  atrPct: number | null
  contractionCount: number | null
  queue: QueueType
  reviewStatus: ReviewStatus
  note: string
  thesis: string
}

export type QueueType = 'breakout' | 'nearPivot' | 'shortlisted' | 'rejected'
export type ReviewStatus = 'new' | 'shortlisted' | 'watching' | 'rejected'

// --- OHLCV ---
export interface OHLCVBar {
  date: string              // YYYY-MM-DD
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// --- Market Context (mock) ---
export interface MarketRegime {
  regime: 'bull' | 'caution' | 'defensive'
  regimeLabel: string
  nifty50Price: number
  nifty50Change: number
  nifty50ChangePct: number
  niftyVs200dma: number
  breadthAbove50d: number
  breadthAbove200d: number
  indiaVix: number
  fiiToday: number          // crores
  diiToday: number
  fii5d: number
  dii5d: number
  newHighsMinusLows: number
  signalCountBreakout: number
  signalCountWatch: number
}

export interface SectorPerformance {
  sector: string
  change1m: number
  change3m: number
  change6m: number
  rrqQuadrant: 'leading' | 'weakening' | 'lagging' | 'improving'
}

// --- Diagnostics / Analysis ---
export interface TrendTemplateResult {
  conditions: Array<{
    label: string
    pass: boolean
    value: string
  }>
  passCount: number
}

export interface VCPMetrics {
  contractionCount: number
  contractionDepths: number[]   // e.g. [22, 11, 5]
  qualityRatio: number
  baseDepth: number
  baseDuration: number          // days
  volDryUp: number
  atrContraction: number
  range10d: number
  breakoutVolMult: number
}

export interface SignalHistoryEntry {
  date: string
  signalType: 'BREAKOUT' | 'WATCH'
  result: 'win' | 'loss' | 'open'
  rMultiple: number | null
  returnPct: number | null
}

// --- Portfolio ---
export type PositionStage = 'shortlisted' | 'watching' | 'open' | 'managing' | 'closed'
export type StopMethod = 'initial' | 'breakeven' | '21ema' | '10dlow'

export interface Position {
  id: string
  symbol: string
  sector: string
  entryDate: string
  entryPrice: number
  shares: number
  initialStop: number
  currentStop: number
  stopMethod: StopMethod
  stage: PositionStage
  currentPrice: number
  unrealizedPnl: number
  rMultiple: number
  daysHeld: number
  exitPrice?: number
  exitDate?: string
  exitReason?: string
}

export interface PortfolioStats {
  portfolioValue: number
  capitalBase: number
  unrealizedPnl: number
  unrealizedPnlPct: number
  openPositions: number
  openSectors: number
  capitalAtRiskPct: number
  capitalAtRiskAmt: number
  avgR: number
  closedTrades: number
  winRate: number
  expectancy: number
}

// --- Chart config ---
export type ChartTimeframe = 'D' | 'W' | 'M'

export interface ChartOverlays {
  sma50: boolean
  sma150: boolean
  sma200: boolean
  refLines: boolean
  rsLine: boolean
  vcpBands: boolean
}
```

- [ ] **Step 2: Write formatter utilities**

```typescript
// sepa-terminal/src/lib/formatters.ts

export const formatINR = (value: number): string =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value)

export const formatINRCompact = (value: number): string => {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)}Cr`
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)}L`
  return formatINR(value)
}

export const formatPrice = (value: number): string =>
  value >= 100
    ? Math.round(value).toLocaleString('en-IN')
    : value.toFixed(2)

export const formatPct = (value: number): string => {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export const formatR = (value: number): string => {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}R`
}

export const formatVolMult = (value: number): string =>
  `${value.toFixed(1)}x`

export const formatDate = (dateStr: string): string => {
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}
```

- [ ] **Step 3: Write color constants**

```typescript
// sepa-terminal/src/lib/colors.ts

export const COLORS = {
  green: '#26A69A',
  greenBg: '#26A69A20',
  red: '#EF5350',
  redBg: '#EF535020',
  amber: '#F5A623',
  amberBg: '#F5A62320',
  blue: '#3B82F6',
  blueBg: '#3B82F620',
  purple: '#8B5CF6',
  purpleBg: '#8B5CF620',
  candleBull: 'rgba(38,166,154,0.9)',
  candleBear: 'rgba(239,83,80,0.9)',
  sma50: '#e8b03e',
  sma150: '#3b82f6',
  sma200: '#ef4444',
  linePivot: '#2563eb',
  lineEntry: '#16a34a',
  lineStop: '#dc2626',
  lineExtended: '#f59e0b',
} as const

export const pnlColor = (value: number) =>
  value >= 0 ? COLORS.green : COLORS.red

export const rsColor = (rank: number) =>
  rank >= 85 ? COLORS.green : rank >= 70 ? COLORS.amber : COLORS.red
```

- [ ] **Step 4: Write mock data generators**

Create `sepa-terminal/src/lib/mock-data.ts` with generators for:
- `generateMockRegime()` → `MarketRegime` (hardcoded bull regime with realistic values)
- `generateMockSectors()` → `SectorPerformance[]` (12 NSE sectors with random perf)
- `generateMockTrendTemplate()` → `TrendTemplateResult` (9 conditions, 7-9 passing)
- `generateMockVCPMetrics()` → `VCPMetrics`
- `generateMockFundamentals()` → key-value object with P/E, growth, ROE, etc.
- `generateMockSectorContext()` → sector RS, RRG quadrant, flows
- `generateMockSignalHistory()` → `SignalHistoryEntry[]`
- `generateMockPortfolio()` → `Position[]` (6 sample positions across all stages)
- `generateMockPortfolioStats()` → `PortfolioStats`

Use plausible Indian equity values (prices ₹200-5000, RS ranks 70-98, etc.).

- [ ] **Step 5: Commit**

```bash
git add sepa-terminal/src/lib/
git commit -m "feat: add TypeScript types, formatters, colors, and mock data"
```

### Task 1.3: Zustand stores

**Files:**
- Create: `sepa-terminal/src/stores/ui.ts`
- Create: `sepa-terminal/src/stores/scanner.ts`
- Create: `sepa-terminal/src/stores/reviews.ts`
- Create: `sepa-terminal/src/stores/portfolio.ts`
- Create: `sepa-terminal/src/stores/market.ts`

- [ ] **Step 1: Write UI store**

```typescript
// sepa-terminal/src/stores/ui.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChartTimeframe, ChartOverlays } from '../lib/types'

interface UIStore {
  activeWorkspace: 1 | 2 | 3
  scanDate: string
  commandPaletteOpen: boolean
  shortcutOverlayOpen: boolean
  chartTimeframe: ChartTimeframe
  chartOverlays: ChartOverlays
  panelSizes: Record<string, number[]>
  setWorkspace: (ws: 1 | 2 | 3) => void
  setScanDate: (date: string) => void
  toggleCommandPalette: () => void
  toggleShortcutOverlay: () => void
  setChartTimeframe: (tf: ChartTimeframe) => void
  toggleChartOverlay: (key: keyof ChartOverlays) => void
  setPanelSizes: (key: string, sizes: number[]) => void
}

export const useUIStore = create<UIStore>()(
  persist(
    (set) => ({
      activeWorkspace: 1,
      scanDate: new Date().toISOString().slice(0, 10),
      commandPaletteOpen: false,
      shortcutOverlayOpen: false,
      chartTimeframe: 'D',
      chartOverlays: {
        sma50: true,
        sma150: true,
        sma200: true,
        refLines: true,
        rsLine: false,
        vcpBands: false,
      },
      panelSizes: {},
      setWorkspace: (ws) => set({ activeWorkspace: ws }),
      setScanDate: (date) => set({ scanDate: date }),
      toggleCommandPalette: () =>
        set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
      toggleShortcutOverlay: () =>
        set((s) => ({ shortcutOverlayOpen: !s.shortcutOverlayOpen })),
      setChartTimeframe: (tf) => set({ chartTimeframe: tf }),
      toggleChartOverlay: (key) =>
        set((s) => ({
          chartOverlays: { ...s.chartOverlays, [key]: !s.chartOverlays[key] },
        })),
      setPanelSizes: (key, sizes) =>
        set((s) => ({ panelSizes: { ...s.panelSizes, [key]: sizes } })),
    }),
    { name: 'sepa-ui', partialize: (s) => ({ panelSizes: s.panelSizes, chartOverlays: s.chartOverlays }) }
  )
)
```

- [ ] **Step 2: Write scanner store**

```typescript
// sepa-terminal/src/stores/scanner.ts
import { create } from 'zustand'
import type { QueueType } from '../lib/types'

interface ScannerStore {
  activeQueue: QueueType
  selectedSetupId: string | null
  sectorFilter: string | null
  rsFilter: number | null
  setQueue: (queue: QueueType) => void
  setSelectedSetup: (id: string | null) => void
  setSectorFilter: (sector: string | null) => void
  setRsFilter: (min: number | null) => void
  selectNext: (ids: string[]) => void
  selectPrev: (ids: string[]) => void
}

export const useScannerStore = create<ScannerStore>()((set, get) => ({
  activeQueue: 'breakout',
  selectedSetupId: null,
  sectorFilter: null,
  rsFilter: null,
  setQueue: (queue) => set({ activeQueue: queue }),
  setSelectedSetup: (id) => set({ selectedSetupId: id }),
  setSectorFilter: (sector) => set({ sectorFilter: sector }),
  setRsFilter: (min) => set({ rsFilter: min }),
  selectNext: (ids) => {
    const { selectedSetupId } = get()
    if (!ids.length) return
    if (!selectedSetupId) { set({ selectedSetupId: ids[0] }); return }
    const idx = ids.indexOf(selectedSetupId)
    const next = idx < ids.length - 1 ? ids[idx + 1] : ids[0]
    set({ selectedSetupId: next })
  },
  selectPrev: (ids) => {
    const { selectedSetupId } = get()
    if (!ids.length) return
    if (!selectedSetupId) { set({ selectedSetupId: ids[ids.length - 1] }); return }
    const idx = ids.indexOf(selectedSetupId)
    const prev = idx > 0 ? ids[idx - 1] : ids[ids.length - 1]
    set({ selectedSetupId: prev })
  },
}))
```

- [ ] **Step 3: Write reviews store with undo**

```typescript
// sepa-terminal/src/stores/reviews.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ReviewStatus } from '../lib/types'

interface ReviewAction {
  symbol: string
  scanDate: string
  status: ReviewStatus
  priority: number
  note: string
  thesis: string
  reviewedAt: string
}

interface UndoEntry {
  key: string
  prev: ReviewAction | undefined
}

interface ReviewsStore {
  reviews: Record<string, ReviewAction>
  undoStack: UndoEntry[]
  shortlist: (symbol: string, scanDate: string) => void
  reject: (symbol: string, scanDate: string) => void
  watch: (symbol: string, scanDate: string) => void
  reset: (symbol: string, scanDate: string) => void
  setNote: (symbol: string, scanDate: string, note: string) => void
  setThesis: (symbol: string, scanDate: string, thesis: string) => void
  undoLast: () => void
  getReview: (symbol: string, scanDate: string) => ReviewAction | undefined
}

const makeKey = (symbol: string, scanDate: string) => `${symbol}:${scanDate}`

export const useReviewsStore = create<ReviewsStore>()(
  persist(
    (set, get) => ({
      reviews: {},
      undoStack: [],

      shortlist: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: {
              symbol, scanDate, status: 'shortlisted', priority: 1,
              note: prev?.note ?? '', thesis: prev?.thesis ?? '',
              reviewedAt: prev?.reviewedAt ?? new Date().toISOString(),
            },
          },
        }))
      },

      reject: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: {
              symbol, scanDate, status: 'rejected', priority: 0,
              note: prev?.note ?? '', thesis: prev?.thesis ?? '',
              reviewedAt: prev?.reviewedAt ?? new Date().toISOString(),
            },
          },
        }))
      },

      watch: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: {
              symbol, scanDate, status: 'watching', priority: 0,
              note: prev?.note ?? '', thesis: prev?.thesis ?? '',
              reviewedAt: prev?.reviewedAt ?? new Date().toISOString(),
            },
          },
        }))
      },

      reset: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        set((s) => {
          const { [key]: _, ...rest } = s.reviews
          return { reviews: rest }
        })
      },

      setNote: (symbol, scanDate, note) => {
        const key = makeKey(symbol, scanDate)
        set((s) => ({
          reviews: {
            ...s.reviews,
            [key]: { ...s.reviews[key] ?? { symbol, scanDate, status: 'new', priority: 0, thesis: '', reviewedAt: new Date().toISOString() }, note },
          },
        }))
      },

      setThesis: (symbol, scanDate, thesis) => {
        const key = makeKey(symbol, scanDate)
        set((s) => ({
          reviews: {
            ...s.reviews,
            [key]: { ...s.reviews[key] ?? { symbol, scanDate, status: 'new', priority: 0, note: '', reviewedAt: new Date().toISOString() }, thesis },
          },
        }))
      },

      undoLast: () => {
        const { undoStack } = get()
        if (!undoStack.length) return
        const entry = undoStack[undoStack.length - 1]
        set((s) => {
          const newReviews = { ...s.reviews }
          if (entry.prev) {
            newReviews[entry.key] = entry.prev
          } else {
            delete newReviews[entry.key]
          }
          return { reviews: newReviews, undoStack: s.undoStack.slice(0, -1) }
        })
      },

      getReview: (symbol, scanDate) => get().reviews[makeKey(symbol, scanDate)],
    }),
    { name: 'sepa-reviews' }
  )
)
```

- [ ] **Step 4: Write market store**

```typescript
// sepa-terminal/src/stores/market.ts
import { create } from 'zustand'
import type { MarketRegime, SectorPerformance } from '../lib/types'
import { generateMockRegime, generateMockSectors } from '../lib/mock-data'

interface MarketStore {
  regime: MarketRegime
  sectors: SectorPerformance[]
  sectorPeriod: '1m' | '3m' | '6m'
  setSectorPeriod: (p: '1m' | '3m' | '6m') => void
  setRegime: (r: MarketRegime) => void
  setSectors: (s: SectorPerformance[]) => void
}

export const useMarketStore = create<MarketStore>()((set) => ({
  regime: generateMockRegime(),
  sectors: generateMockSectors(),
  sectorPeriod: '1m',
  setSectorPeriod: (p) => set({ sectorPeriod: p }),
  setRegime: (r) => set({ regime: r }),
  setSectors: (s) => set({ sectors: s }),
}))
```

- [ ] **Step 5: Write portfolio store**

```typescript
// sepa-terminal/src/stores/portfolio.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Position, PositionStage, StopMethod, PortfolioStats } from '../lib/types'
import { generateMockPortfolio, generateMockPortfolioStats } from '../lib/mock-data'

interface PortfolioStore {
  positions: Position[]
  stats: PortfolioStats
  addPosition: (pos: Omit<Position, 'id'>) => void
  updateStage: (id: string, stage: PositionStage) => void
  updateStop: (id: string, stop: number, method: StopMethod) => void
  closePosition: (id: string, exitPrice: number, reason: string) => void
}

export const usePortfolioStore = create<PortfolioStore>()(
  persist(
    (set) => ({
      positions: generateMockPortfolio(),
      stats: generateMockPortfolioStats(),
      addPosition: (pos) =>
        set((s) => ({
          positions: [...s.positions, { ...pos, id: crypto.randomUUID() }],
        })),
      updateStage: (id, stage) =>
        set((s) => ({
          positions: s.positions.map((p) => (p.id === id ? { ...p, stage } : p)),
        })),
      updateStop: (id, stop, method) =>
        set((s) => ({
          positions: s.positions.map((p) =>
            p.id === id ? { ...p, currentStop: stop, stopMethod: method } : p
          ),
        })),
      closePosition: (id, exitPrice, reason) =>
        set((s) => ({
          positions: s.positions.map((p) =>
            p.id === id
              ? { ...p, stage: 'closed' as const, exitPrice, exitReason: reason, exitDate: new Date().toISOString().slice(0, 10) }
              : p
          ),
        })),
    }),
    { name: 'sepa-portfolio' }
  )
)
```

- [ ] **Step 6: Commit**

```bash
git add sepa-terminal/src/stores/
git commit -m "feat: add Zustand stores (ui, scanner, reviews, market, portfolio)"
```

---

## Batch 2: FastAPI Backend (minimal endpoints for OHLCV + signals)

### Task 2.1: FastAPI app skeleton

**Files:**
- Create: `src/sepa_scanner/api/__init__.py`
- Create: `src/sepa_scanner/api/app.py`
- Create: `src/sepa_scanner/api/deps.py`
- Create: `src/sepa_scanner/api/schemas.py`
- Create: `src/sepa_scanner/api/routes/__init__.py`
- Create: `src/sepa_scanner/api/routes/ohlcv.py`
- Create: `src/sepa_scanner/api/routes/scanner.py`
- Create: `src/sepa_scanner/api/routes/analysis.py`
- Modify: `pyproject.toml` (add fastapi + uvicorn deps)

- [ ] **Step 1: Add FastAPI dependencies to pyproject.toml**

Add `"fastapi[standard]>=0.115.0"` and `"uvicorn>=0.34.0"` to `dependencies` list.

- [ ] **Step 2: Create deps.py with dependency injection**

```python
# src/sepa_scanner/api/deps.py
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sepa_scanner.settings import AppSettings, load_settings
from sepa_scanner.storage.duckdb_store import DuckDbStore
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


@lru_cache
def get_settings() -> AppSettings:
    return load_settings()


@lru_cache
def get_duckdb_store() -> DuckDbStore:
    settings = get_settings()
    store = DuckDbStore(settings.runtime.data_dir / "db" / "sepa.duckdb", read_only=True)
    store.initialize()
    return store


@lru_cache
def get_parquet_store() -> ParquetOhlcvStore:
    settings = get_settings()
    return ParquetOhlcvStore(settings.runtime.data_dir / "ohlcv_parquet")
```

- [ ] **Step 3: Create schemas.py with Pydantic response models**

Pydantic models for: `OHLCVResponse`, `SignalResponse`, `WatchlistResponse`, `DiagnosticsResponse`, `TrendTemplateResponse`, `SignalHistoryResponse`. Each mirrors the TypeScript types but with snake_case. Use `model_config = ConfigDict(from_attributes=True)`.

- [ ] **Step 4: Create ohlcv.py route**

```python
# src/sepa_scanner/api/routes/ohlcv.py
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from sepa_scanner.api.deps import get_parquet_store
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

router = APIRouter(prefix="/api/ohlcv", tags=["ohlcv"])


@router.get("/{symbol}")
def get_ohlcv(
    symbol: str,
    start: date | None = Query(None),
    end: date | None = Query(None),
    store: ParquetOhlcvStore = Depends(get_parquet_store),
) -> list[dict]:
    df = store.read_frame(
        start_date=start,
        end_date=end,
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
    ).filter(__import__("polars").col("symbol") == symbol).sort("date")
    rows = df.to_dicts()
    for row in rows:
        row["date"] = row["date"].isoformat()
    return rows
```

- [ ] **Step 5: Create scanner.py route**

Endpoints:
- `GET /api/scanner/signals?date=YYYY-MM-DD&type=BREAKOUT|WATCH` — returns signals from DuckDB
- `GET /api/scanner/watchlist?date=YYYY-MM-DD` — returns watchlist candidates
- `GET /api/scanner/dates` — returns available scan dates

All wrap existing `DuckDbStore` methods.

- [ ] **Step 6: Create analysis.py route**

Endpoints:
- `GET /api/analysis/{symbol}/diagnostics?date=YYYY-MM-DD` — returns daily diagnostics for a symbol
- `GET /api/analysis/{symbol}/signal-history` — returns all past signals for a symbol

Both use existing DuckDB queries.

- [ ] **Step 7: Create app.py with CORS and router mounts**

```python
# src/sepa_scanner/api/app.py
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sepa_scanner.api.routes import ohlcv, scanner, analysis

app = FastAPI(title="SEPA Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ohlcv.router)
app.include_router(scanner.router)
app.include_router(analysis.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 8: Test the API manually**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
uv run uvicorn sepa_scanner.api.app:app --reload --port 8000
# In another terminal:
curl http://localhost:8000/api/health
curl http://localhost:8000/api/scanner/dates
```

- [ ] **Step 9: Commit**

```bash
git add src/sepa_scanner/api/ pyproject.toml
git commit -m "feat: add FastAPI backend with OHLCV, scanner, and analysis endpoints"
```

### Task 2.2: Frontend API client + TanStack Query hooks

**Files:**
- Create: `sepa-terminal/src/api/client.ts`
- Create: `sepa-terminal/src/api/useOHLCV.ts`
- Create: `sepa-terminal/src/api/useScannerData.ts`
- Create: `sepa-terminal/src/api/useMarketData.ts`

- [ ] **Step 1: Write base API client**

```typescript
// sepa-terminal/src/api/client.ts
const BASE_URL = '/api'

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, init)
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
  return res.json()
}
```

- [ ] **Step 2: Write OHLCV query hook**

```typescript
// sepa-terminal/src/api/useOHLCV.ts
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { OHLCVBar } from '../lib/types'

export function useOHLCV(symbol: string | null) {
  return useQuery({
    queryKey: ['ohlcv', symbol],
    queryFn: () => apiFetch<OHLCVBar[]>(`/ohlcv/${symbol}`),
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}
```

- [ ] **Step 3: Write scanner data hook**

```typescript
// sepa-terminal/src/api/useScannerData.ts
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { Signal, WatchlistCandidate } from '../lib/types'

export function useSignals(date: string) {
  return useQuery({
    queryKey: ['signals', date],
    queryFn: () => apiFetch<Signal[]>(`/scanner/signals?date=${date}`),
    staleTime: 5 * 60 * 1000,
  })
}

export function useWatchlist(date: string) {
  return useQuery({
    queryKey: ['watchlist', date],
    queryFn: () => apiFetch<WatchlistCandidate[]>(`/scanner/watchlist?date=${date}`),
    staleTime: 5 * 60 * 1000,
  })
}

export function useScanDates() {
  return useQuery({
    queryKey: ['scan-dates'],
    queryFn: () => apiFetch<string[]>('/scanner/dates'),
    staleTime: 60 * 1000,
  })
}
```

- [ ] **Step 4: Write market data hook (mock)**

Returns mock data for now, structured to swap in real API later.

- [ ] **Step 5: Commit**

```bash
git add sepa-terminal/src/api/
git commit -m "feat: add API client and TanStack Query hooks"
```

---

## Batch 3: Layout Shell + Routing

### Task 3.1: App shell with routing and providers

**Files:**
- Create/modify: `sepa-terminal/src/main.tsx`
- Create/modify: `sepa-terminal/src/App.tsx`
- Create: `sepa-terminal/src/components/layout/TopBar.tsx`
- Create: `sepa-terminal/src/components/layout/ShortcutBar.tsx`
- Create: `sepa-terminal/src/components/workspaces/MarketScanner.tsx` (placeholder)
- Create: `sepa-terminal/src/components/workspaces/Analysis.tsx` (placeholder)
- Create: `sepa-terminal/src/components/workspaces/Portfolio.tsx` (placeholder)
- Create: `sepa-terminal/src/hooks/useWorkspaceSwitch.ts`

- [ ] **Step 1: Write main.tsx with providers**

```typescript
// sepa-terminal/src/main.tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router'
import App from './App'
import './styles/globals.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
)
```

- [ ] **Step 2: Write App.tsx with workspace switching**

App.tsx renders: TopBar → RegimeStrip → active workspace → ShortcutBar. Uses `useUIStore.activeWorkspace` to conditionally render the active workspace component. Wraps keyboard shortcut providers.

- [ ] **Step 3: Write TopBar component**

Per spec: Logo "SEPA Terminal" + "v2" pill, 3 workspace tabs (active = green border), date + Cmd+K hint. See spec "TopBar component" section.

- [ ] **Step 4: Write ShortcutBar component**

Per spec: Bottom bar with context-aware keyboard hints. Reads `activeWorkspace` from UIStore to show relevant shortcuts. Shows "N of M reviewed" progress on the right.

- [ ] **Step 5: Write useWorkspaceSwitch hook**

```typescript
// sepa-terminal/src/hooks/useWorkspaceSwitch.ts
import { useHotkeys } from 'react-hotkeys-hook'
import { useUIStore } from '../stores/ui'

export function useWorkspaceSwitch() {
  const setWorkspace = useUIStore((s) => s.setWorkspace)
  useHotkeys('1', () => setWorkspace(1), { preventDefault: true })
  useHotkeys('2', () => setWorkspace(2), { preventDefault: true })
  useHotkeys('3', () => setWorkspace(3), { preventDefault: true })
}
```

- [ ] **Step 6: Create placeholder workspace components**

Each just renders a centered text label ("Market + Scanner", "Analysis", "Portfolio") so we can verify routing works.

- [ ] **Step 7: Verify workspace switching works with 1/2/3 keys**

- [ ] **Step 8: Commit**

```bash
git add sepa-terminal/src/
git commit -m "feat: add app shell with workspace routing, TopBar, ShortcutBar"
```

---

## Batch 4: Market Context (Workspace 1 top half)

### Task 4.1: RegimeStrip component

**Files:**
- Create: `sepa-terminal/src/components/layout/RegimeStrip.tsx`

- [ ] **Step 1: Build RegimeStrip**

Horizontal bar with: regime pill (green/amber/red circle + label), then stat cells separated by vertical dividers. Each cell has 10px uppercase label + 13px value. Reads from `useMarketStore`. Uses mock data. See spec "RegimeStrip component" for exact cells and formatting.

- [ ] **Step 2: Commit**

### Task 4.2: SectorHeatmap component

**Files:**
- Create: `sepa-terminal/src/components/market/SectorHeatmap.tsx`

- [ ] **Step 1: Build SectorHeatmap**

6x2 CSS grid of sector cells. Each cell: sector name + performance value, background tinted green/red based on magnitude. Period toggle (1M/3M/6M). Click to filter scanner. Reads from `useMarketStore.sectors`. See spec "SectorHeatmap component" for details.

- [ ] **Step 2: Commit**

### Task 4.3: BreadthPanel component

**Files:**
- Create: `sepa-terminal/src/components/market/BreadthPanel.tsx`

- [ ] **Step 1: Build BreadthPanel**

Contains: 3 horizontal gauge bars (Above 200-DMA, Above 50-DMA, NH-NL) + FII/DII bidirectional bar section. All from mock data via `useMarketStore`. See spec "BreadthPanel component" for layout.

- [ ] **Step 2: Commit**

---

## Batch 5: Scanner + Triage (Workspace 1 bottom half — CRITICAL PATH)

### Task 5.1: QueueTabs component

**Files:**
- Create: `sepa-terminal/src/components/scanner/QueueTabs.tsx`

- [ ] **Step 1: Build QueueTabs**

4 tabs: "Breakout now", "Near pivot", "Shortlisted", "Rejected" — each with count badge. Active tab = green bottom border. Reads/sets `useScannerStore.activeQueue`. Counts derived from setup rows filtered by queue.

- [ ] **Step 2: Commit**

### Task 5.2: ScannerTable with TanStack Table

**Files:**
- Create: `sepa-terminal/src/components/scanner/ScannerTable.tsx`

- [ ] **Step 1: Build ScannerTable**

TanStack Table with columns: Symbol (left, 120px), Signal (pill), Close (right), Pivot (right), RS (right, colored), Vol (right, colored), Contr. (right). Single row selection. Selected row highlighted with green at 8% opacity. Reads setup data from API hooks merged with review store. Row click updates `scannerStore.selectedSetupId`.

Key implementation details:
- Merge signals + watchlist + reviews into unified `SetupRow[]` (port logic from existing `setup_models.py`)
- Filter by `activeQueue` and `sectorFilter`
- Sort per queue type (same logic as existing `filter_setup_queue`)

- [ ] **Step 2: Commit**

### Task 5.3: Shared UI components

**Files:**
- Create: `sepa-terminal/src/components/shared/StatusPill.tsx`
- Create: `sepa-terminal/src/components/shared/MetricCard.tsx`
- Create: `sepa-terminal/src/components/shared/QualificationPanel.tsx`

- [ ] **Step 1: Build StatusPill** — Colored pill for BREAK/WATCH/Stage2/RS etc. Props: label, color variant.

- [ ] **Step 2: Build MetricCard** — Reusable stat card with label + value + optional subtitle.

- [ ] **Step 3: Build QualificationPanel** — Two-column split: green "Why it qualifies" (left) + amber "Risk flags" (right). Each column has title with icon + list of items with colored dots.

- [ ] **Step 4: Commit**

### Task 5.4: PriceChart (Lightweight Charts wrapper)

**Files:**
- Create: `sepa-terminal/src/components/chart/PriceChart.tsx`
- Create: `sepa-terminal/src/components/chart/ChartToolbar.tsx`

- [ ] **Step 1: Build PriceChart**

Wrapper around TradingView Lightweight Charts:
- Takes `OHLCVBar[]` data, `entryRef`, `stop`, `pivot` prices, and `chartOverlays` config
- Creates candlestick series (bull/bear colors from CSS vars)
- Adds SMA line series (50/150/200) computed from OHLCV data on the client
- Adds horizontal price lines for pivot/entry/stop (dashed, labeled)
- Adds volume histogram at bottom (30px)
- Background, grid, crosshair styled per spec
- Uses `useRef` + `useEffect` for chart lifecycle
- Resizes with ResizeObserver
- Supports daily/weekly/monthly by resampling OHLCV data on the client

SMA computation helper (compute on client from OHLCV array):
```typescript
function computeSMA(data: OHLCVBar[], period: number): { time: string; value: number }[] {
  const result: { time: string; value: number }[] = []
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += data[j].close
    result.push({ time: data[i].date, value: sum / period })
  }
  return result
}
```

Weekly/monthly resampling: Group OHLCV by ISO week or month, aggregate OHLC (first open, max high, min low, last close, sum volume).

- [ ] **Step 2: Build ChartToolbar**

Toggle buttons: D/W timeframes, SMA toggles (50/150/200), Ref lines toggle, RS line toggle. Reads/writes `useUIStore.chartOverlays` and `chartTimeframe`. See spec "ChartToolbar" for styling.

- [ ] **Step 3: Commit**

### Task 5.5: SetupDetailCard

**Files:**
- Create: `sepa-terminal/src/components/scanner/SetupDetailCard.tsx`
- Create: `sepa-terminal/src/components/analysis/TradePlanCard.tsx`

- [ ] **Step 1: Build SetupDetailCard**

Vertically stacked sections for selected setup:
1. **DetailHeader**: Ticker name + price + change + action buttons (S/W/X)
2. **ChartToolbar + PriceChart** (reuse from Task 5.4)
3. **QualificationPanel** (reuse from Task 5.3): populate green items from signal data (VCP, RS, volume, sector), amber items from mock risk flags
4. **TradePlanCard**: 4-cell grid (Entry, Stop, Risk, Position at 1% risk) computed from signal's entry_ref and stop values
5. **NoteInput**: Text input + save button, `N` key focuses it

All data from `useScannerStore.selectedSetupId` → lookup in setup rows → pass to sub-components. Chart data from `useOHLCV(symbol)`.

- [ ] **Step 2: Commit**

### Task 5.6: Keyboard triage hook

**Files:**
- Create: `sepa-terminal/src/hooks/useKeyboardTriage.ts`

- [ ] **Step 1: Build useKeyboardTriage**

```typescript
// sepa-terminal/src/hooks/useKeyboardTriage.ts
import { useHotkeys } from 'react-hotkeys-hook'
import { useScannerStore } from '../stores/scanner'
import { useReviewsStore } from '../stores/reviews'
import { useUIStore } from '../stores/ui'

export function useKeyboardTriage(setupIds: string[]) {
  const { selectedSetupId, selectNext, selectPrev } = useScannerStore()
  const { shortlist, reject, watch } = useReviewsStore()
  const scanDate = useUIStore((s) => s.scanDate)
  const setWorkspace = useUIStore((s) => s.setWorkspace)

  const getSymbol = () => selectedSetupId?.split(':')[0] ?? null

  useHotkeys('j, ArrowDown', () => selectNext(setupIds), [setupIds])
  useHotkeys('k, ArrowUp', () => selectPrev(setupIds), [setupIds])
  useHotkeys('s', () => {
    const sym = getSymbol()
    if (sym) { shortlist(sym, scanDate); selectNext(setupIds) }
  }, [setupIds, scanDate])
  useHotkeys('x', () => {
    const sym = getSymbol()
    if (sym) { reject(sym, scanDate); selectNext(setupIds) }
  }, [setupIds, scanDate])
  useHotkeys('w', () => {
    const sym = getSymbol()
    if (sym) watch(sym, scanDate)
  }, [scanDate])
  useHotkeys('Enter, ArrowRight', () => {
    if (selectedSetupId) setWorkspace(2)
  })
}
```

Note: Disable triage keys when note input or command palette is focused (check `document.activeElement`).

- [ ] **Step 2: Commit**

### Task 5.7: Compose MarketScanner workspace

**Files:**
- Modify: `sepa-terminal/src/components/workspaces/MarketScanner.tsx`

- [ ] **Step 1: Compose the full Workspace 1 layout**

Use `react-resizable-panels` to create the layout:
```
┌─────────────────────────┬──────────────────────────┐
│ SectorHeatmap           │ BreadthPanel             │
│ (~50% width, ~140px h)  │ (~50% width, ~140px h)   │
├─────────────────────────┼──────────────────────────┤
│ QueueTabs + ScannerTable│ SetupDetailCard           │
│ (~45% width, fill)      │ (~55% width, fill)        │
└─────────────────────────┴──────────────────────────┘
```

Wire `useKeyboardTriage` with the current queue's setup IDs. Wire `useOHLCV` for the selected symbol's chart.

- [ ] **Step 2: Verify full triage flow works**

Test: J/K navigation, S/X/W actions, chart updates on selection, queue tab switching.

- [ ] **Step 3: Commit**

```bash
git add sepa-terminal/src/
git commit -m "feat: complete Workspace 1 — scanner + triage + chart + keyboard navigation"
```

---

## Batch 6: Analysis Workspace (Workspace 2)

### Task 6.1: SymbolBar + extended chart

**Files:**
- Create: `sepa-terminal/src/components/chart/RSPane.tsx`
- Create: `sepa-terminal/src/components/chart/VolumePane.tsx`

- [ ] **Step 1: Build SymbolBar**

Horizontal bar: ticker (18px) + company · sector + status pills (Stage2, RS, VCP) + price + change. Reads from scanner store selected setup.

- [ ] **Step 2: Build RSPane**

Separate Recharts area chart (80px height) showing RS score over time. Blue when rising, gray when declining. For now, derive RS from OHLCV by computing relative performance vs NIFTYBEES (or mock a simple RS series).

- [ ] **Step 3: Build VolumePane**

Volume histogram (60px height) with green/red bars matching candle direction. 50-day average line overlay. Use Recharts bar chart or a second Lightweight Charts instance.

- [ ] **Step 4: Commit**

### Task 6.2: Analysis info column components

**Files:**
- Create: `sepa-terminal/src/components/analysis/TrendTemplate.tsx`
- Create: `sepa-terminal/src/components/analysis/VCPMetrics.tsx`
- Create: `sepa-terminal/src/components/analysis/SectorContext.tsx`
- Create: `sepa-terminal/src/components/analysis/Fundamentals.tsx`
- Create: `sepa-terminal/src/components/analysis/SignalHistory.tsx`
- Create: `sepa-terminal/src/components/analysis/TradeThesis.tsx`

- [ ] **Step 1: Build TrendTemplate**

9-row checklist. Each row: pass/fail icon (green check / red cross) + condition label + actual value. Data from `/api/analysis/{symbol}/diagnostics` endpoint (maps `tt1_close_above_sma150` through `tt9_near_52w_high` booleans). Format values per spec.

- [ ] **Step 2: Build VCPMetrics**

Key-value rows: contractions, quality ratio, base depth, base duration, vol dry-up, ATR contraction, range 10d, breakout vol. Data from diagnostics endpoint + signal data. Color values green when passing thresholds.

- [ ] **Step 3: Build SectorContext** (mock data)

Key-value rows: sector performance, RRG quadrant, FII/DII flow, sector breadth. All from mock generators.

- [ ] **Step 4: Build Fundamentals** (mock data)

Key-value rows: market cap, P/E, revenue growth, PAT growth, ROE, debt/equity, promoter holding, FII change. All mock.

- [ ] **Step 5: Build SignalHistory**

List of past signals for this symbol from `/api/analysis/{symbol}/signal-history`. Each row: colored dot + date + signal type pill + R-multiple + return %. Summary line below.

- [ ] **Step 6: Build TradeThesis**

Auto-expanding textarea. Persists to reviews store via `setThesis()`. `N` key focuses it.

- [ ] **Step 7: Commit**

### Task 6.3: Compose Analysis workspace

**Files:**
- Modify: `sepa-terminal/src/components/workspaces/Analysis.tsx`

- [ ] **Step 1: Compose the full Workspace 2 layout**

```
┌──────────────────────────────────┬──────────────────┐
│ ChartColumn (70%)                │ InfoColumn (30%)  │
│ SymbolBar                        │ TradePlanCard     │
│ ChartToolbar (extended)          │ TrendTemplate     │
│ PriceChart (340px)               │ VCPMetrics        │
│ RSPane (80px)                    │ SectorContext     │
│ VolumePane (60px)                │ Fundamentals      │
│                                  │ SignalHistory      │
│                                  │ TradeThesis        │
│                                  │ ActionBar (sticky) │
└──────────────────────────────────┴──────────────────┘
```

Use `react-resizable-panels` for the horizontal split. Info column is scrollable.

Keyboard: `Escape` → back to W1, `D`/`Shift+W`/`M` → timeframe, `N` → thesis focus.

- [ ] **Step 2: Wire Enter key from W1 → W2 with selected symbol**

When user presses Enter on a scanner row, switch to W2 with that symbol loaded.

- [ ] **Step 3: Commit**

```bash
git add sepa-terminal/src/
git commit -m "feat: complete Workspace 2 — analysis view with chart, trend template, VCP metrics"
```

---

## Batch 7: Command Palette

### Task 7.1: CommandPalette with cmdk

**Files:**
- Create: `sepa-terminal/src/components/shared/CommandPalette.tsx`

- [ ] **Step 1: Build CommandPalette**

cmdk-based modal:
- Triggered by `Cmd+K` / `Ctrl+K` (via `useHotkeys` global binding in App.tsx)
- Opens as dialog overlay (560px wide, ~80px from top)
- Input with search icon + placeholder "Search symbols, actions, views..."
- Result groups: Symbols, Navigation, Actions, Filters
- Fuzzy search across: symbol names from scanner data, navigation actions, filter commands
- Symbol results show price, RS rank, signal type inline
- Focused item highlighted with green left border
- Enter selects, Escape closes
- Navigation actions: switch workspace
- Filter actions: RS filter, sector filter, clear filters

Implementation: Use `<Command>` from cmdk with `<Command.Input>`, `<Command.List>`, `<Command.Group>`, `<Command.Item>`.

- [ ] **Step 2: Wire Cmd+K globally**

In App.tsx, add `useHotkeys('mod+k', toggleCommandPalette, { preventDefault: true })`.

- [ ] **Step 3: Commit**

```bash
git add sepa-terminal/src/components/shared/CommandPalette.tsx sepa-terminal/src/App.tsx
git commit -m "feat: add command palette with symbol search and navigation"
```

---

## Batch 8: Portfolio Workspace (Workspace 3)

### Task 8.1: PortfolioStats + HeatGauge

**Files:**
- Create: `sepa-terminal/src/components/portfolio/PortfolioStats.tsx`
- Create: `sepa-terminal/src/components/portfolio/HeatGauge.tsx`

- [ ] **Step 1: Build PortfolioStats**

6 equal-width cells: Portfolio value, Unrealized P&L, Open positions, Capital at risk, Avg R, Expectancy. Each cell: 18px value + 11px subtitle. Separated by borders. Reads from `usePortfolioStore.stats`. See spec for exact formatting and coloring.

- [ ] **Step 2: Build HeatGauge**

Horizontal bar visualization: Label "Portfolio heat" + bar track with 4 zone tints (0-3%, 3-6%, 6-9%, 9-12%) + filled portion + white marker + value text + scale labels. See spec "HeatGauge component".

- [ ] **Step 3: Commit**

### Task 8.2: KanbanBoard + KanbanCard

**Files:**
- Create: `sepa-terminal/src/components/portfolio/KanbanBoard.tsx`
- Create: `sepa-terminal/src/components/portfolio/KanbanCard.tsx`

- [ ] **Step 1: Build KanbanCard**

Card with stage-specific content:
- **Shortlisted**: ticker + signal pill + entry/stop/R:R + "Shortlisted Xh ago"
- **Watching**: ticker + WATCH pill + pivot/distance/RS + "Watching Xd"
- **Open**: ticker + R-multiple pill + mini sparkline + entry/now/trail stop + P&L + "N days held"
- **Managing**: same as Open + "Breakeven locked"
- **Closed**: ticker + R-multiple pill + return/held/exit reason + "Closed Xd ago"

Props: `Position` object. Card style: bg-surface, border-subtle, rounded-8px. Hover: border-hover.

- [ ] **Step 2: Build KanbanBoard**

5 equal columns (Shortlisted, Watching, Open, Managing, Closed). Each column: header with title + count badge + optional metric, scrollable body with KanbanCard items. Reads from `usePortfolioStore.positions`, groups by `stage`.

- [ ] **Step 3: Commit**

### Task 8.3: Compose Portfolio workspace

**Files:**
- Modify: `sepa-terminal/src/components/workspaces/Portfolio.tsx`

- [ ] **Step 1: Compose Workspace 3**

```
┌─────────────────────────────────────────────┐
│ PortfolioStats (6 cells)                     │
├─────────────────────────────────────────────┤
│ HeatGauge                                    │
├────────┬────────┬────────┬────────┬─────────┤
│ Short- │ Watch- │ Open   │ Manag- │ Closed  │
│ listed │ ing    │        │ ing    │         │
│ cards  │ cards  │ cards  │ cards  │ cards   │
└────────┴────────┴────────┴────────┴─────────┘
```

All data from portfolio store (mock positions). Keyboard: J/K navigate cards.

- [ ] **Step 2: Commit**

```bash
git add sepa-terminal/src/
git commit -m "feat: complete Workspace 3 — portfolio Kanban with stats and heat gauge"
```

---

## Batch 9: Polish + Integration

### Task 9.1: Delete Streamlit UI

**Files:**
- Delete: `src/sepa_scanner/ui/app.py`
- Delete: `src/sepa_scanner/ui/charts.py`
- Delete: `src/sepa_scanner/ui/queries.py`
- Delete: `src/sepa_scanner/ui/setup_models.py`
- Modify: `src/sepa_scanner/ui/__init__.py` (empty or remove)
- Modify: `pyproject.toml` (remove streamlit, streamlit-lightweight-charts, plotly from deps)

- [ ] **Step 1: Remove Streamlit files and dependencies**
- [ ] **Step 2: Run existing tests to verify nothing breaks**

```bash
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
uv run pytest tests/ -v --ignore=tests/test_ui_backtest.py
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove Streamlit UI in favor of React frontend"
```

### Task 9.2: Loading states, empty states, error boundaries

**Files:**
- Modify: Various components to add loading/empty/error states

- [ ] **Step 1: Add loading shimmer to PriceChart** (show gray animated rectangle while OHLCV loads)
- [ ] **Step 2: Add empty states to ScannerTable** ("No signals for this date" message)
- [ ] **Step 3: Add empty states to KanbanBoard columns** ("No positions" with subtle icon)
- [ ] **Step 4: Add React error boundary wrapper around each workspace**
- [ ] **Step 5: Commit**

### Task 9.3: Keyboard shortcut overlay

- [ ] **Step 1: Build shortcut overlay** — Semi-transparent modal showing all shortcuts for the current workspace. Toggled by `?` key. Grouped by section.
- [ ] **Step 2: Commit**

### Task 9.4: Cross-workspace navigation

- [ ] **Step 1: Clicking a ticker in Portfolio → opens Analysis with that symbol**
- [ ] **Step 2: Command palette symbol search → opens Analysis with that symbol**
- [ ] **Step 3: Commit**

### Task 9.5: Final integration test

- [ ] **Step 1: Start both servers**

```bash
# Terminal 1: FastAPI
cd /Users/rohanahire/Documents/Personal_Projects/multibagger
uv run uvicorn sepa_scanner.api.app:app --reload --port 8000

# Terminal 2: Vite dev server
cd /Users/rohanahire/Documents/Personal_Projects/multibagger/sepa-terminal
npm run dev
```

- [ ] **Step 2: Verify the complete workflow from spec**

1. Land on W1, see regime strip + sector heatmap + scanner table
2. J/K through setups, chart updates
3. S to shortlist, X to reject, auto-advance works
4. Enter to open Analysis (W2)
5. See full chart + trend template + VCP metrics
6. Press 3 for Portfolio
7. See Kanban board with mock positions
8. Cmd+K opens command palette, search works
9. 1/2/3 workspace switching is instant

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: SEPA Terminal v2 — complete React trading workstation"
```

---

## Dependency Graph

```
Batch 1 (Scaffold + Types + Stores)
  ├── Batch 2 (FastAPI backend) ──────────┐
  ├── Batch 3 (Layout shell + routing)    │
  │     ├── Batch 4 (Market context)      │
  │     ├── Batch 5 (Scanner + triage) ◄──┘ (needs OHLCV API)
  │     │     └── Batch 6 (Analysis workspace)
  │     ├── Batch 7 (Command palette)
  │     └── Batch 8 (Portfolio workspace)
  └── Batch 9 (Polish + delete Streamlit)
```

**Parallelizable pairs:**
- Batch 2 (backend) + Batch 3 (layout shell) — independent
- Batch 4 (market) + Batch 5.1-5.3 (scanner table, no chart yet) — independent
- Batch 7 (command palette) + Batch 8 (portfolio) — independent
- Task 9.1 (delete Streamlit) can run anytime after Batch 2

---

## Implementation Notes

1. **SMA computation on client**: Since we fetch all OHLCV data at once, compute SMA 50/150/200 in the browser. No need for a server-side endpoint.

2. **Weekly/monthly chart resampling**: Group bars by ISO week (Mon-Sun) or month. Take first open, max high, min low, last close, sum volume.

3. **Setup row merging**: Port the logic from `src/sepa_scanner/ui/setup_models.py` to TypeScript. The `build_setup_rows` function merges signals + watchlist + reviews into a unified list. The frontend version uses signals from API + reviews from Zustand store.

4. **Diagnostics → TrendTemplate + VCPMetrics**: The `/api/analysis/{symbol}/diagnostics` endpoint returns the `daily_diagnostics` row which has all `tt1`-`tt9` booleans and `vcp1`-`vcp6` booleans. Map these to the TrendTemplate and VCPMetrics display components.

5. **Chart performance**: Lightweight Charts handles thousands of bars efficiently. No virtualization needed. For 16 years of daily data (~4000 bars), it renders smoothly.

6. **Prefetching**: When user is on setup N, prefetch OHLCV for setups N+1 and N+2 via `queryClient.prefetchQuery()`.

// --- Signal / Scanner ---
export interface Signal {
  signalDate: string // YYYY-MM-DD
  symbol: string
  signalType: 'BREAKOUT' | 'WATCH'
  close: number
  pivot: number
  nearPivot: number // % distance from pivot
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
  id: string // "symbol:scanDate"
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
  date: string // YYYY-MM-DD
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// --- Market Context (mock for now) ---
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
  fiiToday: number // crores
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
export interface TrendTemplateCondition {
  label: string
  pass: boolean
  value: string
}

export interface TrendTemplateResult {
  conditions: TrendTemplateCondition[]
  passCount: number
}

export interface VCPMetrics {
  contractionCount: number
  contractionDepths: number[] // e.g. [22, 11, 5]
  qualityRatio: number
  baseDepth: number
  baseDuration: number // days
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
export type PositionStage =
  | 'shortlisted'
  | 'watching'
  | 'open'
  | 'managing'
  | 'closed'
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

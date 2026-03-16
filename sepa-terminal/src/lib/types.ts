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

// --- Chart config ---
export type ChartTimeframe = 'D' | 'W'

export interface ChartOverlays {
  sma50: boolean
  sma150: boolean
  sma200: boolean
  ema21: boolean
  trail10Low: boolean
  refLines: boolean
}

// --- Backtest types ---

export interface BacktestParams {
  startDate: string
  endDate: string
  exitMode: 'minervini_phased'
  targetR: number | null
  pendingDays: number
  maxHoldDays: number
  trailActivationR: number
  exitEngine: 'minervini_phased'
}

export interface BacktestTrade {
  symbol: string
  signalDate: string
  status: 'not_filled' | 'stopped' | 'target' | 'time_stop' | 'incomplete_data' | 'skipped_overlap' | 'skipped_split' | 'dead_money' | 'skipped_portfolio'
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
  exitPhase: string | null
  exitReason: string | null
  partialExitPrice: number | null
  partialExitDate: string | null
  partialExitR: number | null
  partialExitPct: number | null
  trailModeUsed: string | null
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
  breakevenExits: number
  partialProfitExits: number
  trailExits: number
  tightenExits: number
  climaxSells: number
  extensionSells: number
  avgPartialR: number | null
  pctReachingBreakeven: number
  pctReachingPartial: number
  pctReachingTrail: number
  exitEngine: string
}

export interface EquityCurvePoint {
  exitDate: string
  symbol: string
  rMultiple: number
  cumulativeR: number
  runningPeak: number
  drawdown: number
}

export interface DayReachedBreakdown {
  day: number
  count: number
  pctOfReached: number
}

export interface PerDayReturn {
  day: number
  avgReturnPct: number
  positiveCount: number
  total: number
  positivePct: number
}

export interface GapAnalysis {
  gapUpCount: number
  total: number
  gapUpPct: number
  avgGapPct: number
  bigGapUpCount: number
  bigGapDownCount: number
}

export interface UnfilledAnalysis {
  count: number
  avgMaxGainPct: number
  avgLastDayReturnPct: number
  profitableAtLastDay: number
  profitableAtLastDayPct: number
}

export interface PostEntryReturn {
  day: number
  avgReturnPct: number
  profitableCount: number
  total: number
  profitablePct: number
}

export interface SignalAnalysis {
  totalWithData: number
  pivotReached: number
  pivotNotReached: number
  pivotReachedPct: number
  dayReachedBreakdown: DayReachedBreakdown[]
  profitableAtLastDay: number
  profitableAtLastDayPct: number
  avgLastDayReturnPct: number
  medianLastDayReturnPct: number
  avgMaxGainPct: number
  avgMaxLossPct: number
  perDayReturns: PerDayReturn[]
  gapAnalysis: GapAnalysis
  unfilledAnalysis: UnfilledAnalysis
  postEntryReturns?: PostEntryReturn[]
}

export interface BacktestReport {
  params: BacktestParams
  summary: BacktestSummary
  trades: BacktestTrade[]
  equityCurve: EquityCurvePoint[]
  signalAnalysis?: SignalAnalysis
}

export interface SymbolSignalHistory {
  signals: Signal[]
  trades: BacktestTrade[]
}

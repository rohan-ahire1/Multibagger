// --- Scanner ---
export interface Signal {
  signalDate: string
  symbol: string
  signalType: 'BREAKOUT' | 'WATCH'
  close: number
  pivot: number
  nearPivot: number
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

export interface MarketBreadth {
  date: string
  aboveSma50: number
  totalEligible: number
  breadth: number
  regime: 'Strong' | 'Neutral' | 'Weak'
  marketFilterOn: boolean
}

export interface ScanSessionSummary {
  date: string
  breakoutCount: number
  watchCount: number
  breakoutSymbols: string[]
}

// --- Triage ---
export type ReviewStatus = 'new' | 'shortlisted' | 'watching' | 'rejected'

export interface SetupRow {
  id: string // "symbol:scanDate"
  scanDate: string
  symbol: string
  signalType: 'BREAKOUT' | 'WATCH' | null
  close: number
  pivot: number
  nearPivot: number
  rsRank: number
  volMult: number | null
  entryRef: number | null
  stop: number | null
  rPerShare: number | null
  range10: number | null
  voldry: number | null
  atrPct: number | null
  reviewStatus: ReviewStatus
  note: string
}

// --- OHLCV ---
export interface OHLCVBar {
  date: string
  symbol?: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// --- Analysis ---
export interface DailyDiagnostics {
  diagDate: string
  symbol: string
  eligible: boolean
  ttPass: boolean
  tt1CloseAboveSma150: boolean
  tt2CloseAboveSma200: boolean
  tt3Sma150AboveSma200: boolean
  tt4Sma200Rising: boolean
  tt5Sma50AboveSma150: boolean
  tt6Sma50AboveSma200: boolean
  tt7CloseAboveSma50: boolean
  tt8Above52wLow: boolean
  tt9Near52wHigh: boolean
  rsRank: number
  rsPass: boolean
  setupPass: boolean
  vcp1Range10Ok: boolean
  vcp2VoldryOk: boolean
  vcp3AtrTrendOk: boolean
  vcp4AtrContractOk: boolean
  vcp5ExtensionOk: boolean
  vcp6BaseDepthOk: boolean
  contractionCount: number
  contractionQuality: number
  pivot: number
  nearPivot: number
  setupActiveStreak: number
  pivotAgeDays: number
  contractionSequence?: number[]
  contractionDates?: string[]
  contractionSwings?: { date: string; price: number; type: 'high' | 'low' }[]
}

export interface ExplanationItem {
  text: string
  detail: string
  severity?: 'info' | 'warning'
}

export interface ExplanationMetric {
  label: string
  value: string
  context: string
}

export interface SetupExplanation {
  symbol: string
  date: string
  signalType: 'BREAKOUT' | 'WATCH'
  narrative: string
  strengths: ExplanationItem[]
  risks: ExplanationItem[]
  nextActions: ExplanationItem[]
  metrics: ExplanationMetric[]
  contractionSequence?: number[]
  contractionDates?: string[]
  contractionSwings?: { date: string; price: number; type: 'high' | 'low' }[]
}

// --- Backtest ---
export interface BacktestRunRequest {
  startDate: string
  endDate: string
  exitEngine: 'minervini_phased' | 'advanced_sepa'
  targetR: number | null
  pendingDays: number | null
  maxHoldDays: number | null
  usePortfolio: boolean
}

export type TradeStatus =
  | 'open'
  | 'pending_entry'
  | 'not_filled'
  | 'stopped'
  | 'target'
  | 'time_stop'
  | 'incomplete_data'
  | 'skipped_overlap'
  | 'skipped_split'
  | 'dead_money'
  | 'skipped_portfolio'

export interface BacktestTrade {
  symbol: string
  signalDate: string
  status: TradeStatus
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

export interface BacktestReport {
  params: Record<string, unknown>
  summary: BacktestSummary
  trades: BacktestTrade[]
  equityCurve: EquityCurvePoint[]
}

export interface SymbolSignalHistory {
  signals: Signal[]
  trades: BacktestTrade[]
  pendingDays: number
  unfilledOutcomes: Record<string, UnfilledSignalOutcome>
}

export interface UnfilledSignalOutcome {
  signalDate: string
  entryRef: number
  stop: number
  pivot: number | null
  endDate: string
  endPrice: number
  holdingBars: number
  returnPct: number | null
  rMultiple: number | null
}

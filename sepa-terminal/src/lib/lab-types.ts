// Types for the strategy lab. Field names are camelCase because apiFetch runs
// snakeToCamel over every response.

export type ParamType = 'int' | 'float' | 'bool' | 'enum' | 'date' | 'str'

export type StrategyKind = 'signal' | 'rebalance' | 'reversion' | 'portfolio'

export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface ParamSpec {
  name: string
  label: string
  type: ParamType
  default: unknown
  min: number | null
  max: number | null
  step: number | null
  choices: string[] | null
  group: string
  help: string
  advanced: boolean
}

export interface LabStrategy {
  id: string
  name: string
  kind: StrategyKind
  description: string
  dataRequirement: 'signals' | 'panel'
  paramSpecs: ParamSpec[]
  defaults: Record<string, unknown>
}

export interface RunMetrics {
  cagrPct: number
  totalReturnPct: number
  terminalEquity: number
  maxDrawdownPct: number
  maxDrawdownDays: number
  calmar: number | null
  sharpe: number | null
  sortino: number | null
  volatilityPct: number
  exposurePct: number | null
  benchmarkCagrPct: number | null
  excessCagrPct: number | null
  trades: number | null
  winRate: number | null
  avgR: number | null
  expectancyR: number | null
  payoffRatio: number | null
  avgHoldDays: number | null
  turnoverPct: number | null
  cashPct: number | null
  avgHoldings: number | null
  avgHoldingDays: number | null
  costDragPct: number | null
  slippageDragPct: number | null
}

export interface LabRun {
  runId: string
  strategyId: string
  label: string | null
  paramsHash: string
  startDate: string
  endDate: string
  kind: StrategyKind
  dataSource: string
  status: RunStatus
  progressPct: number
  stage: string | null
  error: string | null
  createdAt: string
  finishedAt: string | null
  params: Record<string, unknown> | null
  metrics: RunMetrics | null
  extras: Record<string, unknown> | null
  warnings: string[] | null
}

export interface EquityPoint {
  date: string
  equity: number
  drawdownPct: number
  benchmarkEquity: number | null
}

export interface LabTrade {
  symbol: string
  direction: string
  signalDate: string | null
  entryDate: string | null
  exitDate: string | null
  entryPrice: number | null
  exitPrice: number | null
  shares: number | null
  stop: number | null
  rMultiple: number | null
  pnl: number | null
  pnlPct: number | null
  mfeR: number | null
  maeR: number | null
  holdDays: number | null
  exitReason: string | null
  status: string
  sector: string | null
  meta: Record<string, unknown>
}

export interface LabRunDetail {
  run: LabRun
  equityCurve: EquityPoint[]
  trades: LabTrade[]
}

export interface RunStatusPayload {
  runId: string
  status: RunStatus
  progressPct: number
  stage: string | null
  error: string | null
}

export interface CompareSeries {
  runId: string
  strategyId: string
  label: string | null
  metrics: RunMetrics | null
  points: { date: string; value: number | null; drawdownPct: number }[]
}

export interface CompareResult {
  series: CompareSeries[]
  disjointRunIds: string[]
  commonDays: number
  warnings: string[]
}

export interface CreateRunRequest {
  strategyId: string
  startDate: string
  endDate: string
  params: Record<string, unknown>
  label?: string | null
}

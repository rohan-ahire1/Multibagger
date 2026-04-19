import { create } from 'zustand'
import type { BacktestParams, BacktestReport } from '@/lib/types'

export interface SelectedTrade {
  symbol: string
  signalDate: string
}

/** Data available from 2010-01-01 */
export const DATA_START_DATE = '2010-01-01'

function defaultParams(): BacktestParams {
  return {
    startDate: DATA_START_DATE,
    endDate: '2026-03-24', // Full period to match 75x benchmark
    exitMode: 'advanced_sepa',
    targetR: null,
    pendingDays: 2,
    maxHoldDays: 21,
    trailActivationR: 1.5,
    exitEngine: 'advanced_sepa',
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

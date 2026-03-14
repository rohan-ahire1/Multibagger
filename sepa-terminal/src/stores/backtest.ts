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

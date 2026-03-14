import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Position, PositionStage, StopMethod, PortfolioStats } from '@/lib/types'
import {
  generateMockPortfolio,
  generateMockPortfolioStats,
} from '@/lib/mock-data'

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
          positions: s.positions.map((p) =>
            p.id === id ? { ...p, stage } : p
          ),
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
              ? {
                  ...p,
                  stage: 'closed' as const,
                  exitPrice,
                  exitReason: reason,
                  exitDate: new Date().toISOString().slice(0, 10),
                }
              : p
          ),
        })),
    }),
    { name: 'sepa-portfolio' }
  )
)

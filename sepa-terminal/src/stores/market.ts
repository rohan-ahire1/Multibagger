import { create } from 'zustand'
import type { MarketRegime, SectorPerformance } from '@/lib/types'
import { generateMockRegime, generateMockSectors } from '@/lib/mock-data'

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

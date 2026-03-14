import { create } from 'zustand'
import type { QueueType } from '@/lib/types'

interface ScannerStore {
  activeQueue: QueueType
  selectedSetupId: string | null // "symbol:scanDate"
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
    if (!selectedSetupId) {
      set({ selectedSetupId: ids[0] })
      return
    }
    const idx = ids.indexOf(selectedSetupId)
    const next = idx < ids.length - 1 ? ids[idx + 1] : ids[0]
    set({ selectedSetupId: next })
  },
  selectPrev: (ids) => {
    const { selectedSetupId } = get()
    if (!ids.length) return
    if (!selectedSetupId) {
      set({ selectedSetupId: ids[ids.length - 1] })
      return
    }
    const idx = ids.indexOf(selectedSetupId)
    const prev = idx > 0 ? ids[idx - 1] : ids[ids.length - 1]
    set({ selectedSetupId: prev })
  },
}))

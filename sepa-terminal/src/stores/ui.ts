import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChartTimeframe, ChartOverlays } from '@/lib/types'

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
    {
      name: 'sepa-ui',
      partialize: (s) => ({
        panelSizes: s.panelSizes,
        chartOverlays: s.chartOverlays,
      }),
    }
  )
)

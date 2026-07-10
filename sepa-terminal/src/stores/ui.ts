import { create } from 'zustand'

interface UIStore {
  scanDate: string | null // null until dates load; then latest by default
  commandPaletteOpen: boolean
  shortcutOverlayOpen: boolean
  setScanDate: (d: string) => void
  setCommandPaletteOpen: (open: boolean) => void
  toggleCommandPalette: () => void
  setShortcutOverlayOpen: (open: boolean) => void
  toggleShortcutOverlay: () => void
}

export const useUIStore = create<UIStore>((set) => ({
  scanDate: null,
  commandPaletteOpen: false,
  shortcutOverlayOpen: false,
  setScanDate: (d) => set({ scanDate: d }),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleCommandPalette: () => set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
  setShortcutOverlayOpen: (open) => set({ shortcutOverlayOpen: open }),
  toggleShortcutOverlay: () => set((s) => ({ shortcutOverlayOpen: !s.shortcutOverlayOpen })),
}))

import { create } from 'zustand'

export type Theme = 'light' | 'dark'

interface UIStore {
  scanDate: string | null // null until dates load; then latest by default
  theme: Theme
  commandPaletteOpen: boolean
  shortcutOverlayOpen: boolean
  setScanDate: (d: string) => void
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setCommandPaletteOpen: (open: boolean) => void
  toggleCommandPalette: () => void
  setShortcutOverlayOpen: (open: boolean) => void
  toggleShortcutOverlay: () => void
}

const THEME_STORAGE_KEY = 'sepa-terminal-theme'

function getInitialTheme(): Theme {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Theme still applies when storage is unavailable.
  }
}

export const useUIStore = create<UIStore>((set) => ({
  scanDate: null,
  theme: getInitialTheme(),
  commandPaletteOpen: false,
  shortcutOverlayOpen: false,
  setScanDate: (d) => set({ scanDate: d }),
  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
  },
  toggleTheme: () =>
    set((state) => {
      const theme = state.theme === 'light' ? 'dark' : 'light'
      applyTheme(theme)
      return { theme }
    }),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleCommandPalette: () => set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
  setShortcutOverlayOpen: (open) => set({ shortcutOverlayOpen: open }),
  toggleShortcutOverlay: () => set((s) => ({ shortcutOverlayOpen: !s.shortcutOverlayOpen })),
}))

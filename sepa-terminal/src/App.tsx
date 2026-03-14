import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useHotkeys } from 'react-hotkeys-hook'
import { TopBar } from '@/components/layout/TopBar'
import { RegimeStrip } from '@/components/layout/RegimeStrip'
import { ShortcutBar } from '@/components/layout/ShortcutBar'
import { CommandPalette } from '@/components/shared/CommandPalette'
import { ShortcutOverlay } from '@/components/shared/ShortcutOverlay'
import { MarketScanner } from '@/components/workspaces/MarketScanner'
import { Analysis } from '@/components/workspaces/Analysis'
import { Portfolio } from '@/components/workspaces/Portfolio'
import { Backtest } from '@/components/workspaces/Backtest'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceSwitch } from '@/hooks/useWorkspaceSwitch'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 2,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
    },
  },
})

function AppContent() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const commandPaletteOpen = useUIStore((s) => s.commandPaletteOpen)
  const shortcutOverlayOpen = useUIStore((s) => s.shortcutOverlayOpen)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const toggleShortcutOverlay = useUIStore((s) => s.toggleShortcutOverlay)
  useWorkspaceSwitch()
  useHotkeys('mod+k', () => toggleCommandPalette(), { preventDefault: true })
  useHotkeys('shift+/', () => toggleShortcutOverlay(), { preventDefault: true })

  return (
    <div className="h-screen flex flex-col bg-bg-primary overflow-hidden">
      <TopBar />
      <RegimeStrip />
      {activeWorkspace === 1 && <MarketScanner />}
      {activeWorkspace === 2 && <Analysis />}
      {activeWorkspace === 3 && <Portfolio />}
      {activeWorkspace === 4 && <Backtest />}
      <ShortcutBar />
      {commandPaletteOpen && <CommandPalette />}
      {shortcutOverlayOpen && <ShortcutOverlay />}
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  )
}

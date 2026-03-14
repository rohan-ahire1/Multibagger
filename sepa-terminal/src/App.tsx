import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useHotkeys } from 'react-hotkeys-hook'
import { TopBar } from '@/components/layout/TopBar'
import { ShortcutBar } from '@/components/layout/ShortcutBar'
import { CommandPalette } from '@/components/shared/CommandPalette'
import { MarketScanner } from '@/components/workspaces/MarketScanner'
import { Analysis } from '@/components/workspaces/Analysis'
import { Portfolio } from '@/components/workspaces/Portfolio'
import { useUIStore } from '@/stores/ui'
import { useWorkspaceSwitch } from '@/hooks/useWorkspaceSwitch'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

function AppContent() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const commandPaletteOpen = useUIStore((s) => s.commandPaletteOpen)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  useWorkspaceSwitch()
  useHotkeys('mod+k', () => toggleCommandPalette(), { preventDefault: true })

  return (
    <div className="h-screen flex flex-col bg-bg-primary overflow-hidden">
      <TopBar />
      {activeWorkspace === 1 && <MarketScanner />}
      {activeWorkspace === 2 && <Analysis />}
      {activeWorkspace === 3 && <Portfolio />}
      <ShortcutBar />
      {commandPaletteOpen && <CommandPalette />}
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

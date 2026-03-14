import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TopBar } from '@/components/layout/TopBar'
import { ShortcutBar } from '@/components/layout/ShortcutBar'
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
  useWorkspaceSwitch()

  return (
    <div className="h-screen flex flex-col bg-bg-primary overflow-hidden">
      <TopBar />
      {activeWorkspace === 1 && <MarketScanner />}
      {activeWorkspace === 2 && <Analysis />}
      {activeWorkspace === 3 && <Portfolio />}
      <ShortcutBar />
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

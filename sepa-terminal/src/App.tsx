import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router'
import { useHotkeys } from 'react-hotkeys-hook'
import { TopBar } from '@/components/layout/TopBar'
import { CommandPalette } from '@/components/layout/CommandPalette'
import { ShortcutOverlay } from '@/components/layout/ShortcutOverlay'
import { Dashboard } from '@/pages/Dashboard'
import { Scanner } from '@/pages/Scanner'
import { SymbolPage } from '@/pages/Symbol'
import { Backtest } from '@/pages/Backtest'
import { useUIStore } from '@/stores/ui'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function Shell() {
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const toggleShortcutOverlay = useUIStore((s) => s.toggleShortcutOverlay)
  const navigate = useNavigate()

  useHotkeys('mod+k', () => toggleCommandPalette(), { preventDefault: true })
  useHotkeys('shift+slash', () => toggleShortcutOverlay(), { preventDefault: true })
  useHotkeys('g>d', () => navigate('/'))
  useHotkeys('g>s', () => navigate('/scanner'))
  useHotkeys('g>b', () => navigate('/backtest'))

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/symbol/:symbol" element={<SymbolPage />} />
          <Route path="/backtest" element={<Backtest />} />
        </Routes>
      </main>
      <CommandPalette />
      <ShortcutOverlay />
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  )
}

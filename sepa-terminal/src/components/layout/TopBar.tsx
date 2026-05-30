import { useBacktestStore } from '@/stores/backtest'
import { useUIStore } from '@/stores/ui'
import { formatDate } from '@/lib/formatters'
import { useMarketBreadth } from '@/api/useScannerData'

const WORKSPACES = [
  { id: 1 as const, label: 'Scanner' },
  { id: 2 as const, label: 'Backtest' },
]

function describeActiveRange(
  activeWorkspace: 1 | 2,
  scanDate: string,
  startDate: string,
  endDate: string,
): string {
  if (activeWorkspace === 1) {
    return scanDate ? `Scan ${formatDate(scanDate)}` : 'Scan date unavailable'
  }
  return `${formatDate(startDate)} to ${formatDate(endDate)}`
}

export function TopBar() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const setWorkspace = useUIStore((s) => s.setWorkspace)
  const scanDate = useUIStore((s) => s.scanDate)
  const backtestParams = useBacktestStore((s) => s.params)
  const activeRange = describeActiveRange(
    activeWorkspace,
    scanDate,
    backtestParams.startDate,
    backtestParams.endDate,
  )

  const { data: breadthData } = useMarketBreadth(
    activeWorkspace === 1 ? scanDate : ''
  )

  let breadthIndicator = null
  if (activeWorkspace === 1 && breadthData) {
    const colorClass =
      breadthData.regime === 'Strong'
        ? 'text-green border-green/30 bg-green-bg'
        : breadthData.regime === 'Weak'
        ? 'text-red border-red/30 bg-red-bg'
        : 'text-amber border-amber/30 bg-amber-bg'
    breadthIndicator = (
      <span className={`text-[10px] uppercase font-mono border px-1.5 py-0.5 rounded ${colorClass}`}>
        Breadth: {(breadthData.breadth * 100).toFixed(0)}% ({breadthData.regime})
      </span>
    )
  }

  return (
    <div className="h-10 bg-bg-secondary border-b border-border-default px-4 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-2">
        <span className="text-[15px] font-medium text-text-primary">
          Multibagger Terminal
        </span>
        <span className="text-xs bg-green-bg text-green px-1.5 rounded">
          v2
        </span>
      </div>

      <div className="flex items-center gap-1">
        {WORKSPACES.map((ws) => {
          const isActive = activeWorkspace === ws.id
          return (
            <button
              key={ws.id}
              onClick={() => setWorkspace(ws.id)}
              className={`px-3 py-2 text-xs transition-colors ${
                isActive
                  ? 'text-text-primary border-b-2 border-green'
                  : 'text-text-dim hover:text-text-muted'
              }`}
            >
              {ws.label}
            </button>
          )
        })}
      </div>

      <div className="flex items-center gap-3">
        {breadthIndicator}
        <span className="text-xs text-text-muted">
          {activeRange}
        </span>
        <kbd className="text-xs text-text-faint border border-border-strong rounded px-1.5 font-mono">
          Cmd+K
        </kbd>
      </div>
    </div>
  )
}

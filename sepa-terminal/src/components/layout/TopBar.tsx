import { useUIStore } from '@/stores/ui'

const WORKSPACES = [
  { id: 1 as const, label: 'Market + Scanner' },
  { id: 2 as const, label: 'Analysis' },
  { id: 3 as const, label: 'Portfolio' },
]

function formatDate(date: Date): string {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ]
  return `${date.getDate()} ${months[date.getMonth()]} ${date.getFullYear()}`
}

export function TopBar() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const setWorkspace = useUIStore((s) => s.setWorkspace)

  return (
    <div className="h-10 bg-bg-secondary border-b border-border-default px-4 flex items-center justify-between shrink-0">
      {/* Left: Logo */}
      <div className="flex items-center gap-2">
        <span className="text-[15px] font-medium text-text-primary">
          SEPA Terminal
        </span>
        <span className="text-xs bg-green-bg text-green px-1.5 rounded">
          v2
        </span>
      </div>

      {/* Center: Workspace tabs */}
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

      {/* Right: Date + shortcut hint */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-text-muted">
          {formatDate(new Date())}
        </span>
        <kbd className="text-xs text-text-faint border border-border-strong rounded px-1.5 font-mono">
          Cmd+K
        </kbd>
      </div>
    </div>
  )
}

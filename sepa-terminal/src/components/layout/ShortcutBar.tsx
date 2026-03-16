import { useUIStore } from '@/stores/ui'

interface ShortcutHint {
  key: string
  desc: string
}

const WORKSPACE_SHORTCUTS: Record<1 | 2, ShortcutHint[]> = {
  1: [
    { key: 'J K', desc: 'navigate' },
    { key: 'S', desc: 'shortlist' },
    { key: 'X', desc: 'reject' },
    { key: 'W', desc: 'watch' },
    { key: 'N', desc: 'note' },
    { key: '⌘K', desc: 'command' },
  ],
  2: [
    { key: '[ ]', desc: 'range' },
    { key: 'J K', desc: 'trade' },
    { key: 'D W', desc: 'timeframe' },
    { key: 'R', desc: 'run' },
    { key: '⌘K', desc: 'command' },
  ],
}

export function ShortcutBar() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const shortcuts = WORKSPACE_SHORTCUTS[activeWorkspace]

  return (
    <div
      className="h-[30px] px-4 flex items-center shrink-0 border-t border-border-default"
      style={{ backgroundColor: '#0B0D13' }}
    >
      <div className="flex items-center">
        {shortcuts.map((hint, i) => (
          <span key={hint.key} className={`flex items-center gap-1 ${i > 0 ? 'mx-3' : 'mr-3'}`}>
            <kbd className="text-[10px] font-mono border border-border-strong rounded px-1 text-text-faint">
              {hint.key}
            </kbd>
            <span className="text-[11px] text-text-faint">{hint.desc}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

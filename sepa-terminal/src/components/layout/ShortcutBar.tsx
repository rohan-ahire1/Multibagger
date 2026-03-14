import { useUIStore } from '@/stores/ui'

interface ShortcutHint {
  key: string
  desc: string
}

const WORKSPACE_SHORTCUTS: Record<1 | 2 | 3, ShortcutHint[]> = {
  1: [
    { key: 'J K', desc: 'navigate' },
    { key: 'S', desc: 'shortlist' },
    { key: 'X', desc: 'reject' },
    { key: 'W', desc: 'watch' },
    { key: 'N', desc: 'note' },
    { key: '⌘K', desc: 'command' },
  ],
  2: [
    { key: 'Esc', desc: 'back' },
    { key: 'D W', desc: 'timeframe' },
    { key: 'S', desc: 'shortlist' },
    { key: 'N', desc: 'thesis' },
    { key: '⌘K', desc: 'command' },
  ],
  3: [
    { key: 'J K', desc: 'navigate' },
    { key: 'E', desc: 'edit' },
    { key: 'T', desc: 'trail stop' },
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
      {/* Left: Shortcut hints */}
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

      {/* Right: Progress (workspace 1 only) */}
      {activeWorkspace === 1 && (
        <span className="ml-auto text-[11px] text-text-faint">
          0 of 0 reviewed
        </span>
      )}
    </div>
  )
}

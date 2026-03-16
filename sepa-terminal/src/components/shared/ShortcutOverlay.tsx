import { useEffect, useCallback } from 'react'
import { useUIStore } from '@/stores/ui'

interface ShortcutGroup {
  title: string
  shortcuts: { keys: string[]; description: string }[]
}

const scannerShortcuts: ShortcutGroup[] = [
  {
    title: 'Navigation',
    shortcuts: [
      { keys: ['J'], description: 'Next setup' },
      { keys: ['K'], description: 'Previous setup' },
    ],
  },
  {
    title: 'Triage',
    shortcuts: [
      { keys: ['S'], description: 'Shortlist' },
      { keys: ['X'], description: 'Reject' },
      { keys: ['W'], description: 'Watch' },
      { keys: ['⌘Z'], description: 'Undo last action' },
    ],
  },
  {
    title: 'Other',
    shortcuts: [
      { keys: ['N'], description: 'Focus note' },
      { keys: ['⌘K'], description: 'Command palette' },
      { keys: ['?'], description: 'Show shortcuts' },
    ],
  },
]

const backtestShortcuts: ShortcutGroup[] = [
  {
    title: 'Navigation',
    shortcuts: [
      { keys: ['J'], description: 'Next trade' },
      { keys: ['K'], description: 'Previous trade' },
    ],
  },
  {
    title: 'Chart',
    shortcuts: [
      { keys: ['D'], description: 'Daily timeframe' },
      { keys: ['W'], description: 'Weekly timeframe' },
    ],
  },
  {
    title: 'Actions',
    shortcuts: [
      { keys: ['R'], description: 'Run backtest' },
      { keys: ['⌘K'], description: 'Command palette' },
      { keys: ['?'], description: 'Show shortcuts' },
    ],
  },
]

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 bg-bg-elevated border border-border-strong rounded text-[11px] font-mono text-text-secondary">
      {children}
    </kbd>
  )
}

function ShortcutRow({ keys, description }: { keys: string[]; description: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-text-secondary">{description}</span>
      <div className="flex items-center gap-1">
        {keys.map((key, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <span className="text-text-dim text-xs">/</span>}
            <Kbd>{key}</Kbd>
          </span>
        ))}
      </div>
    </div>
  )
}

function ShortcutSection({ group }: { group: ShortcutGroup }) {
  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-text-faint mb-1">
        {group.title}
      </h3>
      <div className="divide-y divide-border-subtle">
        {group.shortcuts.map((shortcut, i) => (
          <ShortcutRow key={i} keys={shortcut.keys} description={shortcut.description} />
        ))}
      </div>
    </div>
  )
}

const shortcutsByWorkspace: Record<1 | 2, ShortcutGroup[]> = {
  1: scannerShortcuts,
  2: backtestShortcuts,
}

const workspaceLabels: Record<1 | 2, string> = {
  1: 'Scanner',
  2: 'Backtest',
}

export function ShortcutOverlay() {
  const activeWorkspace = useUIStore((s) => s.activeWorkspace)
  const close = useCallback(() => {
    useUIStore.setState({ shortcutOverlayOpen: false })
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [close])

  const groups = shortcutsByWorkspace[activeWorkspace]

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) close()
      }}
    >
      <div className="bg-bg-surface border border-border-strong rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-text-primary">
            Keyboard shortcuts
          </h2>
          <span className="text-[11px] text-text-dim">
            {workspaceLabels[activeWorkspace]}
          </span>
        </div>
        <div className="flex flex-col gap-4">
          {groups.map((group, i) => (
            <ShortcutSection key={i} group={group} />
          ))}
        </div>
      </div>
    </div>
  )
}

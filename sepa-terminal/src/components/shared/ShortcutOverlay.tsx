import { useEffect, useCallback } from 'react'
import { useUIStore } from '@/stores/ui'

interface ShortcutGroup {
  title: string
  shortcuts: { keys: string[]; description: string }[]
}

const workspace1Shortcuts: ShortcutGroup[] = [
  {
    title: 'Navigation',
    shortcuts: [
      { keys: ['J'], description: 'Next setup' },
      { keys: ['K'], description: 'Previous setup' },
      { keys: ['Enter', '\u2192'], description: 'Open in analysis' },
    ],
  },
  {
    title: 'Triage',
    shortcuts: [
      { keys: ['S'], description: 'Shortlist' },
      { keys: ['X'], description: 'Reject' },
      { keys: ['W'], description: 'Watch' },
      { keys: ['\u2318Z'], description: 'Undo last action' },
    ],
  },
  {
    title: 'Other',
    shortcuts: [
      { keys: ['N'], description: 'Focus note' },
      { keys: ['D'], description: 'Daily timeframe' },
      { keys: ['\u21E7W'], description: 'Weekly timeframe' },
      { keys: ['\u2318K'], description: 'Command palette' },
      { keys: ['?'], description: 'Show shortcuts' },
    ],
  },
]

const workspace2Shortcuts: ShortcutGroup[] = [
  {
    title: 'Navigation',
    shortcuts: [
      { keys: ['Esc'], description: 'Back to scanner' },
    ],
  },
  {
    title: 'Chart',
    shortcuts: [
      { keys: ['D'], description: 'Daily timeframe' },
      { keys: ['\u21E7W'], description: 'Weekly timeframe' },
      { keys: ['M'], description: 'Monthly timeframe' },
    ],
  },
  {
    title: 'Actions',
    shortcuts: [
      { keys: ['S'], description: 'Shortlist' },
      { keys: ['N'], description: 'Focus thesis' },
      { keys: ['\u2318K'], description: 'Command palette' },
      { keys: ['?'], description: 'Show shortcuts' },
    ],
  },
]

const workspace3Shortcuts: ShortcutGroup[] = [
  {
    title: 'Navigation',
    shortcuts: [
      { keys: ['J'], description: 'Next position' },
      { keys: ['K'], description: 'Previous position' },
    ],
  },
  {
    title: 'Actions',
    shortcuts: [
      { keys: ['E'], description: 'Edit position (stub)' },
      { keys: ['T'], description: 'Trail stop (stub)' },
      { keys: ['\u2318K'], description: 'Command palette' },
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
        {group.shortcuts.map((s, i) => (
          <ShortcutRow key={i} keys={s.keys} description={s.description} />
        ))}
      </div>
    </div>
  )
}

const shortcutsByWorkspace: Record<number, ShortcutGroup[]> = {
  1: workspace1Shortcuts,
  2: workspace2Shortcuts,
  3: workspace3Shortcuts,
}

const workspaceLabels: Record<number, string> = {
  1: 'Market + Scanner',
  2: 'Analysis',
  3: 'Portfolio',
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

  const groups = shortcutsByWorkspace[activeWorkspace] ?? workspace1Shortcuts

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

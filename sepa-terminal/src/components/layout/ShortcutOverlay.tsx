import { useUIStore } from '@/stores/ui'
import { Kbd } from '@/components/ui/primitives'

const SECTIONS: { title: string; rows: [string, string][] }[] = [
  {
    title: 'Global',
    rows: [
      ['⌘K', 'Command palette / symbol jump'],
      ['?', 'Toggle this overlay'],
      ['g d / g s / g b', 'Go to Dashboard / Scanner / Backtest'],
    ],
  },
  {
    title: 'Scanner triage',
    rows: [
      ['j / k or ↓ / ↑', 'Next / previous row'],
      ['s', 'Shortlist'],
      ['w', 'Watch'],
      ['x', 'Reject'],
      ['u', 'Undo last decision'],
      ['n', 'Focus note'],
      ['↵', 'Open symbol deep-dive'],
      ['1 / 2 / 3 / 4', 'Switch queue tab'],
    ],
  },
]

export function ShortcutOverlay() {
  const open = useUIStore((s) => s.shortcutOverlayOpen)
  const setOpen = useUIStore((s) => s.setShortcutOverlayOpen)

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/20"
      onClick={() => setOpen(false)}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[520px] rounded-xl border border-border bg-surface p-5 shadow-2xl"
      >
        <div className="mb-4 text-sm font-bold">Keyboard shortcuts</div>
        <div className="grid grid-cols-2 gap-6">
          {SECTIONS.map((section) => (
            <div key={section.title}>
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
                {section.title}
              </div>
              <div className="space-y-1.5">
                {section.rows.map(([keys, desc]) => (
                  <div key={keys} className="flex items-center justify-between gap-3 text-xs">
                    <span className="text-ink-soft">{desc}</span>
                    <span className="flex shrink-0 gap-1">
                      {keys.split(' / ').map((k) => (
                        <Kbd key={k}>{k}</Kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

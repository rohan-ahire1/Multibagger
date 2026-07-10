import { cn } from '@/lib/utils'
import { Kbd } from '@/components/ui/primitives'

export type QueueId = 'breakout' | 'watch' | 'shortlisted' | 'rejected'

const TABS: { id: QueueId; label: string; key: string }[] = [
  { id: 'breakout', label: 'Breakouts', key: '1' },
  { id: 'watch', label: 'Near pivot', key: '2' },
  { id: 'shortlisted', label: 'Shortlisted', key: '3' },
  { id: 'rejected', label: 'Rejected', key: '4' },
]

export function QueueTabs({
  active,
  counts,
  onChange,
}: {
  active: QueueId
  counts: Record<QueueId, number>
  onChange: (q: QueueId) => void
}) {
  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-border bg-surface px-3 py-1.5">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[13px] font-medium text-ink-soft hover:bg-surface-2',
            active === tab.id && 'bg-accent-soft text-accent hover:bg-accent-soft'
          )}
        >
          {tab.label}
          <span
            className={cn(
              'tnum rounded bg-surface-2 px-1 text-[11px] font-semibold text-ink-faint',
              active === tab.id && 'bg-accent/10 text-accent'
            )}
          >
            {counts[tab.id]}
          </span>
        </button>
      ))}
      <div className="ml-auto flex items-center gap-2 text-[11px] text-ink-faint">
        <span className="flex items-center gap-1">
          <Kbd>s</Kbd> shortlist
        </span>
        <span className="flex items-center gap-1">
          <Kbd>w</Kbd> watch
        </span>
        <span className="flex items-center gap-1">
          <Kbd>x</Kbd> reject
        </span>
        <span className="flex items-center gap-1">
          <Kbd>u</Kbd> undo
        </span>
        <span className="flex items-center gap-1">
          <Kbd>↵</Kbd> chart
        </span>
      </div>
    </div>
  )
}

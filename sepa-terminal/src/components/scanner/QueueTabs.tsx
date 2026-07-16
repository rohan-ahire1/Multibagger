import { cn } from '@/lib/utils'
import { Kbd } from '@/components/ui/primitives'
import { BookmarkCheck, Radar, Rocket, XCircle } from 'lucide-react'

export type QueueId = 'breakout' | 'watch' | 'shortlisted' | 'rejected'

const TABS = [
  { id: 'breakout', label: 'Breakouts', key: '1', icon: Rocket },
  { id: 'watch', label: 'Near pivot', key: '2', icon: Radar },
  { id: 'shortlisted', label: 'Shortlisted', key: '3', icon: BookmarkCheck },
  { id: 'rejected', label: 'Rejected', key: '4', icon: XCircle },
] satisfies { id: QueueId; label: string; key: string; icon: typeof Rocket }[]

export function QueueTabs({
  active,
  scanDate,
  counts,
  onChange,
}: {
  active: QueueId
  scanDate: string
  counts: Record<QueueId, number>
  onChange: (q: QueueId) => void
}) {
  return (
    <div className="flex shrink-0 items-center gap-1.5 px-3 py-3">
      <div className="mr-2 px-1">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">Setup desk</div>
        <div className="mt-0.5 text-[11px] text-ink-faint">{scanDate}</div>
      </div>
      {TABS.map((tab) => {
        const Icon = tab.icon
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={cn(
              'flex h-8 items-center gap-1.5 rounded-lg border border-transparent px-2.5 text-[11px] font-semibold text-ink-soft hover:border-border hover:bg-surface',
              active === tab.id && 'border-border bg-surface text-accent shadow-sm hover:bg-surface'
            )}
          >
            <Icon className="size-3.5" />
            {tab.label}
            <span
              className={cn(
                'tnum min-w-5 rounded-md bg-surface-2 px-1 text-center text-[10px] font-semibold text-ink-faint',
                active === tab.id && 'bg-accent-soft text-accent'
              )}
            >
              {counts[tab.id]}
            </span>
          </button>
        )
      })}
      <div className="ml-auto hidden items-center gap-2 text-[10px] text-ink-faint xl:flex">
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

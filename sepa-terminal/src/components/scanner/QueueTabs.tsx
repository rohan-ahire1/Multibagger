import { useScannerStore } from '@/stores/scanner'
import type { QueueType } from '@/lib/types'

const tabs: Array<{ key: QueueType; label: string }> = [
  { key: 'breakout', label: 'Breakout now' },
  { key: 'nearPivot', label: 'Near pivot' },
  { key: 'shortlisted', label: 'Shortlisted' },
  { key: 'rejected', label: 'Rejected' },
]

interface QueueTabsProps {
  counts: Record<QueueType, number>
}

export function QueueTabs({ counts }: QueueTabsProps) {
  const activeQueue = useScannerStore((s) => s.activeQueue)
  const setQueue = useScannerStore((s) => s.setQueue)

  return (
    <div className="flex items-center border-b border-border-default">
      {tabs.map((tab) => {
        const isActive = activeQueue === tab.key
        return (
          <button
            key={tab.key}
            onClick={() => setQueue(tab.key)}
            className={`px-3 py-2 text-xs font-medium transition-colors ${
              isActive
                ? 'text-text-primary border-b-2 border-green'
                : 'text-text-dim hover:text-text-muted'
            }`}
          >
            {tab.label}
            <span
              className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded ${
                isActive
                  ? 'bg-green-bg text-green'
                  : 'bg-bg-elevated text-text-dim'
              }`}
            >
              {counts[tab.key]}
            </span>
          </button>
        )
      })}
    </div>
  )
}

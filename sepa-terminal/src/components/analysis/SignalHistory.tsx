import type { SignalHistoryEntry } from '@/lib/types'
import { StatusPill } from '@/components/shared/StatusPill'
import { useMemo } from 'react'

interface SignalHistoryProps {
  entries: SignalHistoryEntry[]
}

function resultDotColor(result: SignalHistoryEntry['result']): string {
  switch (result) {
    case 'win':
      return 'bg-green'
    case 'loss':
      return 'bg-red'
    case 'open':
      return 'bg-blue'
  }
}

function signalVariant(type: SignalHistoryEntry['signalType']): 'green' | 'amber' {
  return type === 'BREAKOUT' ? 'green' : 'amber'
}

function formatResultR(entry: SignalHistoryEntry): string {
  if (entry.result === 'open') return 'open'
  const rSign = (entry.rMultiple ?? 0) >= 0 ? '+' : ''
  const pSign = (entry.returnPct ?? 0) >= 0 ? '+' : ''
  return `${rSign}${(entry.rMultiple ?? 0).toFixed(1)}R · ${pSign}${(entry.returnPct ?? 0).toFixed(1)}%`
}

function resultColor(entry: SignalHistoryEntry): string {
  if (entry.result === 'open') return 'text-blue'
  return entry.result === 'win' ? 'text-green' : 'text-red'
}

export function SignalHistory({ entries }: SignalHistoryProps) {
  const summary = useMemo(() => {
    const total = entries.length
    const wins = entries.filter((e) => e.result === 'win').length
    const winRate = total > 0 ? Math.round((wins / total) * 100) : 0
    const closed = entries.filter((e) => e.rMultiple != null)
    const avgR =
      closed.length > 0
        ? closed.reduce((sum, e) => sum + (e.rMultiple ?? 0), 0) / closed.length
        : 0
    return { total, winRate, avgR }
  }, [entries])

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        Signal history (this stock)
      </h3>
      <div className="flex flex-col gap-1.5">
        {entries.map((entry, i) => (
          <div key={i} className="flex items-center gap-2">
            <div
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${resultDotColor(entry.result)}`}
            />
            <span className="text-[11px] text-text-dim w-[70px] flex-shrink-0">
              {entry.date}
            </span>
            <StatusPill
              label={entry.signalType}
              variant={signalVariant(entry.signalType)}
            />
            <span
              className={`text-[11px] tabular-nums ml-auto ${resultColor(entry)}`}
            >
              {formatResultR(entry)}
            </span>
          </div>
        ))}
      </div>
      {entries.length > 0 && (
        <div className="mt-2 text-[10px] text-text-dim">
          {summary.total} signals &middot; {summary.winRate}% win &middot; avg{' '}
          {summary.avgR >= 0 ? '+' : ''}
          {summary.avgR.toFixed(2)}R
        </div>
      )}
    </div>
  )
}

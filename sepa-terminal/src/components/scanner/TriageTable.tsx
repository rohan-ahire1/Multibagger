import { useEffect, useRef } from 'react'
import type { SetupRow } from '@/lib/types'
import { Badge } from '@/components/ui/primitives'
import { cn, fmtNum, fmtPctRaw, fmtPrice } from '@/lib/utils'

const statusTone = {
  new: undefined,
  shortlisted: 'up',
  watching: 'accent',
  rejected: 'down',
} as const

export function TriageTable({
  rows,
  selectedId,
  onSelect,
  onOpen,
}: {
  rows: SetupRow[]
  selectedId: string | null
  onSelect: (id: string) => void
  onOpen: (symbol: string) => void
}) {
  const selectedRef = useRef<HTMLTableRowElement>(null)

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 z-10 bg-surface-2 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:text-right [&>th]:font-semibold">
          <th className="!text-left !pl-4">Symbol</th>
          <th className="!text-left">Type</th>
          <th>Close</th>
          <th>Pivot</th>
          <th>vs pivot</th>
          <th>RS</th>
          <th>Vol×</th>
          <th>10d range</th>
          <th>Vol dry</th>
          <th>ATR%</th>
          <th className="!pr-4">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const isSelected = row.id === selectedId
          return (
            <tr
              key={row.id}
              ref={isSelected ? selectedRef : undefined}
              onClick={() => onSelect(row.id)}
              onDoubleClick={() => onOpen(row.symbol)}
              className={cn(
                'cursor-pointer border-b border-border hover:bg-surface-2',
                isSelected && 'bg-accent-soft hover:bg-accent-soft'
              )}
            >
              <td className="px-2 py-1.5 pl-4 font-semibold">{row.symbol}</td>
              <td className="px-2 py-1.5">
                {row.signalType ? (
                  <Badge tone={row.signalType === 'BREAKOUT' ? 'up' : 'accent'}>
                    {row.signalType}
                  </Badge>
                ) : (
                  <Badge>CANDIDATE</Badge>
                )}
              </td>
              <td className="tnum px-2 py-1.5 text-right">{fmtPrice(row.close)}</td>
              <td className="tnum px-2 py-1.5 text-right">{fmtPrice(row.pivot)}</td>
              {/* near_pivot is (pivot - close)/pivot: positive = below pivot. Flip so + means above. */}
              <td
                className={cn(
                  'tnum px-2 py-1.5 text-right',
                  row.nearPivot <= 0 ? 'font-semibold text-up' : 'text-ink-soft'
                )}
              >
                {fmtPctRaw(-row.nearPivot * 100, 1)}
              </td>
              <td className="tnum px-2 py-1.5 text-right font-semibold">
                {Math.round(row.rsRank)}
              </td>
              <td className="tnum px-2 py-1.5 text-right">
                {row.volMult != null ? `${fmtNum(row.volMult, 1)}×` : '—'}
              </td>
              <td className="tnum px-2 py-1.5 text-right">{fmtPctRaw((row.range10 ?? 0) * 100, 1)}</td>
              <td className="tnum px-2 py-1.5 text-right">{fmtNum(row.voldry)}</td>
              <td className="tnum px-2 py-1.5 text-right">{fmtPctRaw((row.atrPct ?? 0) * 100, 1)}</td>
              <td className="px-2 py-1.5 pr-4 text-right">
                {row.reviewStatus !== 'new' && (
                  <Badge tone={statusTone[row.reviewStatus]}>{row.reviewStatus}</Badge>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

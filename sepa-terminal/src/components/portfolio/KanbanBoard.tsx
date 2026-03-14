import type { Position, PositionStage } from '@/lib/types'
import { formatINRCompact } from '@/lib/formatters'
import { pnlColor } from '@/lib/colors'
import { KanbanCard } from './KanbanCard'

interface KanbanBoardProps {
  positions: Position[]
}

const COLUMNS: { stage: PositionStage; title: string }[] = [
  { stage: 'shortlisted', title: 'Shortlisted' },
  { stage: 'watching', title: 'Watching' },
  { stage: 'open', title: 'Open' },
  { stage: 'managing', title: 'Managing' },
  { stage: 'closed', title: 'Closed' },
]

function columnAggregate(stage: PositionStage, positions: Position[]): React.ReactNode {
  if (stage === 'open' || stage === 'managing') {
    const totalPnl = positions.reduce((sum, p) => sum + p.unrealizedPnl, 0)
    return (
      <span className="text-[10px] tabular-nums" style={{ color: pnlColor(totalPnl) }}>
        {formatINRCompact(totalPnl)}
      </span>
    )
  }
  if (stage === 'closed') {
    const avgR =
      positions.length > 0
        ? positions.reduce((sum, p) => sum + p.rMultiple, 0) / positions.length
        : 0
    return (
      <span className="text-[10px] tabular-nums" style={{ color: pnlColor(avgR) }}>
        {avgR >= 0 ? '+' : ''}{avgR.toFixed(1)}R avg
      </span>
    )
  }
  return null
}

export function KanbanBoard({ positions }: KanbanBoardProps) {
  const grouped = COLUMNS.map((col) => ({
    ...col,
    items: positions.filter((p) => p.stage === col.stage),
  }))

  return (
    <div className="flex-1 grid grid-cols-5 gap-0 min-h-0">
      {grouped.map((col, i) => (
        <div
          key={col.stage}
          className={`flex flex-col min-h-0${i < grouped.length - 1 ? ' border-r border-border-default' : ''}`}
        >
          {/* Column header */}
          <div className="bg-bg-secondary px-3 py-2 border-b border-border-default flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-text-muted capitalize">
                {col.title}
              </span>
              <span className="text-[10px] bg-bg-elevated text-text-dim px-1.5 rounded">
                {col.items.length}
              </span>
            </div>
            {columnAggregate(col.stage, col.items)}
          </div>

          {/* Column body */}
          <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
            {col.items.map((pos) => (
              <KanbanCard key={pos.id} position={pos} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

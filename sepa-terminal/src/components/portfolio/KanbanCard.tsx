import type { Position } from '@/lib/types'
import { formatPrice, formatPct, formatR, formatINRCompact } from '@/lib/formatters'
import { pnlColor, COLORS } from '@/lib/colors'
import { StatusPill } from '@/components/shared/StatusPill'

interface KanbanCardProps {
  position: Position
}

function InfoRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between text-[11px]">
      <span className="text-text-dim">{label}</span>
      <span className="tabular-nums" style={color ? { color } : undefined}>
        {value}
      </span>
    </div>
  )
}

function timeAgo(dateStr: string): string {
  if (!dateStr) return ''
  const now = new Date()
  const then = new Date(dateStr)
  const diffMs = now.getTime() - then.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  if (diffDays < 1) return 'today'
  if (diffDays === 1) return '1 day ago'
  if (diffDays < 30) return `${diffDays} days ago`
  const diffMonths = Math.floor(diffDays / 30)
  if (diffMonths === 1) return '1 month ago'
  return `${diffMonths} months ago`
}

function ShortlistedCard({ position }: KanbanCardProps) {
  const riskReward =
    position.entryPrice && position.initialStop
      ? (
          (position.currentPrice - position.entryPrice) /
          (position.entryPrice - position.initialStop)
        ).toFixed(1)
      : '—'

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[13px] font-medium">{position.symbol}</span>
        <StatusPill label="BREAK" variant="green" />
      </div>
      <div className="space-y-1">
        <InfoRow label="Entry" value={position.entryPrice ? formatPrice(position.entryPrice) : '—'} />
        <InfoRow
          label="Stop"
          value={position.initialStop ? formatPrice(position.initialStop) : '—'}
          color={COLORS.red}
        />
        <InfoRow label="R:R" value={riskReward} />
      </div>
      <div className="text-[10px] text-text-faint mt-2">
        Shortlisted {position.entryDate ? timeAgo(position.entryDate) : ''}
      </div>
    </>
  )
}

function WatchingCard({ position }: KanbanCardProps) {
  const distance =
    position.entryPrice && position.currentPrice
      ? (
          ((position.currentPrice - position.entryPrice) / position.entryPrice) *
          100
        ).toFixed(1) + '%'
      : '—'

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[13px] font-medium">{position.symbol}</span>
        <StatusPill label="WATCH" variant="amber" />
      </div>
      <div className="space-y-1">
        <InfoRow label="Pivot" value={formatPrice(position.currentPrice)} />
        <InfoRow label="Distance" value={distance} />
      </div>
      <div className="text-[10px] text-text-faint mt-2">
        Watching {position.daysHeld ? `${position.daysHeld} days` : ''}
      </div>
    </>
  )
}

function OpenCard({ position }: KanbanCardProps) {
  const returnPct = ((position.currentPrice - position.entryPrice) / position.entryPrice) * 100

  return (
    <>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[13px] font-medium">{position.symbol}</span>
        <StatusPill
          label={formatR(position.rMultiple)}
          variant={position.rMultiple >= 0 ? 'green' : 'red'}
        />
      </div>
      <div className="text-xs font-medium mb-2" style={{ color: pnlColor(position.unrealizedPnl) }}>
        {formatINRCompact(position.unrealizedPnl)} &middot; {formatPct(returnPct)}
      </div>
      <div className="space-y-1">
        <InfoRow label="Entry" value={formatPrice(position.entryPrice)} />
        <InfoRow
          label="Now"
          value={formatPrice(position.currentPrice)}
          color={pnlColor(position.currentPrice - position.entryPrice)}
        />
        <InfoRow
          label="Stop"
          value={`${formatPrice(position.currentStop)} (${position.stopMethod})`}
        />
      </div>
      <div className="text-[10px] text-text-faint mt-2">
        {position.daysHeld} days held
      </div>
    </>
  )
}

function ManagingCard({ position }: KanbanCardProps) {
  const returnPct = ((position.currentPrice - position.entryPrice) / position.entryPrice) * 100

  return (
    <>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[13px] font-medium">{position.symbol}</span>
        <StatusPill
          label={formatR(position.rMultiple)}
          variant={position.rMultiple >= 0 ? 'green' : 'red'}
        />
      </div>
      <div className="text-xs font-medium mb-2" style={{ color: pnlColor(position.unrealizedPnl) }}>
        {formatINRCompact(position.unrealizedPnl)} &middot; {formatPct(returnPct)}
      </div>
      <div className="space-y-1">
        <InfoRow label="Entry" value={formatPrice(position.entryPrice)} />
        <InfoRow
          label="Now"
          value={formatPrice(position.currentPrice)}
          color={pnlColor(position.currentPrice - position.entryPrice)}
        />
        <InfoRow
          label="Stop"
          value={`${formatPrice(position.currentStop)} (${position.stopMethod})`}
        />
      </div>
      <div className="text-[10px] text-text-faint mt-2">
        {position.daysHeld} days &middot; Breakeven locked
      </div>
    </>
  )
}

function ClosedCard({ position }: KanbanCardProps) {
  const returnPct = position.exitPrice
    ? ((position.exitPrice - position.entryPrice) / position.entryPrice) * 100
    : 0

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[13px] font-medium">{position.symbol}</span>
        <StatusPill
          label={formatR(position.rMultiple)}
          variant={position.rMultiple >= 0 ? 'green' : 'red'}
        />
      </div>
      <div className="space-y-1">
        <InfoRow
          label="Return"
          value={formatPct(returnPct)}
          color={pnlColor(returnPct)}
        />
        <InfoRow label="Held" value={`${position.daysHeld} days`} />
        <InfoRow label="Exit" value={position.exitReason ?? '—'} />
      </div>
      <div className="text-[10px] text-text-faint mt-2">
        Closed {position.exitDate ? timeAgo(position.exitDate) : ''}
      </div>
    </>
  )
}

const cardByStage: Record<string, React.ComponentType<KanbanCardProps>> = {
  shortlisted: ShortlistedCard,
  watching: WatchingCard,
  open: OpenCard,
  managing: ManagingCard,
  closed: ClosedCard,
}

export function KanbanCard({ position }: KanbanCardProps) {
  const CardContent = cardByStage[position.stage] ?? OpenCard

  return (
    <div className="bg-bg-surface border border-border-subtle rounded-lg p-3 hover:border-border-hover cursor-pointer transition-colors">
      <CardContent position={position} />
    </div>
  )
}

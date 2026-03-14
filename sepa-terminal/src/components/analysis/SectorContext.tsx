import { useMemo } from 'react'
import { generateMockSectorContext } from '@/lib/mock-data'

interface SectorContextProps {
  sector?: string
}

function MetricRow({
  label,
  value,
  color,
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div className="flex justify-between">
      <span className="text-[11px] text-text-muted">{label}</span>
      <span
        className={`text-[11px] tabular-nums ${color ?? 'text-text-secondary'}`}
      >
        {value}
      </span>
    </div>
  )
}

function quadrantColor(q: string): string {
  switch (q) {
    case 'Leading':
      return 'text-green'
    case 'Improving':
      return 'text-blue'
    case 'Weakening':
      return 'text-amber'
    case 'Lagging':
      return 'text-red'
    default:
      return 'text-text-secondary'
  }
}

function perfColor(val: number): string {
  return val >= 0 ? 'text-green' : 'text-red'
}

function flowColor(val: string): string {
  return val.startsWith('+') ? 'text-green' : 'text-red'
}

export function SectorContext({ sector = 'Bank' }: SectorContextProps) {
  const data = useMemo(() => generateMockSectorContext(sector), [sector])

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        Sector context
      </h3>
      <div className="flex flex-col gap-1">
        <MetricRow
          label={`Nifty ${data.sector} (1M)`}
          value={`${data.performance1m >= 0 ? '+' : ''}${data.performance1m.toFixed(1)}%`}
          color={perfColor(data.performance1m)}
        />
        <MetricRow
          label="RRG quadrant"
          value={data.rrqQuadrant}
          color={quadrantColor(data.rrqQuadrant)}
        />
        <MetricRow
          label={`FII flow (${data.sector}, 5d)`}
          value={data.fiiFlow}
          color={flowColor(data.fiiFlow)}
        />
        <MetricRow
          label={`DII flow (${data.sector}, 5d)`}
          value={data.diiFlow}
          color={flowColor(data.diiFlow)}
        />
        <MetricRow
          label={`${data.sector} % above 50d`}
          value={`${data.sectorBreadth}%`}
        />
      </div>
    </div>
  )
}

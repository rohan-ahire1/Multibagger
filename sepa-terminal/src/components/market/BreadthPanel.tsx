import { useMarketStore } from '@/stores/market'
import { formatINRCompact } from '@/lib/formatters'

function BreadthGauge({
  label,
  value,
  maxValue = 100,
}: {
  label: string
  value: number
  maxValue?: number
}) {
  const isPositive = value >= 0
  const displayValue = Math.min(Math.abs(value), maxValue)
  const widthPct = (displayValue / maxValue) * 100

  return (
    <div className="flex items-center gap-3">
      <span className="text-[11px] text-text-muted w-28 shrink-0">
        {label}
      </span>
      <div className="flex-1 bg-bg-surface h-2 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${isPositive ? 'bg-green' : 'bg-red'}`}
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <span
        className={`text-xs font-medium tabular-nums w-12 text-right ${
          isPositive ? 'text-green' : 'text-red'
        }`}
      >
        {value >= 0 ? '+' : ''}
        {typeof value === 'number' && label.includes('highs')
          ? value
          : `${value}%`}
      </span>
    </div>
  )
}

function FlowBar({
  label,
  value,
  maxAbsValue,
}: {
  label: string
  value: number
  maxAbsValue: number
}) {
  const isPositive = value >= 0
  const barPct =
    maxAbsValue > 0 ? (Math.abs(value) / maxAbsValue) * 50 : 0

  return (
    <div className="flex items-center gap-3">
      <span className="text-[11px] text-text-muted w-8 shrink-0">{label}</span>
      <div className="flex-1 bg-bg-surface h-2 rounded-full relative overflow-hidden">
        {/* Center line */}
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border-strong" />
        {/* Bar */}
        <div
          className={`absolute top-0 h-full rounded-full ${
            isPositive ? 'bg-green' : 'bg-red'
          }`}
          style={
            isPositive
              ? { left: '50%', width: `${barPct}%` }
              : { right: '50%', width: `${barPct}%` }
          }
        />
      </div>
      <span
        className={`text-xs font-medium tabular-nums w-20 text-right ${
          isPositive ? 'text-green' : 'text-red'
        }`}
      >
        {formatINRCompact(value)}
      </span>
    </div>
  )
}

export function BreadthPanel() {
  const regime = useMarketStore((s) => s.regime)

  const maxAbsFII = Math.max(Math.abs(regime.fii5d), Math.abs(regime.dii5d), 1)

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-3 py-2">
        <span className="text-xs uppercase text-text-muted">
          Market breadth &amp; flows
        </span>
      </div>

      {/* Breadth gauges */}
      <div className="flex flex-col gap-2 px-3">
        <BreadthGauge label="Above 200-DMA" value={regime.breadthAbove200d} />
        <BreadthGauge label="Above 50-DMA" value={regime.breadthAbove50d} />
        <BreadthGauge
          label="New highs - lows"
          value={regime.newHighsMinusLows}
          maxValue={100}
        />
      </div>

      {/* FII / DII section */}
      <div className="border-t border-border-default mt-2 pt-2 px-3 pb-3">
        <span className="text-[10px] uppercase text-text-faint tracking-wider">
          FII / DII NET (5-DAY, CR)
        </span>
        <div className="flex flex-col gap-2 mt-2">
          <FlowBar label="FII" value={regime.fii5d} maxAbsValue={maxAbsFII} />
          <FlowBar label="DII" value={regime.dii5d} maxAbsValue={maxAbsFII} />
        </div>
      </div>
    </div>
  )
}

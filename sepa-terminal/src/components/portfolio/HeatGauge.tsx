import { formatPct } from '@/lib/formatters'
import { COLORS } from '@/lib/colors'

interface HeatGaugeProps {
  capitalAtRiskPct: number
}

export function HeatGauge({ capitalAtRiskPct }: HeatGaugeProps) {
  const maxScale = 12
  const filledPct = Math.min((capitalAtRiskPct / maxScale) * 100, 100)

  const valueColor =
    capitalAtRiskPct < 3
      ? COLORS.green
      : capitalAtRiskPct <= 6
        ? COLORS.amber
        : COLORS.red

  return (
    <div className="px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-text-dim w-24 shrink-0">
          Portfolio heat
        </span>

        <div className="flex-1 relative">
          {/* Track */}
          <div className="bg-bg-surface h-3 rounded-full relative overflow-hidden">
            {/* Zone tints */}
            <div className="absolute inset-0 flex">
              <div className="w-1/4" />
              <div
                className="w-1/4"
                style={{ backgroundColor: 'rgba(245, 166, 35, 0.05)' }}
              />
              <div
                className="w-1/4"
                style={{ backgroundColor: 'rgba(245, 166, 35, 0.10)' }}
              />
              <div
                className="w-1/4"
                style={{ backgroundColor: 'rgba(239, 83, 80, 0.10)' }}
              />
            </div>

            {/* Filled portion */}
            <div
              className="absolute top-0 left-0 h-full rounded-full"
              style={{
                width: `${filledPct}%`,
                background: `linear-gradient(to right, ${COLORS.green}, ${COLORS.amber})`,
              }}
            />
          </div>

          {/* Marker */}
          <div
            className="absolute top-0 h-4 rounded-sm"
            style={{
              width: '3px',
              backgroundColor: 'white',
              left: `${filledPct}%`,
              transform: 'translateX(-50%) translateY(-2px)',
            }}
          />

          {/* Scale labels */}
          <div className="flex justify-between mt-1">
            {['0%', '3%', '6%', '9%', '12%'].map((label) => (
              <span key={label} className="text-[10px] text-text-faint">
                {label}
              </span>
            ))}
          </div>
        </div>

        <span
          className="text-[11px] font-medium tabular-nums shrink-0"
          style={{ color: valueColor }}
        >
          {formatPct(capitalAtRiskPct)}
        </span>
      </div>
    </div>
  )
}

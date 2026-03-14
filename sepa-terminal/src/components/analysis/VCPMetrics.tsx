import type { VCPMetrics as VCPMetricsType } from '@/lib/types'

interface VCPMetricsProps {
  metrics: VCPMetricsType
}

function MetricRow({
  label,
  value,
  pass,
}: {
  label: string
  value: string
  pass?: boolean
}) {
  return (
    <div className="flex justify-between">
      <span className="text-[11px] text-text-muted">{label}</span>
      <span
        className={`text-[11px] tabular-nums ${
          pass ? 'text-green' : 'text-text-secondary'
        }`}
      >
        {value}
      </span>
    </div>
  )
}

export function VCPMetrics({ metrics }: VCPMetricsProps) {
  const depthsStr = metrics.contractionDepths
    .map((d) => `${d}%`)
    .join(' \u2192 ')

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        VCP pattern
      </h3>
      <div className="flex flex-col gap-1">
        <MetricRow
          label="Contractions"
          value={`${metrics.contractionCount} (${depthsStr})`}
          pass={metrics.contractionCount >= 2}
        />
        <MetricRow
          label="Quality ratio"
          value={`${metrics.qualityRatio.toFixed(2)} avg`}
          pass={metrics.qualityRatio < 0.65}
        />
        <MetricRow
          label="Base depth"
          value={`${metrics.baseDepth.toFixed(1)}%`}
        />
        <MetricRow
          label="Base duration"
          value={`${metrics.baseDuration} days (${(metrics.baseDuration / 5).toFixed(1)} weeks)`}
        />
        <MetricRow
          label="Vol dry-up"
          value={`${metrics.volDryUp.toFixed(2)}x (${metrics.volDryUp <= 0.65 ? 'below' : 'above'} 0.65)`}
          pass={metrics.volDryUp <= 0.65}
        />
        <MetricRow
          label="ATR contraction"
          value={`${metrics.atrContraction.toFixed(2)} (10d/30d)`}
          pass={metrics.atrContraction <= 0.8}
        />
        <MetricRow
          label="Range (10d)"
          value={`${metrics.range10d.toFixed(1)}%`}
          pass={metrics.range10d <= 10}
        />
        <MetricRow
          label="Breakout vol"
          value={`${metrics.breakoutVolMult.toFixed(1)}x 50d avg`}
          pass={metrics.breakoutVolMult >= 1.5}
        />
      </div>
    </div>
  )
}

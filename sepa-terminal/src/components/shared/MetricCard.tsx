interface MetricCardProps {
  label: string
  value: string
  subtitle?: string
  valueColor?: string
}

export function MetricCard({ label, value, subtitle, valueColor }: MetricCardProps) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-text-dim">
        {label}
      </span>
      <span
        className="text-sm font-medium tabular-nums"
        style={valueColor ? { color: valueColor } : undefined}
      >
        {value}
      </span>
      {subtitle && (
        <span className="text-[11px] text-text-dim">{subtitle}</span>
      )}
    </div>
  )
}

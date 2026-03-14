import type { BacktestSummary } from '@/lib/types'
import { formatPct, formatR } from '@/lib/formatters'

interface StatsSummaryProps {
  summary: BacktestSummary
}

interface MetricItem {
  label: string
  value: string
  color?: string
}

function Metric({ label, value, color }: MetricItem) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span
        className="text-xs font-medium tabular-nums font-mono"
        style={color ? { color } : undefined}
      >
        {value}
      </span>
    </div>
  )
}

export function StatsSummary({ summary }: StatsSummaryProps) {
  const winRateColor = summary.winRate > 40 ? '#22c55e' : '#f87171'
  const avgRColor =
    summary.averageR != null ? (summary.averageR >= 0 ? '#22c55e' : '#f87171') : undefined
  const cumRColor = summary.cumulativeR >= 0 ? '#22c55e' : '#f87171'

  const metrics: MetricItem[] = [
    {
      label: 'Signals',
      value: String(summary.totalSignals),
    },
    {
      label: 'Filled',
      value: String(summary.filledTrades),
    },
    ...(summary.skippedSplitCount > 0
      ? [{
          label: 'Split Skip',
          value: String(summary.skippedSplitCount),
          color: '#a78bfa',
        }]
      : []),
    {
      label: 'Win Rate',
      value: formatPct(summary.winRate),
      color: winRateColor,
    },
    {
      label: 'Avg R',
      value: summary.averageR != null ? formatR(summary.averageR) : '—',
      color: avgRColor,
    },
    {
      label: 'Cum R',
      value: formatR(summary.cumulativeR),
      color: cumRColor,
    },
    {
      label: 'Max DD',
      value: summary.maxDrawdown !== 0 ? formatR(summary.maxDrawdown) : '—',
      color: '#f87171',
    },
    {
      label: 'Payoff',
      value: summary.payoffRatio != null ? summary.payoffRatio.toFixed(2) : '—',
    },
    {
      label: 'Lose Streak',
      value: String(summary.longestLosingStreak),
      color: summary.longestLosingStreak >= 5 ? '#f87171' : undefined,
    },
    {
      label: 'Avg MFE',
      value: summary.avgMfeR != null ? formatR(summary.avgMfeR) : '—',
      color: '#22c55e',
    },
    {
      label: 'Avg MAE',
      value: summary.avgMaeR != null ? formatR(summary.avgMaeR) : '—',
      color: '#f87171',
    },
    {
      label: 'Giveback',
      value: summary.avgGivebackPct != null ? formatPct(summary.avgGivebackPct) : '—',
    },
  ]

  return (
    <div className="grid grid-cols-3 gap-x-4 gap-y-3 px-4 py-3 border-b border-zinc-800 bg-[#0F1117]">
      {metrics.map((m) => (
        <Metric key={m.label} label={m.label} value={m.value} color={m.color} />
      ))}
    </div>
  )
}

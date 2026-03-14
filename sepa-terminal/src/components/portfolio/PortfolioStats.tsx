import type { PortfolioStats as PortfolioStatsType } from '@/lib/types'
import { formatINRCompact, formatPct, formatR } from '@/lib/formatters'
import { COLORS, pnlColor } from '@/lib/colors'

interface PortfolioStatsProps {
  stats: PortfolioStatsType
}

export function PortfolioStats({ stats }: PortfolioStatsProps) {
  const riskColor =
    stats.capitalAtRiskPct < 3
      ? COLORS.green
      : stats.capitalAtRiskPct <= 6
        ? COLORS.amber
        : COLORS.red

  const healthLabel =
    stats.expectancy > 0.3 ? 'good' : stats.expectancy > 0 ? 'fair' : 'poor'

  const cells: { value: React.ReactNode; subtitle: string }[] = [
    {
      value: formatINRCompact(stats.portfolioValue),
      subtitle: `Capital: ${formatINRCompact(stats.capitalBase)}`,
    },
    {
      value: (
        <span style={{ color: pnlColor(stats.unrealizedPnl) }}>
          {formatINRCompact(stats.unrealizedPnl)}
        </span>
      ),
      subtitle: `${formatPct(stats.unrealizedPnlPct)} total`,
    },
    {
      value: stats.openPositions,
      subtitle: `${stats.openSectors} sectors`,
    },
    {
      value: (
        <span style={{ color: riskColor }}>
          {formatPct(stats.capitalAtRiskPct)}
        </span>
      ),
      subtitle: `${formatINRCompact(stats.capitalAtRiskAmt)} if all stops hit`,
    },
    {
      value: (
        <span style={{ color: pnlColor(stats.avgR) }}>
          {formatR(stats.avgR)}
        </span>
      ),
      subtitle: `${stats.closedTrades} trades \u00b7 ${Math.round(stats.winRate)}% win`,
    },
    {
      value: (
        <span style={{ color: pnlColor(stats.expectancy) }}>
          {formatR(stats.expectancy)}
        </span>
      ),
      subtitle: `System health: ${healthLabel}`,
    },
  ]

  return (
    <div className="bg-bg-secondary border-b border-border-default grid grid-cols-6">
      {cells.map((cell, i) => (
        <div
          key={i}
          className={`px-4 py-3${i < cells.length - 1 ? ' border-r border-border-default' : ''}`}
        >
          <div className="text-lg font-medium tabular-nums">{cell.value}</div>
          <div className="text-[11px] text-text-dim">{cell.subtitle}</div>
        </div>
      ))}
    </div>
  )
}

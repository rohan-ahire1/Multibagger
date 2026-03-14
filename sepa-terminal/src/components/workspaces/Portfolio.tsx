import { usePortfolioStore } from '@/stores/portfolio'
import { PortfolioStats } from '@/components/portfolio/PortfolioStats'
import { HeatGauge } from '@/components/portfolio/HeatGauge'
import { KanbanBoard } from '@/components/portfolio/KanbanBoard'

export function Portfolio() {
  const positions = usePortfolioStore((s) => s.positions)
  const stats = usePortfolioStore((s) => s.stats)

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PortfolioStats stats={stats} />
      <HeatGauge capitalAtRiskPct={stats.capitalAtRiskPct} />
      <KanbanBoard positions={positions} />
    </div>
  )
}

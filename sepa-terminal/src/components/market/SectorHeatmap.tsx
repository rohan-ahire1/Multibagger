import { useMarketStore } from '@/stores/market'
import { useScannerStore } from '@/stores/scanner'
import type { SectorPerformance } from '@/lib/types'

const periods = ['1m', '3m', '6m'] as const

function getChangeForPeriod(
  sector: SectorPerformance,
  period: '1m' | '3m' | '6m',
): number {
  switch (period) {
    case '1m':
      return sector.change1m
    case '3m':
      return sector.change3m
    case '6m':
      return sector.change6m
  }
}

function cellBg(change: number): string {
  if (change > 3) return 'rgba(38, 166, 154, 0.25)'
  if (change >= 0) return 'rgba(38, 166, 154, 0.15)'
  if (change >= -3) return 'rgba(239, 83, 80, 0.15)'
  return 'rgba(239, 83, 80, 0.25)'
}

export function SectorHeatmap() {
  const sectors = useMarketStore((s) => s.sectors)
  const sectorPeriod = useMarketStore((s) => s.sectorPeriod)
  const setSectorPeriod = useMarketStore((s) => s.setSectorPeriod)
  const sectorFilter = useScannerStore((s) => s.sectorFilter)
  const setSectorFilter = useScannerStore((s) => s.setSectorFilter)

  const sorted = [...sectors].sort((a, b) => {
    const aVal = getChangeForPeriod(a, sectorPeriod)
    const bVal = getChangeForPeriod(b, sectorPeriod)
    return bVal - aVal
  })

  const handleSectorClick = (sector: string) => {
    if (sectorFilter === sector) {
      setSectorFilter(null)
    } else {
      setSectorFilter(sector)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-xs uppercase text-text-muted">
          Sector rotation — {sectorPeriod.replace('m', ' month')} change
        </span>
        <div className="flex items-center gap-1">
          {periods.map((p) => (
            <button
              key={p}
              onClick={() => setSectorPeriod(p)}
              className={`text-[10px] px-2 py-0.5 rounded transition-colors ${
                sectorPeriod === p
                  ? 'bg-bg-elevated text-text-primary'
                  : 'text-text-dim hover:text-text-muted'
              }`}
            >
              {p.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-6 gap-1 px-3 pb-3">
        {sorted.map((s) => {
          const change = getChangeForPeriod(s, sectorPeriod)
          const isSelected = sectorFilter === s.sector
          return (
            <button
              key={s.sector}
              onClick={() => handleSectorClick(s.sector)}
              className={`rounded p-1.5 text-left transition-all ${
                isSelected
                  ? 'ring-1 ring-blue'
                  : ''
              }`}
              style={{ backgroundColor: cellBg(change) }}
            >
              <div className="text-[10px] font-medium text-white truncate">
                {s.sector}
              </div>
              <div
                className={`text-xs font-medium tabular-nums ${
                  change >= 0 ? 'text-green' : 'text-red'
                }`}
              >
                {change >= 0 ? '+' : ''}
                {change.toFixed(1)}%
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

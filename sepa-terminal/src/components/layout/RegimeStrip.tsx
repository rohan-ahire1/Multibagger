import { useMarketStore } from '@/stores/market'
import { formatPrice, formatPct, formatINRCompact } from '@/lib/formatters'

const regimeConfig = {
  bull: {
    dotClass: 'bg-green',
    textClass: 'text-green',
    label: 'Stage 2 bull — risk on',
  },
  caution: {
    dotClass: 'bg-amber',
    textClass: 'text-amber',
    label: 'Caution — mixed signals',
  },
  defensive: {
    dotClass: 'bg-red',
    textClass: 'text-red',
    label: 'Defensive — reduce exposure',
  },
} as const

function StatCell({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-px h-7 bg-border-strong shrink-0" />
      <div className="flex flex-col items-start leading-tight">
        <span className="text-[10px] uppercase tracking-wider text-text-faint">
          {label}
        </span>
        <span className="text-[13px] font-medium text-text-secondary tabular-nums">
          {children}
        </span>
      </div>
    </div>
  )
}

function ColoredValue({
  value,
  formatted,
}: {
  value: number
  formatted: string
}) {
  return (
    <span className={value >= 0 ? 'text-green' : 'text-red'}>{formatted}</span>
  )
}

export function RegimeStrip() {
  const regime = useMarketStore((s) => s.regime)
  const config = regimeConfig[regime.regime]

  return (
    <div className="h-10 bg-bg-secondary border-b border-border-default px-4 flex items-center gap-4 shrink-0">
      {/* Regime pill */}
      <div className="flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${config.dotClass}`} />
        <span className={`text-[13px] font-medium ${config.textClass}`}>
          {config.label}
        </span>
      </div>

      {/* NIFTY 50 */}
      <StatCell label="NIFTY 50">
        <span className="flex items-center gap-1">
          {formatPrice(regime.nifty50Price)}
          <ColoredValue
            value={regime.nifty50ChangePct}
            formatted={formatPct(regime.nifty50ChangePct)}
          />
        </span>
      </StatCell>

      {/* VS 200D */}
      <StatCell label="VS 200D">
        <ColoredValue
          value={regime.niftyVs200dma}
          formatted={formatPct(regime.niftyVs200dma)}
        />
      </StatCell>

      {/* BREADTH >50D */}
      <StatCell label="BREADTH >50D">
        {regime.breadthAbove50d.toFixed(0)}%
      </StatCell>

      {/* INDIA VIX */}
      <StatCell label="INDIA VIX">{regime.indiaVix.toFixed(1)}</StatCell>

      {/* FII / DII TODAY */}
      <StatCell label="FII TODAY / DII TODAY">
        <span className="flex items-center gap-2">
          <ColoredValue
            value={regime.fiiToday}
            formatted={formatINRCompact(regime.fiiToday)}
          />
          <span className="text-text-faint">/</span>
          <ColoredValue
            value={regime.diiToday}
            formatted={formatINRCompact(regime.diiToday)}
          />
        </span>
      </StatCell>

      {/* SIGNALS */}
      <StatCell label="SIGNALS">
        {regime.signalCountBreakout} BREAK &middot; {regime.signalCountWatch}{' '}
        WATCH
      </StatCell>
    </div>
  )
}

import { formatPrice } from '@/lib/formatters'

interface TradePlanCardProps {
  entryRef: number | null
  stop: number | null
  close: number
  riskCapital?: number
  riskPct?: number
}

export function TradePlanCard({
  entryRef,
  stop,
  close: _close,
  riskCapital = 1000000,
  riskPct = 0.01,
}: TradePlanCardProps) {
  const hasValues = entryRef != null && stop != null && entryRef > 0 && stop > 0

  const riskPerShare = hasValues ? entryRef - stop : 0
  const riskPctVal = hasValues ? ((entryRef - stop) / entryRef) * 100 : 0
  const shares = hasValues && riskPerShare > 0
    ? Math.floor((riskCapital * riskPct) / riskPerShare)
    : 0
  const positionValue = hasValues ? shares * entryRef : 0

  const formatLakhs = (value: number): string => {
    const lakhs = value / 100000
    return `\u20B9${lakhs.toFixed(2)}L`
  }

  return (
    <div className="grid grid-cols-4 gap-2">
      <div className="bg-bg-surface rounded-md p-2">
        <div className="text-[10px] uppercase text-text-dim">Entry</div>
        <div className="text-sm font-medium tabular-nums text-green">
          {hasValues ? formatPrice(entryRef) : '\u2014'}
        </div>
      </div>
      <div className="bg-bg-surface rounded-md p-2">
        <div className="text-[10px] uppercase text-text-dim">Stop</div>
        <div className="text-sm font-medium tabular-nums text-red">
          {hasValues ? formatPrice(stop) : '\u2014'}
        </div>
      </div>
      <div className="bg-bg-surface rounded-md p-2">
        <div className="text-[10px] uppercase text-text-dim">Risk</div>
        <div className="text-sm font-medium tabular-nums text-text-secondary">
          {hasValues ? `${riskPctVal.toFixed(1)}% \u00B7 1.0R` : '\u2014'}
        </div>
      </div>
      <div className="bg-bg-surface rounded-md p-2">
        <div className="text-[10px] uppercase text-text-dim">Position (1% risk)</div>
        <div className="text-sm font-medium tabular-nums text-green">
          {hasValues ? `${shares} shares \u00B7 ${formatLakhs(positionValue)}` : '\u2014'}
        </div>
      </div>
    </div>
  )
}

import { Check, X } from 'lucide-react'
import type { TrendTemplateResult } from '@/lib/types'

interface TrendTemplateProps {
  result: TrendTemplateResult
}

export function TrendTemplate({ result }: TrendTemplateProps) {
  const { conditions, passCount } = result

  const titleColor =
    passCount === 9
      ? 'text-green'
      : passCount >= 7
        ? 'text-amber'
        : 'text-red'

  return (
    <div>
      <h3 className={`text-xs font-medium mb-2 ${titleColor}`}>
        Trend template ({passCount}/9)
      </h3>
      <div className="flex flex-col gap-1">
        {conditions.map((cond, i) => (
          <div key={i} className="flex items-center gap-2">
            <div
              className={`w-3.5 h-3.5 rounded-[3px] flex items-center justify-center flex-shrink-0 ${
                cond.pass ? 'bg-green-bg' : 'bg-red-bg'
              }`}
            >
              {cond.pass ? (
                <Check className="w-2.5 h-2.5 text-green" />
              ) : (
                <X className="w-2.5 h-2.5 text-red" />
              )}
            </div>
            <span className="text-[11px] text-text-muted flex-1">
              {cond.label}
            </span>
            <span className="text-[11px] text-text-secondary tabular-nums min-w-[60px] text-right">
              {cond.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

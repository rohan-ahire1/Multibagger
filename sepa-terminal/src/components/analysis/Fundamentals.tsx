import { useMemo } from 'react'
import { generateMockFundamentals } from '@/lib/mock-data'

function isPositiveGrowth(value: string | number): boolean {
  if (typeof value === 'string') {
    return value.startsWith('+')
  }
  return false
}

const growthKeys = new Set(['Revenue Growth', 'PAT Growth', 'FII QoQ'])

export function Fundamentals() {
  const data = useMemo(() => generateMockFundamentals(), [])

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        Fundamentals (Screener)
      </h3>
      <div className="flex flex-col gap-1">
        {Object.entries(data).map(([key, value]) => {
          const isGrowthMetric = growthKeys.has(key)
          const positive = isGrowthMetric && isPositiveGrowth(value)
          return (
            <div key={key} className="flex justify-between">
              <span className="text-[11px] text-text-muted">{key}</span>
              <span
                className={`text-[11px] tabular-nums ${
                  positive ? 'text-green' : 'text-text-secondary'
                }`}
              >
                {String(value)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

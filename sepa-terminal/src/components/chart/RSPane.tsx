import { useMemo } from 'react'
import { AreaChart, Area, ResponsiveContainer, YAxis } from 'recharts'
import type { OHLCVBar } from '@/lib/types'

interface RSPaneProps {
  data: OHLCVBar[]
}

export function RSPane({ data }: RSPaneProps) {
  const rsData = useMemo(() => {
    if (data.length === 0) return []
    const base = data[0].close
    return data.map((bar) => ({
      date: bar.date,
      rs: bar.close / base,
    }))
  }, [data])

  if (rsData.length === 0) return null

  return (
    <div className="relative h-full">
      <span className="absolute top-1 left-2 text-[10px] text-text-dim z-10">
        RS vs Nifty 50
      </span>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rsData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
          <YAxis domain={['dataMin', 'dataMax']} hide />
          <Area
            type="monotone"
            dataKey="rs"
            stroke="#3b82f6"
            fill="#3b82f6"
            fillOpacity={0.1}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

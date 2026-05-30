import {
  AreaChart,
  Area,
  ResponsiveContainer,
  YAxis,
  XAxis,
  Tooltip,
} from 'recharts'
import type { EquityCurvePoint } from '@/lib/types'

interface EquityCurveProps {
  data: EquityCurvePoint[]
}

interface TooltipPayloadItem {
  name: string
  value: number
  color: string
}

interface CustomTooltipProps {
  active?: boolean
  payload?: TooltipPayloadItem[]
  label?: string
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-[11px]">
      <div className="text-zinc-500 mb-1">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="font-mono" style={{ color: p.color }}>
          {p.name}: {p.value >= 0 ? '+' : ''}{p.value.toFixed(2)}R
        </div>
      ))}
    </div>
  )
}

export function EquityCurve({ data = [] }: EquityCurveProps) {
  if (!data || data.length === 0) return null

  return (
    <div className="flex flex-col border-b border-zinc-800 bg-[#0F1117]">
      {/* Cumulative R chart */}
      <div className="relative" style={{ height: 120 }}>
        <span className="absolute top-1 left-2 text-[10px] text-zinc-500 z-10 select-none">
          Cumulative R
        </span>
        <ResponsiveContainer width="100%" height={120}>
          <AreaChart data={data} margin={{ top: 16, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="exitDate" hide />
            <YAxis
              tick={{ fontSize: 9, fill: '#71717a' }}
              tickLine={false}
              axisLine={false}
              width={32}
              tickFormatter={(v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}R`}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone"
              dataKey="cumulativeR"
              name="Cum R"
              stroke="#22c55e"
              fill="#22c55e"
              fillOpacity={0.15}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown chart */}
      <div className="relative" style={{ height: 60 }}>
        <span className="absolute top-1 left-2 text-[10px] text-zinc-500 z-10 select-none">
          Drawdown
        </span>
        <ResponsiveContainer width="100%" height={60}>
          <AreaChart data={data} margin={{ top: 12, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="exitDate" hide />
            <YAxis
              tick={{ fontSize: 9, fill: '#71717a' }}
              tickLine={false}
              axisLine={false}
              width={32}
              tickFormatter={(v: number) => `${v.toFixed(0)}R`}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone"
              dataKey="drawdown"
              name="DD"
              stroke="#f87171"
              fill="#f87171"
              fillOpacity={0.2}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

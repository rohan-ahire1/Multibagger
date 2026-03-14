import { useMemo } from 'react'
import {
  BarChart,
  Bar,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
  YAxis,
} from 'recharts'
import type { OHLCVBar } from '@/lib/types'

interface VolumePaneProps {
  data: OHLCVBar[]
}

function formatVolume(vol: number): string {
  if (vol >= 1e7) return `${(vol / 1e7).toFixed(1)}Cr`
  if (vol >= 1e5) return `${(vol / 1e5).toFixed(1)}L`
  if (vol >= 1e3) return `${(vol / 1e3).toFixed(1)}K`
  return String(vol)
}

export function VolumePane({ data }: VolumePaneProps) {
  const { volumeData, avg50 } = useMemo(() => {
    if (data.length === 0) return { volumeData: [], avg50: 0 }

    const vd = data.map((bar) => ({
      date: bar.date,
      volume: bar.volume,
      up: bar.close >= bar.open,
    }))

    // Compute 50-day average volume from the last 50 bars (or all if fewer)
    const tail = data.slice(-50)
    const sum = tail.reduce((acc, b) => acc + b.volume, 0)
    const a50 = tail.length > 0 ? sum / tail.length : 0

    return { volumeData: vd, avg50: a50 }
  }, [data])

  if (volumeData.length === 0) return null

  return (
    <div className="relative" style={{ height: 60 }}>
      <span className="absolute top-1 left-2 text-[10px] text-text-dim z-10">
        Volume &middot; 50d avg: {formatVolume(avg50)}
      </span>
      <ResponsiveContainer width="100%" height={60}>
        <BarChart
          data={volumeData}
          margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
        >
          <YAxis domain={[0, 'dataMax']} hide />
          <ReferenceLine
            y={avg50}
            stroke="#6B6D77"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
          <Bar dataKey="volume" isAnimationActive={false}>
            {volumeData.map((entry, index) => (
              <Cell
                key={index}
                fill={entry.up ? 'rgba(38,166,154,0.6)' : 'rgba(239,83,80,0.6)'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

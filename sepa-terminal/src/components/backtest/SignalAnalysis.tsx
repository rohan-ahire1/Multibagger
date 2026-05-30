import { useState } from 'react'
import type { SignalAnalysis as SignalAnalysisType } from '@/lib/types'
import { formatPct } from '@/lib/formatters'

interface SignalAnalysisProps {
  data: SignalAnalysisType
}

function Metric({
  label,
  value,
  color,
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span
        className="text-xs font-medium tabular-nums font-mono"
        style={color ? { color } : undefined}
      >
        {value}
      </span>
    </div>
  )
}

export function SignalAnalysis({ data }: SignalAnalysisProps) {
  const [expanded, setExpanded] = useState(false)
  if (!data) return null

  const { pivotReached = 0, pivotReachedPct = 0, totalWithData = 0 } = data
  const reachedWidth = totalWithData > 0 ? (pivotReached / totalWithData) * 100 : 0

  const dayReachedBreakdown = data.dayReachedBreakdown ?? []
  const gapAnalysis = data.gapAnalysis ?? {
    gapUpPct: 0,
    avgGapPct: 0,
    bigGapUpCount: 0,
    bigGapDownCount: 0,
  }
  const unfilledAnalysis = data.unfilledAnalysis ?? {
    count: 0,
    avgMaxGainPct: 0,
    profitableAtLastDayPct: 0,
  }

  return (
    <div className="border-b border-zinc-800 bg-[#0F1117]">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2 flex items-center justify-between text-left hover:bg-zinc-800/50 transition-colors"
      >
        <span className="text-xs font-medium text-zinc-300 uppercase tracking-wider">
          Signal Quality
        </span>
        <span
          className={`text-zinc-500 transition-transform ${expanded ? 'rotate-180' : ''}`}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-3 space-y-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
              Pivot Reach
            </div>
            <div className="text-xs font-mono tabular-nums text-zinc-300 mb-1.5">
              {pivotReached} of {totalWithData} reached pivot ({formatPct(pivotReachedPct)})
            </div>
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden flex">
              <div
                className="h-full bg-emerald-500/80 rounded-l-full"
                style={{ width: `${reachedWidth}%` }}
              />
              <div
                className="h-full bg-zinc-600 rounded-r-full"
                style={{ width: `${100 - reachedWidth}%` }}
              />
            </div>
            {dayReachedBreakdown.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {dayReachedBreakdown.map((d) => (
                  <span
                    key={d.day}
                    className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono"
                  >
                    D{d.day}: {d.count} ({formatPct(d.pctOfReached)})
                  </span>
                ))}
              </div>
            )}
          </div>

          {(data.postEntryReturns ?? []).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1.5">
                Post-Entry Returns (from entry price)
              </div>
              <div className="space-y-0.5">
                {(data.postEntryReturns ?? []).map((r) => {
                  const avgColor =
                    r.avgReturnPct >= 0 ? '#22c55e' : '#f87171'
                  const barWidth = Math.min(r.profitablePct, 100)
                  return (
                    <div key={r.day} className="flex items-center gap-2 text-xs font-mono">
                      <span className="text-zinc-500 w-8 shrink-0">D{r.day}</span>
                      <span
                        className="tabular-nums w-16 shrink-0"
                        style={{ color: avgColor }}
                      >
                        {formatPct(r.avgReturnPct)}
                      </span>
                      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${barWidth}%`,
                            backgroundColor: r.profitablePct >= 50 ? '#22c55e' : '#f87171',
                            opacity: 0.7,
                          }}
                        />
                      </div>
                      <span className="text-zinc-400 tabular-nums w-20 text-right shrink-0">
                        {r.profitableCount}/{r.total} ({formatPct(r.profitablePct)})
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1.5">
              Next-Day Gap
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1">
              <Metric
                label="Gap up"
                value={formatPct(gapAnalysis.gapUpPct)}
                color="#22c55e"
              />
              <Metric
                label="Avg gap"
                value={formatPct(gapAnalysis.avgGapPct)}
                color={
                  gapAnalysis.avgGapPct >= 0 ? '#22c55e' : '#f87171'
                }
              />
              <Metric label="Big gaps up" value={String(gapAnalysis.bigGapUpCount)} />
              <Metric label="Big gaps down" value={String(gapAnalysis.bigGapDownCount)} />
            </div>
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1.5">
              Unfilled Signals
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1">
              <Metric
                label="Never reached pivot"
                value={String(unfilledAnalysis.count)}
              />
              <Metric
                label="Avg max gain"
                value={formatPct(unfilledAnalysis.avgMaxGainPct)}
                color="#22c55e"
              />
              <Metric
                label="Profitable at day 5"
                value={formatPct(unfilledAnalysis.profitableAtLastDayPct)}
                color="#22c55e"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

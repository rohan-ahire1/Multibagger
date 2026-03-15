import { useBacktestStore, DATA_START_DATE } from '@/stores/backtest'
import { useRunBacktest } from '@/api/useBacktest'
import type { BacktestParams } from '@/lib/types'

const RANGE_PRESETS = [
  { label: '1Y', years: 1 },
  { label: '3Y', years: 3 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'All', years: 0 },
] as const

export function ParamBar() {
  const { params, setParams, setResult, isRunning, setRunning } = useBacktestStore()
  const { mutateAsync } = useRunBacktest()

  async function handleRun() {
    setRunning(true)
    try {
      const report = await mutateAsync(params)
      setResult(report)
    } finally {
      setRunning(false)
    }
  }

  function applyPreset(years: number) {
    const now = new Date()
    const endDate = now.toISOString().slice(0, 10)
    if (years === 0) {
      setParams({ startDate: DATA_START_DATE, endDate })
    } else {
      const start = new Date(now)
      start.setFullYear(start.getFullYear() - years)
      setParams({ startDate: start.toISOString().slice(0, 10), endDate })
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-[#0F1117]">
      {/* Range presets */}
      <div className="flex items-center gap-0.5">
        {RANGE_PRESETS.map(({ label, years }) => (
          <button
            key={label}
            onClick={() => applyPreset(years)}
            className="text-[10px] px-2 py-0.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
          >
            {label}
          </button>
        ))}
      </div>

      <div className="w-px h-4 bg-zinc-700" />

      {/* Date Range */}
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">From</label>
        <input
          type="date"
          min={DATA_START_DATE}
          value={params.startDate}
          onChange={(e) => setParams({ startDate: e.target.value })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono focus:outline-none focus:border-zinc-500"
        />
      </div>

      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">To</label>
        <input
          type="date"
          min={DATA_START_DATE}
          value={params.endDate}
          onChange={(e) => setParams({ endDate: e.target.value })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono focus:outline-none focus:border-zinc-500"
        />
      </div>

      {/* Exit Mode */}
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">Exit</label>
        <select
          value={params.exitMode}
          onChange={(e) => {
            const mode = e.target.value as BacktestParams['exitMode']
            setParams({
              exitMode: mode,
              exitEngine: mode === 'minervini_phased' ? 'minervini_phased' : 'legacy',
            })
          }}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 focus:outline-none focus:border-zinc-500"
        >
          <option value="minervini_phased">Minervini Phased</option>
          <option value="v2_trail_10d">Trail 10D Low</option>
          <option value="v2_trail_21ema">Trail 21 EMA</option>
          <option value="v3_trail_pct">Trail % Stop</option>
          <option value="v1_fixed">Fixed Stop</option>
        </select>
      </div>

      {/* Target R */}
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">Target R</label>
        <input
          type="number"
          step="0.1"
          min="0"
          placeholder="—"
          value={params.targetR ?? ''}
          onChange={(e) =>
            setParams({ targetR: e.target.value === '' ? null : parseFloat(e.target.value) })
          }
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono w-16 focus:outline-none focus:border-zinc-500"
        />
      </div>

      {/* Pending Days */}
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">Pending</label>
        <input
          type="number"
          min="1"
          max="30"
          value={params.pendingDays}
          onChange={(e) => setParams({ pendingDays: parseInt(e.target.value, 10) })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono w-14 focus:outline-none focus:border-zinc-500"
        />
      </div>

      {/* Max Hold Days */}
      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">Max Hold</label>
        <input
          type="number"
          min="1"
          max="365"
          value={params.maxHoldDays}
          onChange={(e) => setParams({ maxHoldDays: parseInt(e.target.value, 10) })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono w-14 focus:outline-none focus:border-zinc-500"
        />
      </div>

      {/* Run Button */}
      <button
        onClick={handleRun}
        disabled={isRunning}
        className="ml-auto text-xs font-medium px-4 py-1.5 rounded bg-green-600 hover:bg-green-500 disabled:bg-green-900 disabled:text-green-600 disabled:cursor-not-allowed text-white transition-colors"
      >
        {isRunning ? 'Running...' : 'Run Backtest'}
      </button>
    </div>
  )
}

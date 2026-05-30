import { useEffect } from 'react'
import { useBacktestStore, DATA_START_DATE } from '@/stores/backtest'
import { useRunBacktest } from '@/api/useBacktest'
import { useScanDates } from '@/api/useScannerData'

const RANGE_PRESETS = [
  { label: '1Y', years: 1 },
  { label: '3Y', years: 3 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'All', years: 0 },
] as const

function subtractYears(isoDate: string, years: number): string {
  if (!isoDate || typeof isoDate !== 'string') return DATA_START_DATE
  const parts = isoDate.split('-').map(Number)
  if (parts.length !== 3 || parts.some(isNaN)) return DATA_START_DATE
  const [year, month, day] = parts
  const start = new Date(year - years, month - 1, day)
  if (isNaN(start.getTime())) return DATA_START_DATE
  return start.toISOString().slice(0, 10)
}

export function ParamBar() {
  const { params, setParams, setResult, isRunning, setRunning } = useBacktestStore()
  const { mutateAsync } = useRunBacktest()
  const { data: scanDates = [] } = useScanDates()
  const latestScanDate = scanDates[0] ?? null

  useEffect(() => {
    if (!latestScanDate) return
    if (!params.endDate || params.endDate > latestScanDate) {
      setParams({ endDate: latestScanDate })
    }
  }, [latestScanDate, params.endDate, setParams])

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
    if (!latestScanDate) return
    if (years === 0) {
      setParams({ startDate: DATA_START_DATE, endDate: latestScanDate })
      return
    }

    const startDate = subtractYears(latestScanDate, years)
    setParams({
      startDate: startDate < DATA_START_DATE ? DATA_START_DATE : startDate,
      endDate: latestScanDate,
    })
  }

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-[#0F1117]">
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

      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">From</label>
        <input
          type="date"
          min={DATA_START_DATE}
          max={latestScanDate ?? undefined}
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
          max={latestScanDate ?? undefined}
          value={params.endDate}
          onChange={(e) => setParams({ endDate: e.target.value })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono focus:outline-none focus:border-zinc-500"
        />
      </div>

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

      <div className="flex items-center gap-1.5">
        <label className="text-[10px] uppercase tracking-wider text-zinc-500">Exit</label>
        <select
          value={params.exitEngine}
          onChange={(e) => setParams({ exitEngine: e.target.value as 'minervini_phased' | 'advanced_sepa' })}
          className="text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono focus:outline-none focus:border-zinc-500 appearance-none cursor-pointer"
        >
          <option value="minervini_phased">Phased</option>
          <option value="advanced_sepa">Advanced (75x)</option>
        </select>
      </div>

      <button
        onClick={handleRun}
        disabled={isRunning || !latestScanDate}
        className="ml-auto text-xs font-medium px-4 py-1.5 rounded bg-green-600 hover:bg-green-500 disabled:bg-green-900 disabled:text-green-600 disabled:cursor-not-allowed text-white transition-colors"
      >
        {isRunning ? 'Running...' : 'Run Backtest'}
      </button>
    </div>
  )
}

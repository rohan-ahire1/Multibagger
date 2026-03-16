import type { SeriesMarker, Time } from 'lightweight-charts'
import { Panel, Group, Separator } from 'react-resizable-panels'
import type { SetupRow, OHLCVBar } from '@/lib/types'
import { formatPrice, formatPct } from '@/lib/formatters'
import { useUIStore } from '@/stores/ui'
import { useReviewsStore } from '@/stores/reviews'
import { ChartToolbar } from '@/components/chart/ChartToolbar'
import { PriceChart } from '@/components/chart/PriceChart'
import { QualificationPanel } from '@/components/shared/QualificationPanel'
import { TradePlanCard } from '@/components/analysis/TradePlanCard'

interface SetupDetailCardProps {
  setup: SetupRow | null
  ohlcvData: OHLCVBar[]
  isLoading: boolean
  markers?: SeriesMarker<Time>[]
}

function ShimmerPlaceholder() {
  return (
    <div className="flex flex-col gap-3 p-4 animate-pulse">
      <div className="h-6 w-40 bg-bg-elevated rounded" />
      <div className="h-4 w-24 bg-bg-elevated rounded" />
      <div className="h-[260px] bg-bg-elevated rounded" />
      <div className="h-20 bg-bg-elevated rounded" />
      <div className="h-16 bg-bg-elevated rounded" />
    </div>
  )
}

export function SetupDetailCard({
  setup,
  ohlcvData,
  isLoading,
  markers,
}: SetupDetailCardProps) {
  const chartTimeframe = useUIStore((s) => s.chartTimeframe)
  const chartOverlays = useUIStore((s) => s.chartOverlays)
  const setChartTimeframe = useUIStore((s) => s.setChartTimeframe)
  const toggleChartOverlay = useUIStore((s) => s.toggleChartOverlay)
  const shortlist = useReviewsStore((s) => s.shortlist)
  const reject = useReviewsStore((s) => s.reject)
  const watch = useReviewsStore((s) => s.watch)
  const setNote = useReviewsStore((s) => s.setNote)
  const getReview = useReviewsStore((s) => s.getReview)

  if (!setup) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
        Select a setup to view details
      </div>
    )
  }

  if (isLoading) {
    return <ShimmerPlaceholder />
  }

  const review = getReview(setup.symbol, setup.scanDate)
  const noteValue = review?.note ?? ''

  // Compute change % from previous close if we have data
  const changePct =
    ohlcvData.length >= 2
      ? ((setup.close - ohlcvData[ohlcvData.length - 2].close) /
          ohlcvData[ohlcvData.length - 2].close) *
        100
      : 0

  // Build qualification items
  const passItems: Array<{ text: string }> = []
  if (setup.rsRank >= 80)
    passItems.push({ text: `RS Rank ${Math.round(setup.rsRank)} — top decile` })
  else if (setup.rsRank >= 70)
    passItems.push({ text: `RS Rank ${Math.round(setup.rsRank)} — strong` })
  if (setup.volMult >= 1.5)
    passItems.push({
      text: `Volume ${setup.volMult.toFixed(1)}x average — institutional interest`,
    })
  if (setup.signalType === 'BREAKOUT')
    passItems.push({ text: 'Breaking out of base — SEPA trigger' })
  else if (setup.signalType === 'WATCH')
    passItems.push({ text: 'Near pivot — tightening for breakout' })
  if (setup.contractionCount != null && setup.contractionCount >= 2)
    passItems.push({
      text: `${setup.contractionCount} contractions — VCP pattern`,
    })

  const warnItems: Array<{ text: string }> = []
  if (setup.rsRank < 70)
    warnItems.push({ text: `RS Rank only ${Math.round(setup.rsRank)} — below threshold` })
  if (setup.volMult < 1.0)
    warnItems.push({ text: 'Volume below average — lack of conviction' })

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Detail Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-default shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg font-medium text-text-primary">
            {setup.symbol}
          </span>
          <span className="text-sm tabular-nums text-text-secondary">
            {formatPrice(setup.close)}
          </span>
          <span
            className={`text-sm tabular-nums ${
              changePct >= 0 ? 'text-green' : 'text-red'
            }`}
          >
            {formatPct(changePct)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => shortlist(setup.symbol, setup.scanDate)}
            className="border border-green text-green hover:bg-green-bg text-xs px-3 py-1 rounded transition-colors"
          >
            S Shortlist
          </button>
          <button
            onClick={() => watch(setup.symbol, setup.scanDate)}
            className="border border-blue text-blue hover:bg-blue-bg text-xs px-3 py-1 rounded transition-colors"
          >
            W Watch
          </button>
          <button
            onClick={() => reject(setup.symbol, setup.scanDate)}
            className="border border-red text-red hover:bg-red-bg text-xs px-3 py-1 rounded transition-colors"
          >
            X Reject
          </button>
        </div>
      </div>

      {/* Resizable chart + info */}
      <div className="flex-1 min-h-0">
        <Group orientation="vertical">
          {/* Chart panel — draggable */}
          <Panel defaultSize="55" minSize="25">
            <div className="flex flex-col h-full overflow-hidden">
              <div className="shrink-0">
                <ChartToolbar
                  timeframe={chartTimeframe}
                  overlays={chartOverlays}
                  onTimeframeChange={setChartTimeframe}
                  onOverlayToggle={toggleChartOverlay}
                />
              </div>
              <div className="flex-1 min-h-0">
                <PriceChart
                  data={ohlcvData}
                  entryRef={setup.entryRef ?? undefined}
                  stop={setup.stop ?? undefined}
                  pivot={setup.pivot}
                  overlays={chartOverlays}
                  timeframe={chartTimeframe}
                  markers={markers}
                />
              </div>
            </div>
          </Panel>

          <Separator className="h-px bg-border-default hover:bg-border-hover transition-colors cursor-row-resize" />

          {/* Info panel — scrollable */}
          <Panel defaultSize="45" minSize="20">
            <div className="h-full overflow-y-auto">
              <div className="px-4 py-3 border-b border-border-default">
                <QualificationPanel passItems={passItems} warnItems={warnItems} />
              </div>
              <div className="px-4 py-3 border-b border-border-default">
                <TradePlanCard
                  entryRef={setup.entryRef}
                  stop={setup.stop}
                  close={setup.close}
                />
              </div>
              <div className="px-4 py-3">
                <textarea
                  data-note-input
                  placeholder="Add note... (N to focus)"
                  value={noteValue}
                  onChange={(e) => setNote(setup.symbol, setup.scanDate, e.target.value)}
                  className="bg-bg-surface border border-border-strong rounded-md p-2 text-xs text-text-secondary w-full resize-none focus:border-green/50 focus:outline-none"
                  rows={3}
                />
              </div>
            </div>
          </Panel>
        </Group>
      </div>
    </div>
  )
}

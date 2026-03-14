import { useMemo } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import { Panel, Group, Separator } from 'react-resizable-panels'
import { useUIStore } from '@/stores/ui'
import { useScannerStore } from '@/stores/scanner'
import { useOHLCV } from '@/api/useOHLCV'
import {
  generateMockTrendTemplate,
  generateMockVCPMetrics,
  generateMockSignalHistory,
} from '@/lib/mock-data'
import { formatPrice, formatPct } from '@/lib/formatters'
import { ChartToolbar } from '@/components/chart/ChartToolbar'
import { PriceChart } from '@/components/chart/PriceChart'
import { RSPane } from '@/components/chart/RSPane'
import { VolumePane } from '@/components/chart/VolumePane'
import { TradePlanCard } from '@/components/analysis/TradePlanCard'
import { TrendTemplate } from '@/components/analysis/TrendTemplate'
import { VCPMetrics } from '@/components/analysis/VCPMetrics'
import { SectorContext } from '@/components/analysis/SectorContext'
import { Fundamentals } from '@/components/analysis/Fundamentals'
import { SignalHistory } from '@/components/analysis/SignalHistory'
import { TradeThesis } from '@/components/analysis/TradeThesis'
import { StatusPill } from '@/components/shared/StatusPill'

export function Analysis() {
  const setWorkspace = useUIStore((s) => s.setWorkspace)
  const chartTimeframe = useUIStore((s) => s.chartTimeframe)
  const chartOverlays = useUIStore((s) => s.chartOverlays)
  const setChartTimeframe = useUIStore((s) => s.setChartTimeframe)
  const toggleChartOverlay = useUIStore((s) => s.toggleChartOverlay)
  const scanDate = useUIStore((s) => s.scanDate)
  const selectedSetupId = useScannerStore((s) => s.selectedSetupId)

  // Parse symbol from selectedSetupId ("SYMBOL:DATE")
  const symbol = useMemo(() => {
    if (!selectedSetupId) return null
    return selectedSetupId.split(':')[0] ?? null
  }, [selectedSetupId])

  // Fetch OHLCV data
  const { data: ohlcvData = [], isLoading } = useOHLCV(symbol)

  // Mock data
  const trendTemplate = useMemo(() => generateMockTrendTemplate(), [])
  const vcpMetrics = useMemo(() => generateMockVCPMetrics(), [])
  const signalHistory = useMemo(() => generateMockSignalHistory(), [])

  // Derive price info from OHLCV
  const lastBar = ohlcvData.length > 0 ? ohlcvData[ohlcvData.length - 1] : null
  const prevBar = ohlcvData.length > 1 ? ohlcvData[ohlcvData.length - 2] : null
  const price = lastBar?.close ?? 0
  const changePct =
    lastBar && prevBar
      ? ((lastBar.close - prevBar.close) / prevBar.close) * 100
      : 0

  // Keyboard shortcuts for W2
  useHotkeys(
    'Escape',
    () => setWorkspace(1),
    { preventDefault: true },
  )

  useHotkeys(
    'd',
    (e) => {
      if (isInputFocused()) return
      e.preventDefault()
      setChartTimeframe('D')
    },
  )

  useHotkeys(
    'shift+w',
    (e) => {
      if (isInputFocused()) return
      e.preventDefault()
      setChartTimeframe('W')
    },
  )

  useHotkeys(
    'm',
    (e) => {
      if (isInputFocused()) return
      e.preventDefault()
      setChartTimeframe('M')
    },
  )

  useHotkeys(
    'n',
    (e) => {
      if (isInputFocused()) return
      e.preventDefault()
      const el = document.querySelector<HTMLTextAreaElement>(
        '[data-thesis-input]'
      )
      el?.focus()
    },
  )

  if (!symbol) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
        No setup selected. Press <kbd className="mx-1 px-1.5 py-0.5 bg-bg-elevated border border-border-strong rounded text-[11px] font-mono">1</kbd> to return to scanner.
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col gap-3 p-4 animate-pulse">
        <div className="h-6 w-40 bg-bg-elevated rounded" />
        <div className="h-[340px] bg-bg-elevated rounded" />
        <div className="h-20 bg-bg-elevated rounded" />
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <Group orientation="horizontal">
        {/* Chart column — 70% */}
        <Panel defaultSize={70} minSize={50}>
          <div className="flex flex-col h-full overflow-hidden">
            {/* Symbol bar */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-border-default">
              <div className="flex items-center gap-3">
                <span className="text-lg font-medium text-text-primary">
                  {symbol}
                </span>
                <span className="text-xs text-text-dim">
                  Company &middot; Sector
                </span>
                <StatusPill label="Stage 2" variant="green" />
                <StatusPill label={`RS ${trendTemplate.passCount >= 9 ? '92' : '78'}`} variant="blue" />
                {vcpMetrics.contractionCount >= 2 && (
                  <StatusPill
                    label={`VCP ${vcpMetrics.contractionCount}T`}
                    variant="amber"
                  />
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium tabular-nums text-text-secondary">
                  {formatPrice(price)}
                </span>
                <span
                  className={`text-sm tabular-nums ${
                    changePct >= 0 ? 'text-green' : 'text-red'
                  }`}
                >
                  {formatPct(changePct)}
                </span>
              </div>
            </div>

            {/* Chart toolbar */}
            <div className="border-b border-border-default">
              <ChartToolbar
                timeframe={chartTimeframe}
                overlays={chartOverlays}
                onTimeframeChange={setChartTimeframe}
                onOverlayToggle={toggleChartOverlay}
              />
            </div>

            {/* Price chart */}
            <div className="border-b border-border-default">
              <PriceChart
                data={ohlcvData}
                overlays={chartOverlays}
                timeframe={chartTimeframe}
                height={340}
              />
            </div>

            {/* RS pane */}
            <div className="border-b border-border-default">
              <RSPane data={ohlcvData} />
            </div>

            {/* Volume pane */}
            <div>
              <VolumePane data={ohlcvData} />
            </div>
          </div>
        </Panel>

        <Separator className="w-px bg-border-default hover:bg-border-hover transition-colors" />

        {/* Info column — 30% */}
        <Panel defaultSize={30} minSize={20}>
          <div className="flex flex-col h-full">
            {/* Scrollable info sections */}
            <div className="flex-1 overflow-y-auto">
              <div className="flex flex-col gap-4 px-4 py-3">
                {/* Trade plan */}
                <div>
                  <h3 className="text-xs font-medium text-text-primary mb-2">
                    Trade plan
                  </h3>
                  <TradePlanCard
                    entryRef={null}
                    stop={null}
                    close={price}
                  />
                </div>

                <div className="h-px bg-border-default" />

                <TrendTemplate result={trendTemplate} />

                <div className="h-px bg-border-default" />

                <VCPMetrics metrics={vcpMetrics} />

                <div className="h-px bg-border-default" />

                <SectorContext sector="Bank" />

                <div className="h-px bg-border-default" />

                <Fundamentals />

                <div className="h-px bg-border-default" />

                <SignalHistory entries={signalHistory} />

                <div className="h-px bg-border-default" />

                <TradeThesis symbol={symbol} scanDate={scanDate} />
              </div>
            </div>

            {/* Action bar — sticky at bottom */}
            <div className="flex items-center gap-2 px-4 py-3 border-t border-border-default bg-bg-base">
              <button className="border border-green text-green hover:bg-green-bg text-xs px-3 py-1.5 rounded transition-colors flex-1">
                Enter trade &rarr;
              </button>
              <button className="border border-blue text-blue hover:bg-blue-bg text-xs px-3 py-1.5 rounded transition-colors flex-1">
                Move to watching
              </button>
              <button
                onClick={() => setWorkspace(1)}
                className="border border-border-strong text-text-muted hover:text-text-secondary text-xs px-3 py-1.5 rounded transition-colors flex-1"
              >
                Back to scanner
              </button>
            </div>
          </div>
        </Panel>
      </Group>
    </div>
  )
}

function isInputFocused(): boolean {
  const el = document.activeElement
  if (!el) return false
  const tag = el.tagName.toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select'
}

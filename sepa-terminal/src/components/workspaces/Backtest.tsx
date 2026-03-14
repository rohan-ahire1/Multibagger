import { useEffect, useMemo } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import { useBacktestStore } from '@/stores/backtest'
import { useBacktestLast } from '@/api/useBacktest'
import { useOHLCV } from '@/api/useOHLCV'
import { useSymbolSignalHistory } from '@/api/useSignalHistory'
import { buildChartMarkers } from '@/lib/chart-markers'
import { useUIStore } from '@/stores/ui'
import { ParamBar } from '@/components/backtest/ParamBar'
import { StatsSummary } from '@/components/backtest/StatsSummary'
import { EquityCurve } from '@/components/backtest/EquityCurve'
import { TradeList } from '@/components/backtest/TradeList'
import { PriceChart } from '@/components/chart/PriceChart'
import { ChartToolbar } from '@/components/chart/ChartToolbar'

export function Backtest() {
  const { result, setResult, selectedTrade, selectTrade } = useBacktestStore()
  const chartTimeframe = useUIStore((s) => s.chartTimeframe)
  const chartOverlays = useUIStore((s) => s.chartOverlays)
  const setChartTimeframe = useUIStore((s) => s.setChartTimeframe)
  const toggleChartOverlay = useUIStore((s) => s.toggleChartOverlay)
  const { data: cachedResult } = useBacktestLast()

  // Load cached result on mount
  useEffect(() => {
    if (cachedResult && !result) {
      setResult(cachedResult)
    }
  }, [cachedResult, result, setResult])

  const trades = result?.trades ?? []

  // Find selected trade object
  const activeTrade = useMemo(
    () =>
      selectedTrade
        ? trades.find(
            (t) =>
              t.symbol === selectedTrade.symbol &&
              t.signalDate === selectedTrade.signalDate
          )
        : (trades[0] ?? null),
    [trades, selectedTrade]
  )

  // Auto-select first trade
  useEffect(() => {
    if (trades.length > 0 && !selectedTrade) {
      selectTrade({ symbol: trades[0].symbol, signalDate: trades[0].signalDate })
    }
  }, [trades, selectedTrade, selectTrade])

  // Fetch data for selected trade
  const { data: ohlcvData } = useOHLCV(activeTrade?.symbol ?? null)
  const { data: signalHistory } = useSymbolSignalHistory(activeTrade?.symbol)

  const markers = useMemo(
    () =>
      signalHistory
        ? buildChartMarkers(signalHistory.signals, signalHistory.trades)
        : [],
    [signalHistory]
  )

  // Keyboard nav: j = next trade, k = prev trade
  useHotkeys(
    'j',
    () => {
      if (trades.length === 0) return
      const currentIndex = activeTrade
        ? trades.findIndex(
            (t) =>
              t.symbol === activeTrade.symbol &&
              t.signalDate === activeTrade.signalDate
          )
        : -1
      const nextIndex = currentIndex < trades.length - 1 ? currentIndex + 1 : currentIndex
      const next = trades[nextIndex]
      if (next) selectTrade({ symbol: next.symbol, signalDate: next.signalDate })
    },
    { preventDefault: true },
    [trades, activeTrade, selectTrade]
  )

  useHotkeys(
    'k',
    () => {
      if (trades.length === 0) return
      const currentIndex = activeTrade
        ? trades.findIndex(
            (t) =>
              t.symbol === activeTrade.symbol &&
              t.signalDate === activeTrade.signalDate
          )
        : -1
      const prevIndex = currentIndex > 0 ? currentIndex - 1 : 0
      const prev = trades[prevIndex]
      if (prev) selectTrade({ symbol: prev.symbol, signalDate: prev.signalDate })
    },
    { preventDefault: true },
    [trades, activeTrade, selectTrade]
  )

  // Empty state
  if (!result) {
    return (
      <div className="flex-1 flex flex-col">
        <ParamBar />
        <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
          No backtest results. Configure parameters and click Run Backtest.
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <ParamBar />
      <div className="flex-1 flex overflow-hidden">
        {/* Left column — ~40% */}
        <div className="w-[40%] flex flex-col border-r border-zinc-800 overflow-hidden">
          <StatsSummary summary={result.summary} />
          <TradeList trades={trades} />
        </div>

        {/* Right column — flex-1 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <EquityCurve data={result.equityCurve} />
          <div className="border-t border-zinc-800" />
          {activeTrade && ohlcvData ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Trade header */}
              <div className="px-3 py-1.5 flex items-center gap-2 text-sm border-b border-zinc-800">
                <span className="font-medium text-zinc-200">{activeTrade.symbol}</span>
                <span className="text-zinc-500">{activeTrade.signalDate}</span>
                {activeTrade.rMultiple != null && (
                  <span
                    className={
                      activeTrade.rMultiple > 0 ? 'text-emerald-400' : 'text-red-400'
                    }
                  >
                    {activeTrade.rMultiple > 0 ? '+' : ''}
                    {activeTrade.rMultiple.toFixed(1)}R
                  </span>
                )}
              </div>
              <ChartToolbar
                timeframe={chartTimeframe}
                overlays={chartOverlays}
                onTimeframeChange={setChartTimeframe}
                onOverlayToggle={toggleChartOverlay}
              />
              <div className="flex-1">
                <PriceChart
                  data={ohlcvData}
                  entryRef={activeTrade.entryRef}
                  stop={activeTrade.stop}
                  pivot={activeTrade.pivot ?? undefined}
                  overlays={chartOverlays}
                  timeframe={chartTimeframe}
                  markers={markers}
                />
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
              Select a trade to view chart
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

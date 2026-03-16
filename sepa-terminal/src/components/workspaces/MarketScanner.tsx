import { useMemo, useEffect } from 'react'
import {
  Panel,
  Group,
  Separator,
} from 'react-resizable-panels'
import { useUIStore } from '@/stores/ui'
import { useScannerStore } from '@/stores/scanner'
import { useReviewsStore } from '@/stores/reviews'
import { useSignals, useWatchlist, useScanDates } from '@/api/useScannerData'
import { useOHLCV } from '@/api/useOHLCV'
import { useSymbolSignalHistory } from '@/api/useSignalHistory'
import { buildChartMarkers } from '@/lib/chart-markers'
import { ScanDatePicker } from '@/components/scanner/ScanDatePicker'
import { QueueTabs } from '@/components/scanner/QueueTabs'
import { ScannerTable } from '@/components/scanner/ScannerTable'
import { SetupDetailCard } from '@/components/scanner/SetupDetailCard'
import { useKeyboardTriage } from '@/hooks/useKeyboardTriage'
import type { SetupRow, QueueType, Signal, WatchlistCandidate } from '@/lib/types'

function buildSetupRows(
  signals: Signal[],
  watchlist: WatchlistCandidate[],
  scanDate: string,
  reviews: ReturnType<typeof useReviewsStore.getState>['reviews']
): SetupRow[] {
  const rows: SetupRow[] = []
  const seenSymbols = new Set<string>()

  // Signals become rows
  for (const sig of signals) {
    const key = `${sig.symbol}:${scanDate}`
    const review = reviews[key]
    seenSymbols.add(sig.symbol)

    let queue: QueueType = 'breakout'
    if (review?.status === 'shortlisted') queue = 'shortlisted'
    else if (review?.status === 'rejected') queue = 'rejected'
    else if (sig.signalType === 'WATCH') queue = 'nearPivot'

    rows.push({
      id: key,
      scanDate,
      symbol: sig.symbol,
      signalType: sig.signalType,
      close: sig.close,
      pivot: sig.pivot,
      nearPivot: sig.nearPivot,
      rsRank: sig.rsRank,
      volMult: sig.volMult,
      entryRef: sig.entryRef,
      stop: sig.stop,
      rPerShare: sig.rPerShare,
      range10: sig.range10,
      voldry: sig.voldry,
      atrPct: sig.atrPct,
      contractionCount: null,
      queue,
      reviewStatus: review?.status ?? 'new',
      note: review?.note ?? '',
      thesis: review?.thesis ?? '',
    })
  }

  // Watchlist candidates that aren't already signals
  for (const wl of watchlist) {
    if (seenSymbols.has(wl.symbol)) continue
    const key = `${wl.symbol}:${scanDate}`
    const review = reviews[key]

    let queue: QueueType = 'nearPivot'
    if (review?.status === 'shortlisted') queue = 'shortlisted'
    else if (review?.status === 'rejected') queue = 'rejected'

    rows.push({
      id: key,
      scanDate,
      symbol: wl.symbol,
      signalType: 'WATCH',
      close: wl.close,
      pivot: wl.pivot,
      nearPivot: wl.nearPivot,
      rsRank: wl.rsRank,
      volMult: 0,
      entryRef: null,
      stop: null,
      rPerShare: null,
      range10: wl.range10,
      voldry: wl.voldry,
      atrPct: wl.atrPct,
      contractionCount: null,
      queue,
      reviewStatus: review?.status ?? 'new',
      note: review?.note ?? '',
      thesis: review?.thesis ?? '',
    })
  }

  return rows
}

export function MarketScanner() {
  const scanDate = useUIStore((s) => s.scanDate)
  const setScanDate = useUIStore((s) => s.setScanDate)
  const activeQueue = useScannerStore((s) => s.activeQueue)
  const selectedSetupId = useScannerStore((s) => s.selectedSetupId)
  const setSelectedSetup = useScannerStore((s) => s.setSelectedSetup)
  const reviews = useReviewsStore((s) => s.reviews)

  const { data: scanDates = [] } = useScanDates()

  // Auto-set to most recent date on first load only
  useEffect(() => {
    if (scanDates.length && (!scanDate || !scanDates.includes(scanDate))) {
      setScanDate(scanDates[0])
    }
  }, [scanDates, scanDate, setScanDate])

  // Fetch signals and watchlist for the selected date
  const { data: signals = [], isLoading: signalsLoading, error: signalsError, refetch: refetchSignals } = useSignals(scanDate)
  const { data: watchlist = [], isLoading: watchlistLoading, error: watchlistError, refetch: refetchWatchlist } = useWatchlist(scanDate)
  const dataLoading = signalsLoading || watchlistLoading
  const dataError = signalsError ?? watchlistError

  // Build setup rows
  const allRows = useMemo(
    () => buildSetupRows(signals, watchlist, scanDate, reviews),
    [signals, watchlist, scanDate, reviews]
  )

  // Filter by active queue
  const filteredRows = useMemo(
    () => allRows.filter((r) => r.queue === activeQueue),
    [allRows, activeQueue]
  )

  // Compute counts for all queues
  const counts = useMemo(() => {
    const c: Record<QueueType, number> = {
      breakout: 0,
      nearPivot: 0,
      shortlisted: 0,
      rejected: 0,
    }
    for (const row of allRows) {
      c[row.queue]++
    }
    return c
  }, [allRows])

  // IDs for keyboard navigation
  const setupIds = useMemo(
    () => filteredRows.map((r) => r.id),
    [filteredRows]
  )

  // Auto-select first row when queue changes or data loads
  useEffect(() => {
    if (setupIds.length > 0 && (!selectedSetupId || !setupIds.includes(selectedSetupId))) {
      setSelectedSetup(setupIds[0])
    }
  }, [setupIds, selectedSetupId, setSelectedSetup])

  // Get selected setup
  const selectedSetup = useMemo(
    () => allRows.find((r) => r.id === selectedSetupId) ?? null,
    [allRows, selectedSetupId]
  )

  // Fetch OHLCV for selected symbol
  const selectedSymbol = selectedSetup?.symbol ?? null
  const { data: ohlcvData = [], isLoading: ohlcvLoading } =
    useOHLCV(selectedSymbol)

  // Signal history + chart markers for selected symbol
  const { data: signalHistory } = useSymbolSignalHistory(selectedSymbol ?? undefined)
  const markers = useMemo(
    () =>
      signalHistory
        ? buildChartMarkers(signalHistory.signals, signalHistory.trades)
        : [],
    [signalHistory]
  )

  // Wire keyboard triage
  useKeyboardTriage(setupIds)

  return (
    <div className="flex-1 min-h-0 overflow-hidden">
      <Group orientation="horizontal">
        <Panel defaultSize="45" minSize="25">
          <div className="flex flex-col h-full">
            <div className="flex items-center justify-between px-2 py-1.5 border-b border-border-default">
              <ScanDatePicker
                scanDate={scanDate}
                scanDates={scanDates}
                onDateChange={setScanDate}
              />
            </div>
            <QueueTabs counts={counts} />
            <ScannerTable
              rows={filteredRows}
              selectedId={selectedSetupId}
              onSelect={setSelectedSetup}
              isLoading={dataLoading}
              error={dataError instanceof Error ? dataError : null}
              onRetry={() => { refetchSignals(); refetchWatchlist() }}
            />
          </div>
        </Panel>

        <Separator className="w-px bg-border-default hover:bg-border-hover transition-colors" />

        <Panel defaultSize="55" minSize="30">
          <SetupDetailCard
            setup={selectedSetup}
            ohlcvData={ohlcvData}
            isLoading={ohlcvLoading}
            markers={markers}
          />
        </Panel>
      </Group>
    </div>
  )
}

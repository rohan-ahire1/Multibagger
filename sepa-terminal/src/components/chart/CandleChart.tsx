import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { X } from 'lucide-react'
import type { BacktestTrade, OHLCVBar, Signal, UnfilledSignalOutcome } from '@/lib/types'
import { useUIStore } from '@/stores/ui'
import { cn, fmtDate, fmtDateShort, fmtPctRaw, fmtPrice, fmtR } from '@/lib/utils'

const SMA_PERIODS = [
  { period: 50, color: 'accent' },
  { period: 150, color: 'warn' },
  { period: 200, color: 'down' },
] as const

type OrderedMarker = {
  time: Time
  position: 'aboveBar' | 'belowBar' | 'inBar'
  color: string
  shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'
  text?: string
  order: number
}

type TradeRegion = {
  left: number
  width: number
  containerWidth: number
}

type TradeVisual = {
  state: 'profit' | 'loss'
  isHypothetical: boolean
  pendingKind?: 'waiting' | 'expired' | 'skipped' | 'signal_only'
  status: string
  startDate: string
  entryDate: string | null
  endDate: string
  entryPrice: number | null
  endPrice: number
  returnPct: number | null
  rMultiple: number | null
  holdingDays: number
  isOpen: boolean
  entryRef: number | null
  stop: number | null
  pivot: number | null
}

type ChartTimeframe = '5Y' | '2Y' | '1Y' | '6M'

const TIMEFRAME_OPTIONS: ChartTimeframe[] = ['5Y', '2Y', '1Y', '6M']
const TIMEFRAME_MONTHS: Record<ChartTimeframe, number> = {
  '5Y': 60,
  '2Y': 24,
  '1Y': 12,
  '6M': 6,
}

function toTime(date: string): UTCTimestamp {
  return (new Date(`${date}T00:00:00Z`).getTime() / 1000) as UTCTimestamp
}

function sma(bars: OHLCVBar[], period: number) {
  const out: { time: UTCTimestamp; value: number }[] = []
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close
    if (i >= period) sum -= bars[i - period].close
    if (i >= period - 1) out.push({ time: toTime(bars[i].date), value: sum / period })
  }
  return out
}

function chartVisibleRange(
  bars: OHLCVBar[],
  timeframe: ChartTimeframe,
  tradeVisual: TradeVisual | null
) {
  const firstTime = new Date(`${bars[0].date}T00:00:00Z`).getTime()
  const lastTime = new Date(`${bars[bars.length - 1].date}T00:00:00Z`).getTime()
  const duration = TIMEFRAME_MONTHS[timeframe] * 30.4375 * 24 * 60 * 60 * 1000

  let fromTime: number
  let toTimeMs: number
  if (tradeVisual) {
    const tradeStart = new Date(`${tradeVisual.startDate}T00:00:00Z`).getTime()
    const tradeEnd = new Date(`${tradeVisual.endDate}T00:00:00Z`).getTime()
    const centre = (tradeStart + tradeEnd) / 2
    fromTime = centre - duration / 2
    toTimeMs = centre + duration / 2
  } else {
    toTimeMs = lastTime
    fromTime = lastTime - duration
  }

  // Preserve the requested duration when the window runs into either edge of
  // the available history.
  if (toTimeMs > lastTime) {
    fromTime -= toTimeMs - lastTime
    toTimeMs = lastTime
  }
  if (fromTime < firstTime) {
    toTimeMs = Math.min(lastTime, toTimeMs + (firstTime - fromTime))
    fromTime = firstTime
  }

  const fromBar = bars.find((bar) => new Date(`${bar.date}T00:00:00Z`).getTime() >= fromTime)
    ?? bars[0]
  const toBar = [...bars]
    .reverse()
    .find((bar) => new Date(`${bar.date}T00:00:00Z`).getTime() <= toTimeMs)
    ?? bars[bars.length - 1]

  return { from: toTime(fromBar.date), to: toTime(toBar.date) }
}

export function CandleChart({
  bars,
  signals,
  selectedSignal,
  selectedSignalDate,
  selectedTrade,
  unfilledOutcome,
  pivot,
  stop,
  height = 460,
}: {
  bars: OHLCVBar[]
  signals?: Signal[]
  selectedSignal?: Signal | null
  selectedSignalDate?: string | null
  selectedTrade?: BacktestTrade | null
  unfilledOutcome?: UnfilledSignalOutcome | null
  pivot?: number | null
  stop?: number | null
  height?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [tradeRegion, setTradeRegion] = useState<TradeRegion | null>(null)
  const [tradeDetailsOpen, setTradeDetailsOpen] = useState(false)
  const [timeframe, setTimeframe] = useState<ChartTimeframe>('1Y')
  const theme = useUIStore((state) => state.theme)

  const tradeVisual = useMemo<TradeVisual | null>(() => {
    if (!bars.length) return null

    const latestBar = bars[bars.length - 1]
    const signalDate = selectedSignalDate ?? selectedTrade?.signalDate ?? selectedSignal?.signalDate
    const isSelectedBreakout = selectedSignal?.signalType === 'BREAKOUT' || selectedTrade != null
    if (!signalDate || !isSelectedBreakout) return null

    if (!selectedTrade?.entryDate || selectedTrade.entryPrice == null) {
      if (selectedTrade?.status !== 'not_filled' || !unfilledOutcome) return null
      return {
        state: (unfilledOutcome.returnPct ?? 0) >= 0 ? 'profit' : 'loss',
        isHypothetical: true,
        pendingKind: 'expired',
        status: selectedTrade.status,
        startDate: signalDate,
        entryDate: null,
        endDate: unfilledOutcome.endDate,
        entryPrice: null,
        endPrice: unfilledOutcome.endPrice,
        returnPct: unfilledOutcome.returnPct,
        rMultiple: unfilledOutcome.rMultiple,
        holdingDays: unfilledOutcome.holdingBars,
        isOpen: false,
        entryRef: unfilledOutcome.entryRef,
        stop: unfilledOutcome.stop,
        pivot: unfilledOutcome.pivot,
      }
    }

    const endDate = selectedTrade.exitDate ?? latestBar.date
    const endPrice = selectedTrade.exitPrice ?? latestBar.close
    const returnPct = selectedTrade.returnPct
      ?? ((endPrice - selectedTrade.entryPrice) / selectedTrade.entryPrice) * 100
    const initialRisk = selectedTrade.entryPrice - selectedTrade.stop
    const rMultiple = selectedTrade.rMultiple
      ?? (initialRisk > 0 ? (endPrice - selectedTrade.entryPrice) / initialRisk : null)
    const entryIndex = bars.findIndex((bar) => bar.date === selectedTrade.entryDate)
    const endIndex = bars.findIndex((bar) => bar.date === endDate)

    return {
      state: returnPct >= 0 ? 'profit' : 'loss',
      isHypothetical: false,
      status: selectedTrade.status,
      startDate: selectedSignalDate ?? selectedTrade.entryDate,
      entryDate: selectedTrade.entryDate,
      endDate,
      entryPrice: selectedTrade.entryPrice,
      endPrice,
      returnPct,
      rMultiple,
      holdingDays: selectedTrade.holdingDays
        ?? (entryIndex >= 0 && endIndex >= entryIndex ? endIndex - entryIndex : 0),
      isOpen: selectedTrade.exitDate == null,
      entryRef: selectedTrade.entryRef,
      stop: selectedTrade.stop,
      pivot: selectedTrade.pivot,
    }
  }, [bars, selectedSignal, selectedSignalDate, selectedTrade, unfilledOutcome])

  const markers = useMemo(() => {
    const styles = getComputedStyle(document.documentElement)
    const up = styles.getPropertyValue('--color-up').trim() || (theme === 'dark' ? '#52c994' : '#16835d')
    const accent =
      styles.getPropertyValue('--color-accent').trim() || (theme === 'dark' ? '#7c9df5' : '#3563e9')
    const down = styles.getPropertyValue('--color-down').trim() || (theme === 'dark' ? '#ef7d7d' : '#c63c3c')
    const warn = styles.getPropertyValue('--color-warn').trim() || (theme === 'dark' ? '#e8b86d' : '#a56a16')
    const barDates = new Set(bars.map((b) => b.date))
    const signalMarkers: OrderedMarker[] = (signals ?? [])
      .filter((s) => barDates.has(s.signalDate))
      .sort((a, b) => a.signalDate.localeCompare(b.signalDate))
      .map((s): OrderedMarker => ({
        time: toTime(s.signalDate) as Time,
        position: 'belowBar' as const,
        color: s.signalDate === selectedSignalDate ? warn : s.signalType === 'BREAKOUT' ? up : accent,
        shape: (s.signalType === 'BREAKOUT' ? 'arrowUp' : 'circle') as 'arrowUp' | 'circle',
        text: s.signalDate === selectedSignalDate
          ? `★ SELECTED ${s.signalType === 'BREAKOUT' ? 'BO' : 'WATCH'}`
          : s.signalType === 'BREAKOUT' ? 'BO' : 'W',
        order: s.signalDate === selectedSignalDate ? 1 : 0,
      }))

    if (
      selectedSignalDate
      && barDates.has(selectedSignalDate)
      && !signalMarkers.some((marker) => marker.time === toTime(selectedSignalDate))
    ) {
      signalMarkers.push({
        time: toTime(selectedSignalDate) as Time,
        position: 'belowBar',
        color: warn,
        shape: 'arrowUp',
        text: '★ SELECTED BO',
        order: 1,
      })
    }

    if (selectedTrade?.entryDate && barDates.has(selectedTrade.entryDate)) {
      signalMarkers.push({
        time: toTime(selectedTrade.entryDate) as Time,
        position: 'belowBar',
        color: accent,
        shape: 'arrowUp',
        text: `ENTRY${selectedTrade.entryPrice != null ? ` ${selectedTrade.entryPrice.toFixed(2)}` : ''}`,
        order: 2,
      })
    }

    if (selectedTrade?.exitDate && barDates.has(selectedTrade.exitDate)) {
      const isWin = (selectedTrade.returnPct ?? 0) >= 0
      signalMarkers.push({
        time: toTime(selectedTrade.exitDate) as Time,
        position: 'aboveBar',
        color: isWin ? up : down,
        shape: 'arrowDown',
        text: `EXIT ${fmtPctRaw(selectedTrade.returnPct)} · ${fmtR(selectedTrade.rMultiple)}`,
        order: 3,
      })
    }

    return signalMarkers
      .sort((a, b) => Number(a.time) - Number(b.time) || a.order - b.order)
      .map((marker) => ({
        time: marker.time,
        position: marker.position,
        color: marker.color,
        shape: marker.shape,
        text: marker.text,
      }))
  }, [signals, bars, selectedSignalDate, selectedTrade, theme])

  useEffect(() => {
    setTradeDetailsOpen(false)
  }, [selectedSignalDate])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !bars.length) return

    const styles = getComputedStyle(document.documentElement)
    const palette = {
      surface: styles.getPropertyValue('--color-surface').trim(),
      surface2: styles.getPropertyValue('--color-surface-2').trim(),
      border: styles.getPropertyValue('--color-border').trim(),
      inkSoft: styles.getPropertyValue('--color-ink-soft').trim(),
      accent: styles.getPropertyValue('--color-accent').trim(),
      up: styles.getPropertyValue('--color-up').trim(),
      down: styles.getPropertyValue('--color-down').trim(),
      warn: styles.getPropertyValue('--color-warn').trim(),
      volumeUp: styles.getPropertyValue('--chart-volume-up').trim(),
      volumeDown: styles.getPropertyValue('--chart-volume-down').trim(),
    }

    const chart = createChart(container, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: palette.surface },
        textColor: palette.inkSoft,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.surface2 },
        horzLines: { color: palette.surface2 },
      },
      rightPriceScale: { borderColor: palette.border },
      timeScale: { borderColor: palette.border, timeVisible: false },
      crosshair: {
        horzLine: { labelBackgroundColor: palette.accent },
        vertLine: { labelBackgroundColor: palette.accent },
      },
    })
    chartRef.current = chart

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    })
    candles.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    )

    for (const { period, color } of SMA_PERIODS) {
      if (bars.length < period) continue
      const line = chart.addSeries(LineSeries, {
        color: palette[color],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      line.setData(sma(bars, period))
    }

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    volume.setData(
      bars.map((b) => ({
        time: toTime(b.date),
        value: b.volume,
        color: b.close >= b.open ? palette.volumeUp : palette.volumeDown,
      }))
    )

    if (pivot != null && pivot > 0) {
      candles.createPriceLine({
        price: pivot,
        color: palette.accent,
        lineWidth: 1,
        lineStyle: 2,
        title: 'pivot',
      })
    }
    if (stop != null && stop > 0) {
      candles.createPriceLine({
        price: stop,
        color: palette.down,
        lineWidth: 1,
        lineStyle: 2,
        title: 'stop',
      })
    }
    if (selectedTrade?.entryPrice != null && selectedTrade.entryPrice > 0) {
      candles.createPriceLine({
        price: selectedTrade.entryPrice,
        color: palette.accent,
        lineWidth: 2,
        lineStyle: 0,
        title: 'selected entry',
      })
    }
    if (selectedTrade?.exitPrice != null && selectedTrade.exitPrice > 0) {
      candles.createPriceLine({
        price: selectedTrade.exitPrice,
        color: (selectedTrade.returnPct ?? 0) >= 0 ? palette.up : palette.down,
        lineWidth: 2,
        lineStyle: 0,
        title: 'selected exit',
      })
    }

    if (markers.length) createSeriesMarkers(candles, markers)

    chart.timeScale().setVisibleRange(chartVisibleRange(bars, timeframe, tradeVisual))

    const updateTradeRegion = () => {
      if (!tradeVisual) {
        setTradeRegion(null)
        return
      }

      const startX = chart.timeScale().timeToCoordinate(toTime(tradeVisual.startDate))
      const endX = chart.timeScale().timeToCoordinate(toTime(tradeVisual.endDate))
      if (startX == null || endX == null) {
        setTradeRegion(null)
        return
      }

      const containerWidth = container.clientWidth
      const rawLeft = Math.min(startX, endX)
      const rawRight = Math.max(startX, endX)
      if (rawRight < 0 || rawLeft > containerWidth) {
        setTradeRegion(null)
        return
      }

      const left = Math.max(0, rawLeft - 3)
      const right = Math.min(containerWidth, rawRight + 3)
      const centre = (left + right) / 2
      const displayWidth = Math.min(containerWidth, Math.max(28, right - left))
      const displayLeft = Math.min(
        Math.max(0, centre - displayWidth / 2),
        Math.max(0, containerWidth - displayWidth)
      )
      setTradeRegion({
        left: displayLeft,
        width: displayWidth,
        containerWidth,
      })
    }

    const handleChartClick = (param: MouseEventParams<Time>) => {
      if (!tradeVisual || param.time == null) return
      const clickedTime = Number(param.time)
      const startTime = Number(toTime(tradeVisual.startDate))
      const endTime = Number(toTime(tradeVisual.endDate))
      if (clickedTime >= Math.min(startTime, endTime) && clickedTime <= Math.max(startTime, endTime)) {
        setTradeDetailsOpen(true)
      }
    }

    const timeScale = chart.timeScale()
    timeScale.subscribeVisibleTimeRangeChange(updateTradeRegion)
    chart.subscribeClick(handleChartClick)
    const resizeObserver = new ResizeObserver(updateTradeRegion)
    resizeObserver.observe(container)
    requestAnimationFrame(updateTradeRegion)

    return () => {
      timeScale.unsubscribeVisibleTimeRangeChange(updateTradeRegion)
      chart.unsubscribeClick(handleChartClick)
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [bars, markers, pivot, stop, selectedTrade, tradeVisual, timeframe, height, theme])

  const labelCentre = tradeRegion
    ? Math.min(
        Math.max(tradeRegion.left + tradeRegion.width / 2, 380),
        Math.max(380, tradeRegion.containerWidth - 175)
      )
    : 0

  return (
    <div className="relative w-full overflow-hidden" style={{ height }}>
      <div ref={containerRef} className="absolute inset-0" />

      <div className="absolute left-2 top-2 z-30 flex items-center rounded-lg border border-border-strong/80 bg-surface/95 p-1 shadow-lg backdrop-blur-sm">
        {TIMEFRAME_OPTIONS.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={timeframe === option}
            onClick={() => setTimeframe(option)}
            className={cn(
              'h-7 min-w-10 rounded-md px-2 text-[11px] font-bold transition',
              timeframe === option
                ? 'bg-accent text-white shadow-sm'
                : 'text-ink-faint hover:bg-surface-2 hover:text-ink'
            )}
          >
            {option}
          </button>
        ))}
      </div>

      {tradeRegion && tradeVisual && (
        <>
          <div
            aria-hidden="true"
            className={cn(
              'pointer-events-none absolute inset-y-0 z-[2] border-x-2',
              tradeVisual.state === 'profit'
                && 'border-up/70 bg-up/20 shadow-[inset_0_0_32px_color-mix(in_srgb,var(--color-up)_22%,transparent)]',
              tradeVisual.state === 'loss'
                && 'border-down/70 bg-down/20 shadow-[inset_0_0_32px_color-mix(in_srgb,var(--color-down)_22%,transparent)]'
            )}
            style={{ left: tradeRegion.left, width: tradeRegion.width }}
          />
          <button
            type="button"
            onClick={() => setTradeDetailsOpen(true)}
            className={cn(
              'absolute top-2 z-20 -translate-x-1/2 rounded-lg border-2 px-3 py-2 text-left shadow-lg backdrop-blur-sm transition hover:-translate-y-0.5 hover:shadow-xl',
              tradeVisual.state === 'profit' && 'border-up/60 bg-up-soft text-up',
              tradeVisual.state === 'loss' && 'border-down/60 bg-down-soft text-down'
            )}
            style={{ left: labelCentre }}
            title="Open complete trade details"
          >
            <div className="text-[12px] font-extrabold tracking-[-0.01em]">
              {tradeVisual.isHypothetical
                ? `${pendingTitle(tradeVisual)} · HYPOTHETICAL ${fmtPctRaw(tradeVisual.returnPct)} · ${tradeVisual.holdingDays} bars`
                : `${tradeVisual.isOpen ? 'OPEN TRADE' : tradeVisual.state === 'profit' ? 'PROFIT' : 'LOSS'} · ${tradeVisual.holdingDays} days · ${fmtPctRaw(tradeVisual.returnPct)}`}
            </div>
            <div className="mt-0.5 whitespace-nowrap text-[10px] font-semibold opacity-85">
              {tradeVisual.isHypothetical
                ? `Trigger ${fmtPrice(tradeVisual.entryRef)} · end ${fmtPrice(tradeVisual.endPrice)}`
                : `${fmtPrice(tradeVisual.entryPrice)} → ${fmtPrice(tradeVisual.endPrice)}`} · click for details
            </div>
          </button>
        </>
      )}

      {tradeDetailsOpen && tradeVisual && (
        <TradeDetailsCard
          trade={selectedTrade}
          signal={selectedSignal}
          visual={tradeVisual}
          signalDate={selectedSignalDate}
          onClose={() => setTradeDetailsOpen(false)}
        />
      )}
    </div>
  )
}

function TradeDetailsCard({
  trade,
  signal,
  visual,
  signalDate,
  onClose,
}: {
  trade?: BacktestTrade | null
  signal?: Signal | null
  visual: TradeVisual
  signalDate?: string | null
  onClose: () => void
}) {
  const isHypothetical = visual.isHypothetical
  const resultTone = visual.state === 'profit'
    ? 'text-up'
    : 'text-down'
  const resultSurface = visual.state === 'profit'
    ? 'border-up/60 bg-up-soft/95'
    : 'border-down/60 bg-down-soft/95'
  const distanceToEntry = isHypothetical && visual.entryRef != null && visual.entryRef > 0
    ? ((visual.endPrice - visual.entryRef) / visual.entryRef) * 100
    : null
  const breakoutDate = signalDate ?? trade?.signalDate ?? signal?.signalDate

  return (
    <div
      className={cn(
        'absolute right-4 top-16 z-40 w-[min(500px,calc(100%-2rem))] overflow-hidden rounded-2xl border-2 shadow-2xl backdrop-blur-xl',
        resultSurface
      )}
    >
      <div className="flex items-start gap-3 border-b border-current/15 px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-faint">
            Selected breakout outcome
          </div>
          {isHypothetical && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="rounded-md border border-down/35 bg-down-soft px-2.5 py-1 text-[11px] font-black tracking-[0.08em] text-down">
                NOT FILLED · {pendingTitle(visual)}
              </span>
              <span className="rounded-md border border-warn/40 bg-warn-soft px-2.5 py-1 text-[11px] font-black tracking-[0.08em] text-warn">
                HYPOTHETICAL — NOT REALISED P&amp;L
              </span>
            </div>
          )}
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className={cn('tnum text-[34px] font-black tracking-[-0.05em]', resultTone)}>
              {fmtPctRaw(visual.returnPct)}
            </span>
            <span className={cn('tnum text-[19px] font-bold', resultTone)}>
              {fmtR(visual.rMultiple)}
            </span>
            <span className="text-[13px] font-semibold text-ink-soft">
              {isHypothetical
                ? `hypothetical move over ${visual.holdingDays} available bars`
                : `over ${visual.holdingDays} trading days`}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close trade details"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-current/20 bg-surface/70 text-ink-soft hover:bg-surface hover:text-ink"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-px bg-border/70 sm:grid-cols-4">
        <TradeDetail label="Breakout" value={fmtDate(breakoutDate)} />
        <TradeDetail label={isHypothetical ? 'Planned entry' : 'Entry date'} value={isHypothetical ? fmtPrice(visual.entryRef) : fmtDate(visual.entryDate)} />
        <TradeDetail label={visual.isOpen || isHypothetical ? 'Window end' : 'Exit date'} value={fmtDate(visual.endDate)} />
        <TradeDetail label="Execution" value={isHypothetical ? 'not filled' : visual.isOpen ? 'open' : visual.status.replace(/_/g, ' ')} />
        <TradeDetail label={isHypothetical ? 'Window-end price' : 'Entry price'} value={fmtPrice(isHypothetical ? visual.endPrice : visual.entryPrice)} emphasis />
        <TradeDetail label={isHypothetical ? 'Hypothetical return' : visual.isOpen ? 'Current price' : 'Exit price'} value={isHypothetical ? fmtPctRaw(visual.returnPct) : fmtPrice(visual.endPrice)} emphasis />
        <TradeDetail label="Initial stop" value={fmtPrice(visual.stop)} />
        <TradeDetail label="Pivot" value={fmtPrice(visual.pivot)} />
        <TradeDetail label={isHypothetical ? 'Vs entry trigger' : 'MFE'} value={isHypothetical ? fmtPctRaw(distanceToEntry) : fmtR(trade?.mfeR)} tone={isHypothetical ? visual.state === 'profit' ? 'up' : 'down' : (trade?.mfeR ?? 0) >= 0 ? 'up' : 'down'} />
        <TradeDetail label="MAE" value={isHypothetical ? '—' : fmtR(trade?.maeR)} tone={!isHypothetical && (trade?.maeR ?? 0) <= 0 ? 'down' : undefined} />
        <TradeDetail label="Giveback" value={isHypothetical ? '—' : fmtPctRaw(trade?.givebackPct)} />
        <TradeDetail label={isHypothetical ? 'Signal window' : 'Duration'} value={`${visual.holdingDays} bars`} />
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-current/15 bg-surface/70 px-5 py-3 text-[12px] text-ink-soft">
        <span className="font-semibold text-ink">{isHypothetical ? 'Execution:' : 'Exit reason:'}</span>
        <span>{isHypothetical
          ? pendingExplanation(visual)
          : visual.isOpen
            ? 'Position remains open at the latest available close'
            : trade?.exitReason ?? trade?.exitPhase ?? '—'}</span>
        <span className="ml-auto tnum text-[11px] text-ink-faint">
          {fmtDateShort(isHypothetical ? breakoutDate : visual.entryDate)} → {fmtDateShort(visual.endDate)}
        </span>
      </div>
    </div>
  )
}

function pendingTitle(visual: TradeVisual): string {
  switch (visual.pendingKind) {
    case 'waiting':
      return 'WAITING FOR ENTRY'
    case 'expired':
      return 'ENTRY NEVER TRIGGERED'
    case 'skipped':
      return 'PORTFOLIO SKIPPED'
    default:
      return 'BREAKOUT SIGNAL ONLY'
  }
}

function pendingExplanation(visual: TradeVisual): string {
  switch (visual.pendingKind) {
    case 'waiting':
      return 'Entry trigger remains valid but has not filled within the available bars'
    case 'expired':
      return 'Entry trigger was not reached before the configured pending window expired'
    case 'skipped':
      return `No position was opened because the portfolio result was ${visual.status.replace(/_/g, ' ')}`
    default:
      return 'The breakout exists, but this cached portfolio run has no matching trade record'
  }
}

function TradeDetail({
  label,
  value,
  emphasis = false,
  tone,
}: {
  label: string
  value: string
  emphasis?: boolean
  tone?: 'up' | 'down'
}) {
  return (
    <div className="min-w-0 bg-surface/90 px-3.5 py-3">
      <div className="text-[10px] font-bold uppercase tracking-wider text-ink-faint">{label}</div>
      <div
        className={cn(
          'tnum mt-1 truncate text-[14px] font-semibold capitalize text-ink',
          emphasis && 'text-[17px] font-extrabold',
          tone === 'up' && 'text-up',
          tone === 'down' && 'text-down'
        )}
        title={value}
      >
        {value}
      </div>
    </div>
  )
}

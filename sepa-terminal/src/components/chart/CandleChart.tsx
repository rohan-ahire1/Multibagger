import { useEffect, useMemo, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { OHLCVBar, Signal } from '@/lib/types'

const SMA_PERIODS = [
  { period: 50, color: '#2557d6' },
  { period: 150, color: '#b0700a' },
  { period: 200, color: '#c23934' },
] as const

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

export function CandleChart({
  bars,
  signals,
  pivot,
  stop,
  height = 460,
}: {
  bars: OHLCVBar[]
  signals?: Signal[]
  pivot?: number | null
  stop?: number | null
  height?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  const markers = useMemo(() => {
    if (!signals?.length) return []
    const barDates = new Set(bars.map((b) => b.date))
    return signals
      .filter((s) => barDates.has(s.signalDate))
      .sort((a, b) => a.signalDate.localeCompare(b.signalDate))
      .map((s) => ({
        time: toTime(s.signalDate) as Time,
        position: 'belowBar' as const,
        color: s.signalType === 'BREAKOUT' ? '#1a7f4b' : '#2557d6',
        shape: (s.signalType === 'BREAKOUT' ? 'arrowUp' : 'circle') as 'arrowUp' | 'circle',
        text: s.signalType === 'BREAKOUT' ? 'BO' : 'W',
      }))
  }, [signals, bars])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !bars.length) return

    const chart = createChart(container, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#55554f',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: '#f1f1ef' },
        horzLines: { color: '#f1f1ef' },
      },
      rightPriceScale: { borderColor: '#e4e4e0' },
      timeScale: { borderColor: '#e4e4e0', timeVisible: false },
      crosshair: {
        horzLine: { labelBackgroundColor: '#2557d6' },
        vertLine: { labelBackgroundColor: '#2557d6' },
      },
    })
    chartRef.current = chart

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#1a7f4b',
      downColor: '#c23934',
      borderUpColor: '#1a7f4b',
      borderDownColor: '#c23934',
      wickUpColor: '#1a7f4b',
      wickDownColor: '#c23934',
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
        color,
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
        color: b.close >= b.open ? 'rgba(26,127,75,0.35)' : 'rgba(194,57,52,0.35)',
      }))
    )

    if (pivot != null && pivot > 0) {
      candles.createPriceLine({
        price: pivot,
        color: '#2557d6',
        lineWidth: 1,
        lineStyle: 2,
        title: 'pivot',
      })
    }
    if (stop != null && stop > 0) {
      candles.createPriceLine({
        price: stop,
        color: '#c23934',
        lineWidth: 1,
        lineStyle: 2,
        title: 'stop',
      })
    }

    if (markers.length) createSeriesMarkers(candles, markers)

    // Land on the last ~9 months but keep full history scrollable
    const from = bars[Math.max(0, bars.length - 190)]
    chart.timeScale().setVisibleRange({
      from: toTime(from.date),
      to: toTime(bars[bars.length - 1].date),
    })

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [bars, markers, pivot, stop, height])

  return <div ref={containerRef} className="w-full" style={{ height }} />
}

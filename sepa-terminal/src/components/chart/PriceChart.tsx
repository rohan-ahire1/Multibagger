import { useRef, useEffect } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import type { OHLCVBar, ChartOverlays, ChartTimeframe } from '@/lib/types'

interface PriceChartProps {
  data: OHLCVBar[]
  entryRef?: number
  stop?: number
  pivot?: number
  overlays: ChartOverlays
  timeframe: ChartTimeframe
  markers?: SeriesMarker<Time>[]
  contractionSwings?: { date: string; price: number; type: 'high' | 'low' }[]
}

function computeSMA(data: OHLCVBar[], period: number) {
  const result: { time: string; value: number }[] = []
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += data[j].close
    result.push({ time: data[i].date, value: sum / period })
  }
  return result
}

function computeEMA(data: OHLCVBar[], period: number) {
  if (data.length === 0) return []
  const k = 2 / (period + 1)
  const result: { time: string; value: number }[] = []
  let ema = data[0].close
  for (let i = 0; i < data.length; i++) {
    ema = data[i].close * k + ema * (1 - k)
    if (i >= period - 1) {
      result.push({ time: data[i].date, value: ema })
    }
  }
  return result
}

function computeTrailingLow(data: OHLCVBar[], period: number) {
  const result: { time: string; value: number }[] = []
  for (let i = period - 1; i < data.length; i++) {
    let low = data[i].low
    for (let j = i - period + 1; j < i; j++) low = Math.min(low, data[j].low)
    result.push({ time: data[i].date, value: low })
  }
  return result
}

function resampleWeekly(data: OHLCVBar[]): OHLCVBar[] {
  const weeks = new Map<string, OHLCVBar>()
  for (const bar of data) {
    const d = new Date(bar.date)
    // Get Monday of the ISO week
    const day = d.getDay()
    const diff = d.getDate() - day + (day === 0 ? -6 : 1)
    const monday = new Date(d)
    monday.setDate(diff)
    const key = monday.toISOString().slice(0, 10)

    const existing = weeks.get(key)
    if (existing) {
      existing.high = Math.max(existing.high, bar.high)
      existing.low = Math.min(existing.low, bar.low)
      existing.close = bar.close
      existing.volume += bar.volume
    } else {
      weeks.set(key, { ...bar, date: key })
    }
  }
  return Array.from(weeks.values()).sort((a, b) => a.date.localeCompare(b.date))
}

function resample(data: OHLCVBar[], timeframe: ChartTimeframe): OHLCVBar[] {
  switch (timeframe) {
    case 'W':
      return resampleWeekly(data)
    default:
      return data
  }
}

/** Map a daily date string to its weekly/monthly bucket key */
function toBucketKey(dateStr: string | undefined | null, timeframe: ChartTimeframe): string {
  if (!dateStr) return ''
  if (timeframe === 'W') {
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return ''
    const day = d.getDay()
    const diff = d.getDate() - day + (day === 0 ? -6 : 1)
    const monday = new Date(d)
    monday.setDate(diff)
    return monday.toISOString().slice(0, 10)
  }
  return dateStr
}

/**
 * When chart is resampled (W/M), remap marker dates to bucket dates
 * and deduplicate so only one marker per type per candle is shown.
 */
function deduplicateMarkers(
  markers: SeriesMarker<Time>[],
  timeframe: ChartTimeframe,
): SeriesMarker<Time>[] {
  if (!markers) return []
  if (timeframe === 'D') return markers

  const seen = new Set<string>()
  const result: SeriesMarker<Time>[] = []

  for (const m of markers) {
    if (!m || !m.time) continue
    const bucket = toBucketKey(m.time as string, timeframe)
    if (!bucket) continue
    const key = `${bucket}:${m.text ?? ''}:${m.color}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push({ ...m, time: bucket as unknown as Time })
  }

  result.sort((a, b) => (a.time as string).localeCompare(b.time as string))
  return result
}

export function PriceChart({
  data,
  entryRef,
  stop,
  pivot,
  overlays,
  timeframe,
  markers,
  contractionSwings,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    // Clean up previous chart
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const containerHeight = containerRef.current.clientHeight || 260

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#0F1117' },
        textColor: '#6B6D77',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: '#1E2030' },
        horzLines: { color: '#1E2030' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#1E2030' },
      timeScale: { borderColor: '#1E2030', timeVisible: false },
      width: containerRef.current.clientWidth,
      height: containerHeight,
    })

    chartRef.current = chart

    const resampled = resample(data, timeframe)

    // Candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: 'rgba(38,166,154,0.9)',
      downColor: 'rgba(239,83,80,0.9)',
      borderUpColor: 'rgba(38,166,154,0.9)',
      borderDownColor: 'rgba(239,83,80,0.9)',
      wickUpColor: 'rgba(38,166,154,0.9)',
      wickDownColor: 'rgba(239,83,80,0.9)',
    })

    candleSeries.setData(
      resampled.map((bar) => ({
        time: bar.date,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }))
    )

    if (markers && markers.length > 0) {
      const dedupedMarkers = deduplicateMarkers(markers, timeframe)
      createSeriesMarkers(candleSeries, dedupedMarkers)
    }

    // Volume histogram
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    volumeSeries.setData(
      resampled.map((bar) => ({
        time: bar.date,
        value: bar.volume,
        color:
          bar.close >= bar.open
            ? 'rgba(38,166,154,0.3)'
            : 'rgba(239,83,80,0.3)',
      }))
    )

    // SMA overlays
    if (overlays.sma50) {
      const sma50Series = chart.addSeries(LineSeries, {
        color: '#e8b03e',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      sma50Series.setData(computeSMA(resampled, 50))
    }

    if (overlays.sma150) {
      const sma150Series = chart.addSeries(LineSeries, {
        color: '#3b82f6',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      sma150Series.setData(computeSMA(resampled, 150))
    }

    if (overlays.sma200) {
      const sma200Series = chart.addSeries(LineSeries, {
        color: '#ef4444',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      sma200Series.setData(computeSMA(resampled, 200))
    }

    if (overlays.ema21) {
      const ema21Series = chart.addSeries(LineSeries, {
        color: '#a855f7',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      ema21Series.setData(computeEMA(resampled, 21))
    }

    if (overlays.trail10Low) {
      const trail10Series = chart.addSeries(LineSeries, {
        color: '#f97316',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      trail10Series.setData(computeTrailingLow(resampled, 10))
    }

    // Reference price lines
    if (overlays.refLines) {
      if (pivot != null) {
        candleSeries.createPriceLine({
          price: pivot,
          color: '#2563eb',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'Pivot',
        })
      }
      if (entryRef != null) {
        candleSeries.createPriceLine({
          price: entryRef,
          color: '#16a34a',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'Entry',
        })
      }
      if (stop != null) {
        candleSeries.createPriceLine({
          price: stop,
          color: '#dc2626',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'Stop',
        })
      }
    }

    // Contraction zig-zag lines
    if (contractionSwings && Array.isArray(contractionSwings) && contractionSwings.length > 0) {
      const contractionLineSeries = chart.addSeries(LineSeries, {
        color: '#e8b03e', // Gold/amber
        lineWidth: 2,     // Must be integer for LineWidth
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      })

      // Map date buckets for timeframe if active with explicit types
      const lineData = contractionSwings
        .map((pt) => {
          if (!pt || !pt.date || pt.price == null) return null
          const time = toBucketKey(pt.date, timeframe)
          if (!time) return null
          return { time, value: pt.price, originalDate: pt.date }
        })
        .filter((pt): pt is { time: string; value: number; originalDate: string } => pt !== null)

      // Sort chronologically based on their original dates first, then by bucketed time
      lineData.sort((a, b) => {
        const cmp = a.originalDate.localeCompare(b.originalDate)
        if (cmp !== 0) return cmp
        return a.time.localeCompare(b.time)
      })

      // Deduplicate by bucketed time so that only one point per time index is kept
      const uniqueLineData: { time: string; value: number }[] = []
      const seenTimes = new Set<string>()
      for (const pt of lineData) {
        if (!seenTimes.has(pt.time)) {
          seenTimes.add(pt.time)
          uniqueLineData.push({ time: pt.time, value: pt.value })
        }
      }

      // Sort the final unique list strictly by bucket time
      uniqueLineData.sort((a, b) => a.time.localeCompare(b.time))

      if (uniqueLineData.length > 0) {
        contractionLineSeries.setData(uniqueLineData)
      }
    }

    chart.timeScale().fitContent()

    // Resize observer — tracks both width and height
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height: h } = entry.contentRect
        if (width > 0 && h > 0) {
          chart.applyOptions({ width, height: h })
        }
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, overlays, timeframe, entryRef, stop, pivot, markers, contractionSwings])

  return (
    <div
      ref={containerRef}
      className="w-full h-full"
    />
  )
}

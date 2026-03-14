import { useRef, useEffect } from 'react'
import {
  createChart,
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
  height?: number
  markers?: SeriesMarker<Time>[]
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

function resampleMonthly(data: OHLCVBar[]): OHLCVBar[] {
  const months = new Map<string, OHLCVBar>()
  for (const bar of data) {
    const key = bar.date.slice(0, 7) + '-01'

    const existing = months.get(key)
    if (existing) {
      existing.high = Math.max(existing.high, bar.high)
      existing.low = Math.min(existing.low, bar.low)
      existing.close = bar.close
      existing.volume += bar.volume
    } else {
      months.set(key, { ...bar, date: key })
    }
  }
  return Array.from(months.values()).sort((a, b) => a.date.localeCompare(b.date))
}

function resample(data: OHLCVBar[], timeframe: ChartTimeframe): OHLCVBar[] {
  switch (timeframe) {
    case 'W':
      return resampleWeekly(data)
    case 'M':
      return resampleMonthly(data)
    default:
      return data
  }
}

export function PriceChart({
  data,
  entryRef,
  stop,
  pivot,
  overlays,
  timeframe,
  height = 260,
  markers,
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
      height,
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
      candleSeries.setMarkers(markers)
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

    chart.timeScale().fitContent()

    // Resize observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect
        chart.applyOptions({ width })
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, overlays, timeframe, height, entryRef, stop, pivot, markers])

  return (
    <div
      ref={containerRef}
      style={{ height }}
      className="w-full"
    />
  )
}

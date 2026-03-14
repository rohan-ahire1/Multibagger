import type { SeriesMarker, Time } from 'lightweight-charts'
import type { BacktestTrade, Signal } from './types'

const MARKER_CONFIG = {
  watch: { color: '#f59e0b', text: 'W' },
  breakout: { color: '#22c55e', text: 'B' },
  entry: { color: '#3b82f6', text: 'E' },
  winExit: { color: '#10b981', text: 'X+' },
  lossExit: { color: '#ef4444', text: 'X-' },
  notFilled: { color: '#6b7280', text: 'NF' },
} as const

function marker(time: string, config: { color: string; text: string }): SeriesMarker<Time> {
  return {
    time: time as unknown as Time,
    position: 'belowBar',
    shape: 'arrowUp',
    color: config.color,
    text: config.text,
  }
}

export function buildChartMarkers(
  signals: Signal[],
  trades: BacktestTrade[]
): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = []

  for (const s of signals) {
    if (s.signalType === 'WATCH') {
      markers.push(marker(s.signalDate, MARKER_CONFIG.watch))
    } else if (s.signalType === 'BREAKOUT') {
      markers.push(marker(s.signalDate, MARKER_CONFIG.breakout))
    }
  }

  for (const t of trades) {
    if (t.status === 'not_filled') {
      markers.push(marker(t.signalDate, MARKER_CONFIG.notFilled))
    } else {
      if (t.entryDate) {
        markers.push(marker(t.entryDate, MARKER_CONFIG.entry))
      }
      if (t.exitDate) {
        const isWin = t.rMultiple !== null && t.rMultiple > 0
        markers.push(marker(t.exitDate, isWin ? MARKER_CONFIG.winExit : MARKER_CONFIG.lossExit))
      }
    }
  }

  markers.sort((a, b) => (a.time as string).localeCompare(b.time as string))
  return markers
}

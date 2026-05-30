import type { SeriesMarker, Time } from 'lightweight-charts'
import type { BacktestTrade, Signal } from './types'

const MARKER_CONFIG = {
  watch: { color: '#f59e0b', text: 'W' },
  breakout: { color: '#10b981', text: 'B' }, // Green for Breakout
  entry: { color: '#3b82f6', text: 'E' },    // Blue for Entry
  winExit: { color: '#10b981', text: 'X+' },  // Green for Win Exit
  lossExit: { color: '#ef4444', text: 'X-' }, // Red for Loss Exit
  notFilled: { color: '#6b7280', text: 'NF' },
} as const

function marker(
  time: string,
  config: { color: string; text: string },
  position: 'aboveBar' | 'belowBar',
  shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'
): SeriesMarker<Time> {
  return {
    time: time as unknown as Time,
    position,
    shape,
    color: config.color,
    text: config.text,
  }
}

export function buildChartMarkers(
  signals?: Signal[],
  trades?: BacktestTrade[],
  contractionDates?: string[],
  contractionSequence?: number[],
  contractionHighs?: number[],
  contractionLows?: number[]
): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = []

  // 1. Contractions (rendered aboveBar as circles)
  if (contractionDates && contractionSequence && Array.isArray(contractionDates) && Array.isArray(contractionSequence)) {
    const len = Math.min(contractionDates.length, contractionSequence.length)
    for (let i = 0; i < len; i++) {
      const dateStr = contractionDates[i]
      const seqVal = contractionSequence[i]
      if (!dateStr || typeof dateStr !== 'string' || seqVal == null) continue

      let text = `C${i + 1}: ${seqVal.toFixed(0)}%`
      if (
        contractionHighs &&
        contractionLows &&
        Array.isArray(contractionHighs) &&
        Array.isArray(contractionLows) &&
        contractionHighs[i] != null &&
        contractionLows[i] != null &&
        contractionHighs[i] > 0
      ) {
        const highVal = contractionHighs[i]
        const lowVal = contractionLows[i]
        const actualPct = ((highVal - lowVal) / highVal) * 100
        text = `C${i + 1}: ${highVal.toFixed(0)}→${lowVal.toFixed(0)} (${actualPct.toFixed(0)}%)`
      }
      markers.push({
        time: dateStr as unknown as Time,
        position: 'aboveBar',
        shape: 'circle',
        color: '#e8b03e', // Gold/amber
        text,
      })
    }
  }

  // 2. Signals
  if (signals && Array.isArray(signals)) {
    for (const s of signals) {
      if (!s || !s.signalDate || typeof s.signalDate !== 'string') continue
      if (s.signalType === 'WATCH') {
        markers.push(marker(s.signalDate, MARKER_CONFIG.watch, 'belowBar', 'square'))
      } else if (s.signalType === 'BREAKOUT') {
        markers.push(marker(s.signalDate, MARKER_CONFIG.breakout, 'belowBar', 'arrowUp'))
      }
    }
  }

  // 3. Trades
  if (trades && Array.isArray(trades)) {
    for (const t of trades) {
      if (!t) continue
      if (t.status === 'not_filled') {
        if (t.signalDate && typeof t.signalDate === 'string') {
          markers.push(marker(t.signalDate, MARKER_CONFIG.notFilled, 'belowBar', 'square'))
        }
      } else {
        if (t.entryDate && typeof t.entryDate === 'string') {
          markers.push(marker(t.entryDate, MARKER_CONFIG.entry, 'belowBar', 'arrowUp'))
        }
        if (t.exitDate && typeof t.exitDate === 'string') {
          const isWin = t.rMultiple !== null && t.rMultiple > 0
          markers.push(
            marker(
              t.exitDate,
              isWin ? MARKER_CONFIG.winExit : MARKER_CONFIG.lossExit,
              'aboveBar',
              'arrowDown'
            )
          )
        }
      }
    }
  }

  // 4. Sort markers chronologically (filtering out markers without valid string times)
  const sortedMarkers = markers.filter(
    (m) => m && m.time && typeof m.time === 'string' && m.time.trim().length > 0
  )
  sortedMarkers.sort((a, b) => (a.time as string).localeCompare(b.time as string))

  // 5. Apply cool-down filter to consecutive signals of the same type to avoid cluster blocks
  const filteredMarkers: SeriesMarker<Time>[] = []
  const lastSeenMap = new Map<string, number>()

  for (const m of sortedMarkers) {
    const timeStr = m.time as string
    const timeMs = new Date(timeStr).getTime()
    if (isNaN(timeMs)) continue
    const txt = m.text || ''

    let skip = false
    if (txt === 'W') {
      const lastTime = lastSeenMap.get('W')
      // 12 calendar days cool-down for watchlist candidates inside the same base
      if (lastTime !== undefined && (timeMs - lastTime) < 12 * 24 * 60 * 60 * 1000) {
        skip = true
      } else {
        lastSeenMap.set('W', timeMs)
      }
    } else if (txt === 'B') {
      const lastTime = lastSeenMap.get('B')
      // 7 calendar days cool-down for consecutive breakout attempts
      if (lastTime !== undefined && (timeMs - lastTime) < 7 * 24 * 60 * 60 * 1000) {
        skip = true
      } else {
        lastSeenMap.set('B', timeMs)
      }
    } else if (txt === 'NF') {
      const lastTime = lastSeenMap.get('NF')
      // 7 calendar days cool-down for consecutive not filled notifications
      if (lastTime !== undefined && (timeMs - lastTime) < 7 * 24 * 60 * 60 * 1000) {
        skip = true
      } else {
        lastSeenMap.set('NF', timeMs)
      }
    }

    if (!skip) {
      filteredMarkers.push(m)
    }
  }

  // 6. Deduplicate markers on the exact same date and position
  const seen = new Map<string, SeriesMarker<Time>>()
  for (const m of filteredMarkers) {
    const key = `${m.time as string}_${m.position}`
    const existing = seen.get(key)
    if (!existing) {
      seen.set(key, m)
    } else {
      // Priority scoring: Keep the more informative marker
      const getPriority = (txt: string) => {
        if (txt === 'E') return 4
        if (txt === 'B') return 3
        if (txt === 'W') return 2
        if (txt === 'NF') return 1
        if (txt === 'X+' || txt === 'X-') return 4
        if (txt.startsWith('C')) return 3
        return 0
      }
      if (getPriority(m.text || '') > getPriority(existing.text || '')) {
        seen.set(key, m)
      }
    }
  }

  const uniqueMarkers = Array.from(seen.values())
  uniqueMarkers.sort((a, b) => (a.time as string).localeCompare(b.time as string))
  return uniqueMarkers;
}



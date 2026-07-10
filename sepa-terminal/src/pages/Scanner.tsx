import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useHotkeys } from 'react-hotkeys-hook'
import { useSignals, useWatchlist } from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import { useReviewsStore } from '@/stores/reviews'
import type { ReviewStatus, SetupRow } from '@/lib/types'
import { EmptyState, Spinner } from '@/components/ui/primitives'
import { TriageTable } from '@/components/scanner/TriageTable'
import { DetailPanel } from '@/components/scanner/DetailPanel'
import { QueueTabs, type QueueId } from '@/components/scanner/QueueTabs'

export function Scanner() {
  const scanDate = useUIStore((s) => s.scanDate)
  const { data: signals, isLoading: signalsLoading, error: signalsError } = useSignals(scanDate)
  const { data: watchlist, isLoading: watchlistLoading } = useWatchlist(scanDate)
  const reviews = useReviewsStore((s) => s.reviews)
  const setStatus = useReviewsStore((s) => s.setStatus)
  const undoLast = useReviewsStore((s) => s.undoLast)
  const navigate = useNavigate()

  const [queue, setQueue] = useState<QueueId>('breakout')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const noteRef = useRef<HTMLTextAreaElement>(null)

  const allRows = useMemo<SetupRow[]>(() => {
    if (!scanDate) return []
    const bySymbol = new Map<string, SetupRow>()

    for (const s of signals ?? []) {
      bySymbol.set(s.symbol, {
        id: `${s.symbol}:${scanDate}`,
        scanDate,
        symbol: s.symbol,
        signalType: s.signalType,
        close: s.close,
        pivot: s.pivot,
        nearPivot: s.nearPivot,
        rsRank: s.rsRank,
        volMult: s.volMult,
        entryRef: s.entryRef,
        stop: s.stop,
        rPerShare: s.rPerShare,
        range10: s.range10,
        voldry: s.voldry,
        atrPct: s.atrPct,
        reviewStatus: 'new',
        note: '',
      })
    }
    for (const w of watchlist ?? []) {
      if (bySymbol.has(w.symbol)) continue
      bySymbol.set(w.symbol, {
        id: `${w.symbol}:${scanDate}`,
        scanDate,
        symbol: w.symbol,
        signalType: null,
        close: w.close,
        pivot: w.pivot,
        nearPivot: w.nearPivot,
        rsRank: w.rsRank,
        volMult: null,
        entryRef: null,
        stop: null,
        rPerShare: null,
        range10: w.range10,
        voldry: w.voldry,
        atrPct: w.atrPct,
        reviewStatus: 'new',
        note: '',
      })
    }

    return [...bySymbol.values()].map((row) => {
      const review = reviews[row.id]
      return review ? { ...row, reviewStatus: review.status, note: review.note } : row
    })
  }, [signals, watchlist, scanDate, reviews])

  const queues = useMemo(
    () => ({
      breakout: allRows.filter((r) => r.signalType === 'BREAKOUT' && r.reviewStatus === 'new'),
      watch: allRows.filter((r) => r.signalType !== 'BREAKOUT' && r.reviewStatus === 'new'),
      shortlisted: allRows.filter(
        (r) => r.reviewStatus === 'shortlisted' || r.reviewStatus === 'watching'
      ),
      rejected: allRows.filter((r) => r.reviewStatus === 'rejected'),
    }),
    [allRows]
  )

  const rows = queues[queue]
  const selected: SetupRow | undefined = rows[Math.min(selectedIdx, rows.length - 1)]

  // Clamp selection when the queue shrinks (e.g. row triaged away)
  useEffect(() => {
    if (selectedIdx > rows.length - 1) setSelectedIdx(Math.max(0, rows.length - 1))
  }, [rows.length, selectedIdx])

  const decide = (status: ReviewStatus) => {
    if (!selected) return
    setStatus(selected.symbol, selected.scanDate, status)
  }

  useHotkeys('j, down', () => setSelectedIdx((i) => Math.min(i + 1, rows.length - 1)), [rows.length])
  useHotkeys('k, up', () => setSelectedIdx((i) => Math.max(i - 1, 0)), [rows.length])
  useHotkeys('s', () => decide('shortlisted'), [selected])
  useHotkeys('w', () => decide('watching'), [selected])
  useHotkeys('x', () => decide('rejected'), [selected])
  useHotkeys('u', () => undoLast(), [])
  useHotkeys('n', () => noteRef.current?.focus(), { preventDefault: true }, [])
  useHotkeys('enter', () => selected && navigate(`/symbol/${selected.symbol}`), [selected])
  useHotkeys('1', () => setQueue('breakout'))
  useHotkeys('2', () => setQueue('watch'))
  useHotkeys('3', () => setQueue('shortlisted'))
  useHotkeys('4', () => setQueue('rejected'))

  if (signalsLoading || watchlistLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Loading scan…" />
      </div>
    )
  }

  if (signalsError) {
    return <EmptyState title="Failed to load signals" hint={String(signalsError)} />
  }

  return (
    <div className="flex h-full flex-col">
      <QueueTabs
        active={queue}
        counts={{
          breakout: queues.breakout.length,
          watch: queues.watch.length,
          shortlisted: queues.shortlisted.length,
          rejected: queues.rejected.length,
        }}
        onChange={(q) => {
          setQueue(q)
          setSelectedIdx(0)
        }}
      />
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto border-r border-border">
          {rows.length ? (
            <TriageTable
              rows={rows}
              selectedId={selected?.id ?? null}
              onSelect={(id) => setSelectedIdx(rows.findIndex((r) => r.id === id))}
              onOpen={(symbol) => navigate(`/symbol/${symbol}`)}
            />
          ) : (
            <EmptyState
              title="Queue empty"
              hint={
                queue === 'breakout' || queue === 'watch'
                  ? 'Nothing new to triage for this scan date.'
                  : 'Triage decisions will land here.'
              }
            />
          )}
        </div>
        <div className="w-[380px] shrink-0 overflow-y-auto bg-surface">
          {selected ? (
            <DetailPanel row={selected} noteRef={noteRef} />
          ) : (
            <EmptyState title="No row selected" />
          )}
        </div>
      </div>
    </div>
  )
}

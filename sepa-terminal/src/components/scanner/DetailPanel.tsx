import { type RefObject } from 'react'
import { Link } from 'react-router'
import {
  AlertTriangle,
  ArrowRight,
  BookmarkPlus,
  CheckCircle2,
  ChevronRight,
  Eye,
  Undo2,
  X,
} from 'lucide-react'
import { useSetupExplanation } from '@/api/hooks'
import { useReviewsStore } from '@/stores/reviews'
import type { ExplanationItem, SetupRow } from '@/lib/types'
import { Badge, Spinner, StatCell } from '@/components/ui/primitives'
import { fmtPrice, fmtNum, fmtPctRaw, symbolSignalPath } from '@/lib/utils'

export function DetailPanel({
  row,
  noteRef,
  onShortlist,
  onWatch,
  onReject,
  onUndo,
}: {
  row: SetupRow
  noteRef: RefObject<HTMLTextAreaElement>
  onShortlist: () => void
  onWatch: () => void
  onReject: () => void
  onUndo: () => void
}) {
  const { data: explanation, isLoading } = useSetupExplanation(row.symbol, row.scanDate)
  const setNote = useReviewsStore((s) => s.setNote)

  const riskPct =
    row.entryRef != null && row.rPerShare != null && row.entryRef > 0
      ? (row.rPerShare / row.entryRef) * 100
      : null

  return (
    <div className="flex min-h-full flex-col">
      {/* Header */}
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-surface/95 px-4 py-3.5 backdrop-blur">
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-accent-soft text-xs font-bold text-accent">
            {row.symbol.slice(0, 2)}
          </div>
          <div>
            <span className="text-[15px] font-bold tracking-[-0.02em]">{row.symbol}</span>
            <div className="mt-0.5 text-[10px] text-ink-faint">Decision workspace</div>
          </div>
          {row.signalType ? (
            <Badge tone={row.signalType === 'BREAKOUT' ? 'up' : 'accent'}>{row.signalType}</Badge>
          ) : (
            <Badge>CANDIDATE</Badge>
          )}
        </div>
        <Link
          to={symbolSignalPath(row.symbol, row.scanDate, 'scanner')}
          className="flex items-center gap-1 text-xs font-medium text-accent hover:underline"
        >
          Deep dive <ArrowRight className="size-3" />
        </Link>
      </div>

      {/* Trade plan */}
      <div className="grid grid-cols-3 divide-x divide-border border-b border-border bg-surface-2/45">
        <StatCell label="Entry" value={fmtPrice(row.entryRef ?? row.pivot)} />
        <StatCell label="Stop" value={fmtPrice(row.stop)} tone="down" />
        <StatCell
          label="Risk / share"
          value={
            row.rPerShare != null
              ? `${fmtPrice(row.rPerShare)}${riskPct != null ? ` (${fmtNum(riskPct, 1)}%)` : ''}`
              : '—'
          }
        />
      </div>
      <div className="grid grid-cols-3 divide-x divide-border border-b border-border">
        <StatCell label="Close" value={fmtPrice(row.close)} />
        <StatCell label="Pivot" value={fmtPrice(row.pivot)} />
        <StatCell label="RS rank" value={Math.round(row.rsRank)} />
      </div>

      {/* Narrative + strengths/risks */}
      <div className="flex-1 space-y-4 px-4 py-4">
        {isLoading && <Spinner label="Loading analysis…" />}
        {explanation && (
          <>
            <p className="text-xs leading-relaxed text-ink-soft">{explanation.narrative}</p>

            {explanation.contractionSequence && explanation.contractionSequence.length > 0 && (
              <div>
                <SectionLabel>Contractions</SectionLabel>
                <div className="mt-1 flex items-center gap-1">
                  {explanation.contractionSequence.map((depth, i) => (
                    <span key={i} className="flex items-center gap-1">
                      {i > 0 && <ChevronRight className="size-3 text-ink-faint" />}
                      <Badge tone="accent">{fmtPctRaw(depth, 0)}</Badge>
                    </span>
                  ))}
                </div>
              </div>
            )}

            <ItemList label="Strengths" items={explanation.strengths} icon="check" />
            <ItemList label="Risks" items={explanation.risks} icon="warn" />
            <ItemList label="Next actions" items={explanation.nextActions} icon="arrow" />

            {explanation.metrics.length > 0 && (
              <div>
                <SectionLabel>Metrics</SectionLabel>
                <div className="mt-1 divide-y divide-border rounded-md border border-border">
                  {explanation.metrics.map((m) => (
                    <div key={m.label} className="flex items-baseline justify-between px-2.5 py-1.5">
                      <span className="text-[11px] text-ink-faint">{m.label}</span>
                      <span className="tnum text-xs font-semibold" title={m.context}>
                        {m.value}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Note */}
        <div>
          <SectionLabel>Note</SectionLabel>
          <textarea
            ref={noteRef}
            value={row.note}
            onChange={(e) => setNote(row.symbol, row.scanDate, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur()
              e.stopPropagation()
            }}
            placeholder="Why take / skip this? (esc to leave)"
            rows={3}
            className="mt-1.5 w-full resize-none rounded-lg border border-border bg-surface-2/50 px-3 py-2.5 text-xs outline-none placeholder:text-ink-faint focus:border-accent focus:bg-surface"
          />
        </div>
      </div>

      <div className="sticky bottom-0 z-10 border-t border-border bg-surface/95 p-3 backdrop-blur">
        <div className="grid grid-cols-3 gap-2">
          <ActionButton tone="up" icon={<BookmarkPlus className="size-3.5" />} onClick={onShortlist}>
            Shortlist
          </ActionButton>
          <ActionButton tone="accent" icon={<Eye className="size-3.5" />} onClick={onWatch}>
            Watch
          </ActionButton>
          <ActionButton tone="down" icon={<X className="size-3.5" />} onClick={onReject}>
            Reject
          </ActionButton>
        </div>
        <button
          type="button"
          onClick={onUndo}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md py-1 text-[10px] font-medium text-ink-faint hover:bg-surface-2 hover:text-ink-soft"
        >
          <Undo2 className="size-3" /> Undo last decision
        </button>
      </div>
    </div>
  )
}

function ActionButton({
  children,
  icon,
  tone,
  onClick,
}: {
  children: React.ReactNode
  icon: React.ReactNode
  tone: 'up' | 'accent' | 'down'
  onClick: () => void
}) {
  const tones = {
    up: 'border-up/20 bg-up-soft text-up hover:bg-up hover:text-white',
    accent: 'border-accent/20 bg-accent-soft text-accent hover:bg-accent hover:text-white',
    down: 'border-down/20 bg-down-soft text-down hover:bg-down hover:text-white',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex h-8 items-center justify-center gap-1.5 rounded-lg border text-[10px] font-semibold ${tones[tone]}`}
    >
      {icon}
      {children}
    </button>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
      {children}
    </div>
  )
}

function ItemList({
  label,
  items,
  icon,
}: {
  label: string
  items: ExplanationItem[]
  icon: 'check' | 'warn' | 'arrow'
}) {
  if (!items.length) return null
  const Icon =
    icon === 'check' ? CheckCircle2 : icon === 'warn' ? AlertTriangle : ArrowRight
  const iconColor = icon === 'check' ? 'text-up' : icon === 'warn' ? 'text-warn' : 'text-accent'
  return (
    <div>
      <SectionLabel>{label}</SectionLabel>
      <ul className="mt-1 space-y-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-xs">
            <Icon className={`mt-0.5 size-3.5 shrink-0 ${iconColor}`} />
            <span>
              <span className="font-medium">{item.text}</span>
              {item.detail && <span className="text-ink-faint"> — {item.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

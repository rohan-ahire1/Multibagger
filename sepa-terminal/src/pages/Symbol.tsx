import { useMemo } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import {
  ArrowLeft,
  CandlestickChart,
  CheckCircle2,
  Target,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import {
  useDiagnostics,
  useOHLCV,
  useSetupExplanation,
  useSignalHistory,
} from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import type { BacktestTrade, DailyDiagnostics, Signal, UnfilledSignalOutcome } from '@/lib/types'
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  PageHeader,
  Spinner,
  StatCell,
} from '@/components/ui/primitives'
import { CandleChart } from '@/components/chart/CandleChart'
import {
  cn,
  fmtDate,
  fmtDateShort,
  fmtNum,
  fmtPctRaw,
  fmtPrice,
  fmtR,
  symbolSignalPath,
  type SymbolNavigationSource,
} from '@/lib/utils'

const TT_CHECKS: { key: keyof DailyDiagnostics; label: string }[] = [
  { key: 'tt1CloseAboveSma150', label: 'Close > 150-SMA' },
  { key: 'tt2CloseAboveSma200', label: 'Close > 200-SMA' },
  { key: 'tt3Sma150AboveSma200', label: '150-SMA > 200-SMA' },
  { key: 'tt4Sma200Rising', label: '200-SMA rising' },
  { key: 'tt5Sma50AboveSma150', label: '50-SMA > 150-SMA' },
  { key: 'tt6Sma50AboveSma200', label: '50-SMA > 200-SMA' },
  { key: 'tt7CloseAboveSma50', label: 'Close > 50-SMA' },
  { key: 'tt8Above52wLow', label: '≥30% above 52w low' },
  { key: 'tt9Near52wHigh', label: 'Near 52w high' },
]

const VCP_CHECKS: { key: keyof DailyDiagnostics; label: string }[] = [
  { key: 'vcp1Range10Ok', label: '10d range tight' },
  { key: 'vcp2VoldryOk', label: 'Volume dry-up' },
  { key: 'vcp3AtrTrendOk', label: 'ATR trending down' },
  { key: 'vcp4AtrContractOk', label: 'ATR contracted' },
  { key: 'vcp5ExtensionOk', label: 'Not extended' },
  { key: 'vcp6BaseDepthOk', label: 'Base depth OK' },
]

export function SymbolPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const scanDate = useUIStore((s) => s.scanDate)
  const selectedSignalDate = searchParams.get('signalDate')
  const source = parseNavigationSource(searchParams.get('source'))
  const contextDate = selectedSignalDate ?? scanDate

  const { data: bars, isLoading: barsLoading, error: barsError } = useOHLCV(symbol ?? null)
  const { data: diag } = useDiagnostics(symbol ?? null, contextDate)
  const { data: explanation } = useSetupExplanation(symbol ?? null, contextDate)
  const { data: history } = useSignalHistory(symbol ?? null)

  const activeSignal = useMemo(
    () => {
      const exactSignal = history?.signals?.find((s) => s.signalDate === contextDate)
      return selectedSignalDate ? exactSignal : exactSignal ?? history?.signals?.[0]
    },
    [contextDate, history, selectedSignalDate]
  )
  const selectedTrade = useMemo(
    () => history?.trades?.find((trade) => trade.signalDate === selectedSignalDate),
    [history, selectedSignalDate]
  )
  const selectedUnfilledOutcome = selectedSignalDate
    ? history?.unfilledOutcomes?.[selectedSignalDate]
    : undefined
  const backTo = source === 'dashboard' ? '/' : source === 'backtest' ? '/backtest' : '/scanner'
  const backLabel = source === 'dashboard'
    ? 'Back to dashboard'
    : source === 'backtest'
      ? 'Back to backtest'
      : 'Back to setup desk'

  if (!symbol) return <EmptyState title="No symbol" />

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-6 py-5">
      {/* Header */}
      <PageHeader
        eyebrow="Security analysis"
        title={
          <span className="flex items-center gap-2.5">
            <Link
              to={backTo}
              aria-label={backLabel}
              title={backLabel}
              className="flex size-8 items-center justify-center rounded-lg border border-border bg-surface text-ink-faint shadow-sm hover:border-border-strong hover:text-ink"
            >
              <ArrowLeft className="size-3.5" />
            </Link>
            {symbol}
            {activeSignal && (
              <Badge tone={activeSignal.signalType === 'BREAKOUT' ? 'up' : 'accent'}>
                {selectedSignalDate ? 'SELECTED ' : ''}{activeSignal.signalType} · {fmtDate(activeSignal.signalDate)}
              </Badge>
            )}
          </span>
        }
        description={
          diag
            ? `Trend template ${TT_CHECKS.filter((c) => diag[c.key]).length}/9 · RS ${Math.round(diag.rsRank)} · diagnostics aligned to ${fmtDate(contextDate)}`
            : 'Full price history, setup diagnostics, and recorded signal context.'
        }
        right={
          <div className="flex size-10 items-center justify-center rounded-xl border border-accent/15 bg-accent-soft text-accent shadow-[var(--shadow-card)]">
            <CandlestickChart className="size-4.5" />
          </div>
        }
      />

      {selectedSignalDate && (activeSignal || selectedTrade) && (
        <SelectedSignalOutcome
          signalDate={selectedSignalDate}
          signal={activeSignal?.signalDate === selectedSignalDate ? activeSignal : undefined}
          trade={selectedTrade}
          unfilledOutcome={selectedUnfilledOutcome}
        />
      )}

      {/* Chart */}
      <Card className="overflow-hidden">
        {barsLoading ? (
          <div className="flex h-[460px] items-center justify-center">
            <Spinner label="Loading price history…" />
          </div>
        ) : barsError || !bars?.length ? (
          <EmptyState title="No OHLCV data" hint={barsError ? String(barsError) : undefined} />
        ) : (
          <div className="p-3">
            <CandleChart
              bars={bars}
              signals={history?.signals}
              selectedSignal={activeSignal?.signalDate === selectedSignalDate ? activeSignal : null}
              selectedSignalDate={selectedSignalDate}
              selectedTrade={selectedTrade}
              unfilledOutcome={selectedUnfilledOutcome}
              pivot={selectedTrade?.pivot ?? diag?.pivot ?? activeSignal?.pivot}
              stop={selectedTrade?.stop ?? activeSignal?.stop}
            />
            <div className="flex items-center gap-4 border-t border-border/70 px-2 pt-2 text-[10px] text-ink-faint">
              <span><span className="text-accent">━</span> 50-SMA</span>
              <span><span className="text-warn">━</span> 150-SMA</span>
              <span><span className="text-down">━</span> 200-SMA</span>
              <span className="ml-auto">
                markers: BO = breakout, W = watch{selectedSignalDate ? ', selected BO / entry / exit are labelled' : ''}
              </span>
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Diagnostics */}
        <Card className="overflow-hidden">
          <CardHeader
            title="Qualification"
            subtitle={diag ? `As of ${fmtDate(diag.diagDate)}` : undefined}
          />
          {diag ? (
            <div className="px-4 pb-3">
              <CheckGroup
                title={`Trend template · ${TT_CHECKS.filter((c) => diag[c.key]).length}/9`}
                checks={TT_CHECKS.map((c) => ({ label: c.label, pass: !!diag[c.key] }))}
              />
              <CheckGroup
                title={`VCP setup · ${VCP_CHECKS.filter((c) => diag[c.key]).length}/6`}
                checks={VCP_CHECKS.map((c) => ({ label: c.label, pass: !!diag[c.key] }))}
                className="mt-3"
              />
              <div className="mt-3 grid grid-cols-2 gap-x-4 text-xs">
                <MetaRow label="Contractions" value={`${diag.contractionCount}`} />
                <MetaRow label="Quality" value={fmtNum(diag.contractionQuality)} />
                <MetaRow label="Pivot" value={fmtPrice(diag.pivot)} />
                <MetaRow label="To pivot" value={fmtPctRaw(-diag.nearPivot * 100, 1)} />
                <MetaRow label="Setup streak" value={`${diag.setupActiveStreak}d`} />
                <MetaRow label="Pivot age" value={`${diag.pivotAgeDays}d`} />
              </div>
            </div>
          ) : (
            <div className="px-4 pb-4">
              <EmptyState
                title="No diagnostics"
                hint={`Nothing recorded for ${symbol} on ${contextDate ?? '—'}.`}
              />
            </div>
          )}
        </Card>

        {/* Setup narrative */}
        <Card className="overflow-hidden">
          <CardHeader title="Setup read" />
          <div className="space-y-3 px-4 pb-4">
            {explanation ? (
              <>
                <p className="text-xs leading-relaxed text-ink-soft">{explanation.narrative}</p>
                {explanation.metrics.length > 0 && (
                  <div className="divide-y divide-border rounded-md border border-border">
                    {explanation.metrics.map((m) => (
                      <div key={m.label} className="flex items-baseline justify-between px-2.5 py-1.5">
                        <span className="text-[11px] text-ink-faint">{m.label}</span>
                        <span className="tnum text-xs font-semibold" title={m.context}>
                          {m.value}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <EmptyState title="No setup analysis" hint="Only available on scan dates with diagnostics." />
            )}
          </div>
        </Card>

        {/* Signal history */}
        <Card className="overflow-hidden">
          <CardHeader
            title="Signal history"
            subtitle={history ? `${history.signals.length} signals` : undefined}
          />
          <div className="max-h-[380px] overflow-y-auto">
            {history?.signals.length ? (
              <table className="w-full text-xs">
                <tbody>
                  {history.signals.map((s) => (
                    <tr
                      key={`${s.signalDate}-${s.signalType}`}
                      role="link"
                      tabIndex={0}
                      onClick={() => navigate(symbolSignalPath(symbol, s.signalDate, source ?? 'history'))}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          navigate(symbolSignalPath(symbol, s.signalDate, source ?? 'history'))
                        }
                      }}
                      className={cn(
                        'cursor-pointer border-t border-border/80 outline-none hover:bg-surface-2 focus-visible:bg-accent-soft focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
                        s.signalDate === selectedSignalDate
                          && 'bg-accent-soft shadow-[inset_3px_0_0_var(--color-accent)]'
                      )}
                    >
                      <td className="tnum px-4 py-2">
                        <Link
                          to={symbolSignalPath(symbol, s.signalDate, source ?? 'history')}
                          onClick={(event) => event.stopPropagation()}
                          className="font-semibold hover:text-accent"
                        >
                          {fmtDate(s.signalDate)}
                        </Link>
                      </td>
                      <td className="py-2">
                        <Badge tone={s.signalType === 'BREAKOUT' ? 'up' : 'accent'}>
                          {s.signalType}
                        </Badge>
                      </td>
                      <td className="tnum px-4 py-1.5 text-right text-ink-soft">
                        {fmtPrice(s.close)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState title="No signal history" />
            )}
          </div>
          {history && history.trades.length > 0 && (
            <>
              <CardHeader title="Backtest trades" className="border-t border-border" />
              <div className="grid grid-cols-3 divide-x divide-border border-t border-border">
                <StatCell label="Trades" value={history.trades.length} />
                <StatCell
                  label="Avg R"
                  value={fmtR(avg(history.trades.map((t) => t.rMultiple)))}
                  tone={(avg(history.trades.map((t) => t.rMultiple)) ?? 0) >= 0 ? 'up' : 'down'}
                />
                <StatCell
                  label="Win rate"
                  value={fmtPctRaw(winRate(history.trades.map((t) => t.rMultiple)), 0)}
                />
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}

function SelectedSignalOutcome({
  signalDate,
  signal,
  trade,
  unfilledOutcome,
}: {
  signalDate: string
  signal?: Signal
  trade?: BacktestTrade
  unfilledOutcome?: UnfilledSignalOutcome
}) {
  const isHypothetical = trade?.status === 'not_filled' && unfilledOutcome != null
  const notInCachedBacktest = trade == null
  const returnPct = trade?.returnPct ?? unfilledOutcome?.returnPct ?? null
  const rMultiple = trade?.rMultiple ?? unfilledOutcome?.rMultiple ?? null
  const isWin = returnPct != null && returnPct > 0
  const isLoss = returnPct != null && returnPct < 0
  const Icon = isWin ? TrendingUp : isLoss ? TrendingDown : Target
  const outcomeLabel = trade
    ? trade.entryDate
      ? trade.exitDate
        ? isWin ? 'WIN' : isLoss ? 'LOSS' : 'FLAT'
        : 'OPEN / NO EXIT'
      : trade.status.replace(/_/g, ' ').toUpperCase()
    : signal?.signalType === 'WATCH'
      ? 'WATCH — NOT TRADED'
      : 'NO MATCHING BACKTEST TRADE'

  return (
    <Card
      className={cn(
        'overflow-hidden border-2',
        isWin && 'border-up/45 bg-up-soft/25',
        isLoss && 'border-down/45 bg-down-soft/25',
        !isWin && !isLoss && (notInCachedBacktest ? 'border-warn/40 bg-warn-soft/20' : 'border-accent/35 bg-accent-soft/20')
      )}
    >
      <div className="flex items-center gap-3 border-b border-current/10 px-4 py-3">
        <div
          className={cn(
            'flex size-9 items-center justify-center rounded-lg',
            isWin && 'bg-up text-white',
            isLoss && 'bg-down text-white',
            !isWin && !isLoss && 'bg-accent text-white'
          )}
        >
          <Icon className="size-4.5" />
        </div>
        <div>
          <div className="text-[13px] font-bold tracking-[-0.01em]">Selected breakout outcome</div>
          <div className="mt-0.5 text-[11px] text-ink-soft">
            This chart is pinned to the signal from {fmtDate(signalDate)}.
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {notInCachedBacktest ? (
            <Badge tone="warn" className="px-2.5 py-1">NOT IN THIS BACKTEST</Badge>
          ) : isHypothetical ? (
            <Badge tone={isWin ? 'up' : 'down'} className="px-2.5 py-1">
              HYPOTHETICAL {isWin ? 'PROFIT' : 'LOSS'}
            </Badge>
          ) : null}
          {!notInCachedBacktest && (
            <Badge tone={isHypothetical ? 'warn' : isWin ? 'up' : isLoss ? 'down' : 'accent'} className="px-2.5 py-1">
              {isHypothetical ? 'NOT FILLED' : outcomeLabel}
            </Badge>
          )}
        </div>
      </div>

      {notInCachedBacktest ? (
        <div className="grid grid-cols-1 divide-y divide-border/80 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          <StatCell label="Breakout" value={fmtDateShort(signalDate)} />
          <StatCell label="Backtest status" value="Not included" />
          <StatCell label="Outcome" value="Not calculated" />
        </div>
      ) : (
        <div className="grid grid-cols-3 divide-x divide-border/80 lg:grid-cols-6">
          <StatCell label="Breakout" value={fmtDateShort(signalDate)} />
          <StatCell
            label={isHypothetical ? 'Entry trigger' : 'Entry'}
            value={isHypothetical
              ? fmtPrice(unfilledOutcome?.entryRef)
              : <PriceAndDate price={trade?.entryPrice} date={trade?.entryDate} />}
          />
          <StatCell
            label={isHypothetical ? 'Window end' : 'Exit'}
            value={isHypothetical
              ? <PriceAndDate price={unfilledOutcome?.endPrice} date={unfilledOutcome?.endDate} />
              : <PriceAndDate price={trade?.exitPrice} date={trade?.exitDate} />}
          />
          <StatCell
            label={isHypothetical ? 'Signal window' : 'Holding'}
            value={isHypothetical
              ? `${unfilledOutcome?.holdingBars ?? 0} bars`
              : trade?.holdingDays != null ? `${trade.holdingDays} days` : '—'}
          />
          <StatCell
            label={isHypothetical ? 'Hypothetical return' : 'Return'}
            value={fmtPctRaw(returnPct)}
            tone={isWin ? 'up' : isLoss ? 'down' : 'neutral'}
          />
          <StatCell
            label={isHypothetical ? 'Hypothetical R' : 'R-multiple'}
            value={fmtR(rMultiple)}
            tone={(rMultiple ?? 0) > 0 ? 'up' : (rMultiple ?? 0) < 0 ? 'down' : 'neutral'}
          />
        </div>
      )}

      {(trade || isHypothetical || notInCachedBacktest) && (
        <div className="border-t border-border/80 px-4 py-2 text-[11px] text-ink-soft">
          {notInCachedBacktest ? (
            <>
              This raw breakout has no matching record in the currently cached backtest. It may have been excluded before simulation; no entry, exit, or hypothetical P&amp;L is inferred.
            </>
          ) : isHypothetical ? (
            <>
              <span className="font-bold text-warn">Not filled:</span> hypothetical move from the price-basis-aligned entry trigger to the backend pending-window expiry; no realised P&amp;L.
            </>
          ) : trade ? (
            <>
              Status: <span className="font-semibold text-ink">{trade.status.replace(/_/g, ' ')}</span>
              {(trade.exitReason ?? trade.exitPhase) && (
                <> · Exit reason: <span className="font-semibold text-ink">{trade.exitReason ?? trade.exitPhase}</span></>
              )}
            </>
          ) : null}
        </div>
      )}
    </Card>
  )
}

function PriceAndDate({
  price,
  date,
}: {
  price: number | null | undefined
  date: string | null | undefined
}) {
  if (price == null && !date) return <>—</>
  return (
    <span>
      {fmtPrice(price)}
      {date && <span className="ml-1 text-[10px] font-normal text-ink-faint">{fmtDateShort(date)}</span>}
    </span>
  )
}

function parseNavigationSource(value: string | null): SymbolNavigationSource | null {
  return value === 'dashboard' || value === 'scanner' || value === 'backtest' || value === 'history'
    ? value
    : null
}

function avg(values: (number | null)[]): number | null {
  const nums = values.filter((v): v is number => v != null)
  if (!nums.length) return null
  return nums.reduce((a, b) => a + b, 0) / nums.length
}

function winRate(values: (number | null)[]): number {
  const nums = values.filter((v): v is number => v != null)
  if (!nums.length) return 0
  return (nums.filter((v) => v > 0).length / nums.length) * 100
}

function CheckGroup({
  title,
  checks,
  className,
}: {
  title: string
  checks: { label: string; pass: boolean }[]
  className?: string
}) {
  return (
    <div className={className}>
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        {title}
      </div>
      <div className="grid grid-cols-1 gap-y-1">
        {checks.map((c) => (
          <div key={c.label} className="flex items-center gap-1.5 text-xs">
            {c.pass ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-up" />
            ) : (
              <XCircle className="size-3.5 shrink-0 text-down" />
            )}
            <span className={cn(!c.pass && 'text-ink-faint')}>{c.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between border-t border-border py-1">
      <span className="text-[11px] text-ink-faint">{label}</span>
      <span className="tnum font-semibold">{value}</span>
    </div>
  )
}

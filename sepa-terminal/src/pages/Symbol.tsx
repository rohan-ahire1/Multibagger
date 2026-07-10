import { useMemo } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, CheckCircle2, XCircle } from 'lucide-react'
import {
  useDiagnostics,
  useOHLCV,
  useSetupExplanation,
  useSignalHistory,
} from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import type { DailyDiagnostics } from '@/lib/types'
import { Badge, Card, CardHeader, EmptyState, Spinner, StatCell } from '@/components/ui/primitives'
import { CandleChart } from '@/components/chart/CandleChart'
import { cn, fmtDate, fmtNum, fmtPctRaw, fmtPrice, fmtR } from '@/lib/utils'

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
  const scanDate = useUIStore((s) => s.scanDate)

  const { data: bars, isLoading: barsLoading, error: barsError } = useOHLCV(symbol ?? null)
  const { data: diag } = useDiagnostics(symbol ?? null, scanDate)
  const { data: explanation } = useSetupExplanation(symbol ?? null, scanDate)
  const { data: history } = useSignalHistory(symbol ?? null)

  const latestSignal = useMemo(
    () => history?.signals?.find((s) => s.signalDate === scanDate) ?? history?.signals?.[0],
    [history, scanDate]
  )

  if (!symbol) return <EmptyState title="No symbol" />

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link to="/scanner" className="text-ink-faint hover:text-ink">
          <ArrowLeft className="size-4" />
        </Link>
        <h1 className="text-lg font-bold">{symbol}</h1>
        {latestSignal && (
          <Badge tone={latestSignal.signalType === 'BREAKOUT' ? 'up' : 'accent'}>
            {latestSignal.signalType} · {fmtDate(latestSignal.signalDate)}
          </Badge>
        )}
        {diag && (
          <span className="text-xs text-ink-faint">
            Trend template {TT_CHECKS.filter((c) => diag[c.key]).length}/9 · RS{' '}
            {Math.round(diag.rsRank)} · diagnostics for {fmtDate(scanDate)}
          </span>
        )}
      </div>

      {/* Chart */}
      <Card>
        {barsLoading ? (
          <div className="flex h-[460px] items-center justify-center">
            <Spinner label="Loading price history…" />
          </div>
        ) : barsError || !bars?.length ? (
          <EmptyState title="No OHLCV data" hint={barsError ? String(barsError) : undefined} />
        ) : (
          <div className="p-2">
            <CandleChart
              bars={bars}
              signals={history?.signals}
              pivot={diag?.pivot ?? latestSignal?.pivot}
              stop={latestSignal?.stop}
            />
            <div className="flex items-center gap-4 px-2 pt-1 text-[11px] text-ink-faint">
              <span><span className="text-[#2557d6]">━</span> 50-SMA</span>
              <span><span className="text-[#b0700a]">━</span> 150-SMA</span>
              <span><span className="text-[#c23934]">━</span> 200-SMA</span>
              <span className="ml-auto">markers: BO = breakout signal, W = watch signal</span>
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-3 gap-4">
        {/* Diagnostics */}
        <Card>
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
                hint={`Nothing recorded for ${symbol} on ${scanDate ?? '—'}.`}
              />
            </div>
          )}
        </Card>

        {/* Setup narrative */}
        <Card>
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
        <Card>
          <CardHeader
            title="Signal history"
            subtitle={history ? `${history.signals.length} signals` : undefined}
          />
          <div className="max-h-[380px] overflow-y-auto">
            {history?.signals.length ? (
              <table className="w-full text-xs">
                <tbody>
                  {history.signals.map((s) => (
                    <tr key={`${s.signalDate}-${s.signalType}`} className="border-t border-border">
                      <td className="tnum px-4 py-1.5">{fmtDate(s.signalDate)}</td>
                      <td className="py-1.5">
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

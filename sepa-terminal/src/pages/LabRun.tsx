import { useMemo } from 'react'
import { Link, useParams } from 'react-router'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ArrowLeft } from 'lucide-react'
import { useLabRun, useLabRunStatus } from '@/api/lab'
import type { LabTrade } from '@/lib/lab-types'
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
  StatCell,
} from '@/components/ui/primitives'
import { fmtDateShort } from '@/lib/utils'

export function LabRun() {
  const { runId } = useParams<{ runId: string }>()
  const { data, isLoading, error, refetch } = useLabRun(runId)

  const unfinished = data?.run.status === 'queued' || data?.run.status === 'running'
  const status = useLabRunStatus(runId, unfinished ?? true)

  // The detail query only refetches when the poll reports the run has landed.
  const polledStatus = status.data?.status
  useMemo(() => {
    if (polledStatus === 'done' || polledStatus === 'failed') refetch()
  }, [polledStatus, refetch])

  if (isLoading) return <Spinner label="Loading run" />
  if (error) return <ErrorState error={error} />
  if (!data) return <EmptyState title="Run not found" />

  const { run, equityCurve, trades } = data
  const m = run.metrics
  // Only rebalance runs lack per-trade R. Reversion runs have stops and
  // R-multiples like signal runs; only their exit rule differs.
  const isRebalance = run.kind === 'rebalance'

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-6 py-5">
      <Link
        to="/lab"
        className="inline-flex items-center gap-1 text-[11px] text-ink-faint hover:text-ink"
      >
        <ArrowLeft className="size-3" /> Back to lab
      </Link>

      <PageHeader
        eyebrow={run.strategyId}
        title={run.label || `Run ${run.runId.slice(0, 8)}`}
        description={`${fmtDateShort(run.startDate)} – ${fmtDateShort(run.endDate)} · ${run.dataSource}`}
      />

      {run.status === 'failed' && (
        <Card className="border-danger/30 bg-danger/5 p-4">
          <div className="text-xs font-semibold text-danger">Run failed</div>
          <pre className="mt-1.5 whitespace-pre-wrap text-[11px] text-danger">{run.error}</pre>
        </Card>
      )}

      {(run.status === 'queued' || run.status === 'running') && (
        <Card className="p-4">
          <Spinner
            label={`${status.data?.stage ?? run.stage ?? run.status} · ${Math.round(
              status.data?.progressPct ?? run.progressPct ?? 0
            )}%`}
          />
        </Card>
      )}

      {run.warnings?.map((w) => (
        <div
          key={w}
          className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-warning"
        >
          {w}
        </div>
      ))}

      {m && (
        <div className="grid grid-cols-4 gap-3">
          <StatCell label="CAGR" value={`${m.cagrPct.toFixed(2)}%`} />
          <StatCell label="Max drawdown" value={`${m.maxDrawdownPct.toFixed(2)}%`} />
          <StatCell label="Calmar" value={m.calmar?.toFixed(2) ?? '—'} />
          <StatCell label="Sharpe" value={m.sharpe?.toFixed(2) ?? '—'} />
          <StatCell label="Total return" value={`${m.totalReturnPct.toFixed(1)}%`} />
          <StatCell label="Volatility" value={`${m.volatilityPct.toFixed(1)}%`} />
          <StatCell
            label="Benchmark CAGR"
            value={m.benchmarkCagrPct === null ? '—' : `${m.benchmarkCagrPct.toFixed(2)}%`}
          />
          <StatCell
            label="Excess CAGR"
            value={m.excessCagrPct === null ? '—' : `${m.excessCagrPct.toFixed(2)} pts`}
          />
          {/* Trade statistics are meaningless for a target-weight strategy. */}
          {!isRebalance && (
            <>
              <StatCell label="Trades" value={m.trades ?? '—'} />
              <StatCell
                label="Win rate"
                value={m.winRate === null ? '—' : `${m.winRate.toFixed(1)}%`}
              />
              <StatCell label="Avg R" value={m.avgR?.toFixed(2) ?? '—'} />
              <StatCell label="Payoff" value={m.payoffRatio?.toFixed(2) ?? '—'} />
            </>
          )}
        </div>
      )}

      {equityCurve.length > 0 && (
        <Card className="p-4">
          <div className="mb-2 text-[11px] font-semibold text-ink">Equity</div>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityCurve}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} width={64} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="#2563eb"
                  fill="#2563eb"
                  fillOpacity={0.12}
                  strokeWidth={1.5}
                />
                {equityCurve.some((p) => p.benchmarkEquity !== null) && (
                  <Area
                    type="monotone"
                    dataKey="benchmarkEquity"
                    stroke="#94a3b8"
                    fill="none"
                    strokeDasharray="4 3"
                    strokeWidth={1.2}
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {trades.length > 0 && <TradesTable trades={trades} isRebalance={isRebalance} />}

      {run.params && (
        <Card className="p-4">
          <div className="mb-2 text-[11px] font-semibold text-ink">Parameters</div>
          <div className="grid grid-cols-4 gap-x-4 gap-y-1 text-[11px]">
            {Object.entries(run.params).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span className="text-ink-faint">{k}</span>
                <span className="tnum text-ink">{String(v)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function TradesTable({ trades, isRebalance }: { trades: LabTrade[]; isRebalance: boolean }) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-border bg-surface-2/50 px-3 py-2 text-[11px] font-semibold text-ink">
        {isRebalance ? 'Orders' : 'Trades'} ({trades.length})
      </div>
      <div className="max-h-[520px] overflow-auto">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 border-b border-border bg-surface text-[10px] uppercase text-ink-faint">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">Symbol</th>
              <th className="px-2 py-1.5 text-left font-medium">Entry</th>
              <th className="px-2 py-1.5 text-left font-medium">Exit</th>
              <th className="px-2 py-1.5 text-right font-medium">Shares</th>
              {!isRebalance && <th className="px-2 py-1.5 text-right font-medium">R</th>}
              <th className="px-2 py-1.5 text-right font-medium">P&amp;L %</th>
              <th className="px-2 py-1.5 text-left font-medium">Reason</th>
              <th className="px-2 py-1.5 text-left font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={`${t.symbol}-${t.signalDate}-${i}`} className="border-b border-border/50">
                <td className="px-2 py-1.5 font-medium text-ink">{t.symbol}</td>
                <td className="tnum px-2 py-1.5 text-ink-faint">
                  {t.entryDate ? fmtDateShort(t.entryDate) : '—'}
                </td>
                <td className="tnum px-2 py-1.5 text-ink-faint">
                  {t.exitDate ? fmtDateShort(t.exitDate) : '—'}
                </td>
                <td className="tnum px-2 py-1.5 text-right">{t.shares?.toFixed(0) ?? '—'}</td>
                {!isRebalance && (
                  <td
                    className={`tnum px-2 py-1.5 text-right ${
                      (t.rMultiple ?? 0) > 0 ? 'text-success' : 'text-danger'
                    }`}
                  >
                    {t.rMultiple?.toFixed(2) ?? '—'}
                  </td>
                )}
                <td className="tnum px-2 py-1.5 text-right">
                  {t.pnlPct === null ? '—' : `${t.pnlPct.toFixed(1)}%`}
                </td>
                <td className="px-2 py-1.5 text-ink-faint">{t.exitReason ?? '—'}</td>
                <td className="px-2 py-1.5 text-ink-faint">{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

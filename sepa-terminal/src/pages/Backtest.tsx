import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Play } from 'lucide-react'
import { useLastBacktest, useRunBacktest } from '@/api/hooks'
import type { BacktestReport, TradeStatus } from '@/lib/types'
import { Badge, Card, CardHeader, EmptyState, ErrorState, Spinner, StatCell } from '@/components/ui/primitives'
import { cn, fmtDate, fmtDateShort, fmtNum, fmtPctRaw, fmtR } from '@/lib/utils'

export function Backtest() {
  const { data: last, isLoading: lastLoading } = useLastBacktest()
  const run = useRunBacktest()

  const [startDate, setStartDate] = useState('2023-01-01')
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [exitEngine, setExitEngine] = useState<'minervini_phased' | 'advanced_sepa'>('minervini_phased')
  const [targetR, setTargetR] = useState('')

  const report = run.data ?? last

  const onRun = () => {
    run.mutate({
      startDate,
      endDate,
      exitEngine,
      targetR: targetR ? Number(targetR) : null,
      pendingDays: null, // config.yaml defaults (single source of truth)
      maxHoldDays: null,
      usePortfolio: true,
    })
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      {/* Run form */}
      <Card className="flex flex-wrap items-end gap-3 p-4">
        <Field label="Start">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="tnum h-8 rounded-md border border-border bg-surface px-2 text-xs outline-none focus:border-accent"
          />
        </Field>
        <Field label="End">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="tnum h-8 rounded-md border border-border bg-surface px-2 text-xs outline-none focus:border-accent"
          />
        </Field>
        <Field label="Exit engine">
          <select
            value={exitEngine}
            onChange={(e) => setExitEngine(e.target.value as typeof exitEngine)}
            className="h-8 rounded-md border border-border bg-surface px-2 text-xs outline-none focus:border-accent"
          >
            <option value="minervini_phased">Minervini phased</option>
            <option value="advanced_sepa">Advanced SEPA</option>
          </select>
        </Field>
        <Field label="Target R (blank = none)">
          <input
            type="number"
            step="0.5"
            min="0"
            value={targetR}
            onChange={(e) => setTargetR(e.target.value)}
            placeholder="—"
            className="tnum h-8 w-24 rounded-md border border-border bg-surface px-2 text-xs outline-none focus:border-accent"
          />
        </Field>
        <button
          onClick={onRun}
          disabled={run.isPending}
          className="flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-xs font-semibold text-white hover:bg-accent/90 disabled:opacity-50"
        >
          <Play className="size-3" />
          {run.isPending ? 'Running…' : 'Run backtest'}
        </button>
        <span className="text-[11px] text-ink-faint">
          Sizing/exits come from config.yaml. R-based metrics — for CAGR truth use{' '}
          <code className="rounded bg-surface-2 px-1">optimize_joint_wf.py --parity</code>.
        </span>
      </Card>

      {run.isError && <ErrorState error={run.error} />}

      {run.isPending ? (
        <Card className="flex items-center justify-center py-16">
          <Spinner label="Simulating portfolio… this can take a minute" />
        </Card>
      ) : report ? (
        <ReportView report={report} />
      ) : lastLoading ? (
        <Card className="flex items-center justify-center py-16">
          <Spinner />
        </Card>
      ) : (
        <EmptyState title="No backtest yet" hint="Run one above — the last result is cached." />
      )}
    </div>
  )
}

function ReportView({ report }: { report: BacktestReport }) {
  const { summary, equityCurve, trades } = report
  const [statusFilter, setStatusFilter] = useState<'filled' | 'all'>('filled')

  const visibleTrades = useMemo(() => {
    const rows = statusFilter === 'filled' ? trades.filter((t) => t.entryDate != null) : trades
    return [...rows].sort((a, b) => (b.exitDate ?? b.signalDate).localeCompare(a.exitDate ?? a.signalDate))
  }, [trades, statusFilter])

  return (
    <>
      {/* Summary */}
      <Card>
        <div className="grid grid-cols-6 divide-x divide-border">
          <StatCell
            label="Cumulative R"
            value={fmtR(summary.cumulativeR, 1)}
            tone={summary.cumulativeR >= 0 ? 'up' : 'down'}
          />
          <StatCell label="Expectancy" value={fmtR(summary.expectancy)} tone={(summary.expectancy ?? 0) >= 0 ? 'up' : 'down'} />
          <StatCell label="Win rate" value={fmtPctRaw(summary.winRate * 100, 0)} />
          <StatCell label="Payoff" value={fmtNum(summary.payoffRatio)} />
          <StatCell label="Max DD (R)" value={fmtNum(summary.maxDrawdown, 1)} tone="down" />
          <StatCell label="Trades" value={`${summary.filledTrades} / ${summary.totalSignals}`} />
        </div>
        <div className="grid grid-cols-6 divide-x divide-border border-t border-border">
          <StatCell label="Avg R" value={fmtR(summary.averageR)} />
          <StatCell label="Median R" value={fmtR(summary.medianR)} />
          <StatCell label="Avg MFE" value={fmtR(summary.avgMfeR)} />
          <StatCell label="Avg MAE" value={fmtR(summary.avgMaeR)} />
          <StatCell label="Losing streak" value={summary.longestLosingStreak} />
          <StatCell label="Exit engine" value={summary.exitEngine || summary.exitMode} />
        </div>
      </Card>

      {/* Equity curve */}
      <Card>
        <CardHeader title="Equity curve" subtitle="Cumulative R by exit date, with drawdown" />
        <div className="h-64 px-2 pb-2">
          <ResponsiveContainer>
            <AreaChart data={equityCurve} margin={{ top: 4, right: 8, left: -14 }}>
              <CartesianGrid stroke="var(--color-border)" vertical={false} />
              <XAxis
                dataKey="exitDate"
                tickFormatter={fmtDateShort}
                tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                tickLine={false}
                axisLine={{ stroke: 'var(--color-border)' }}
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                labelFormatter={(l) => fmtDate(String(l))}
                formatter={(v, name) => [fmtNum(Number(v), 1), name === 'cumulativeR' ? 'Cumulative R' : 'Drawdown']}
                contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: 'var(--color-border)' }}
              />
              <Area
                dataKey="cumulativeR"
                type="monotone"
                stroke="var(--color-accent)"
                strokeWidth={1.5}
                fill="var(--color-accent-soft)"
              />
              <Area
                dataKey="drawdown"
                type="monotone"
                stroke="var(--color-down)"
                strokeWidth={1}
                fill="var(--color-down-soft)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>

      {/* Trades */}
      <Card>
        <CardHeader
          title={`Trades · ${visibleTrades.length}`}
          right={
            <div className="flex gap-1">
              {(['filled', 'all'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  className={cn(
                    'rounded-md px-2 py-0.5 text-[11px] font-medium text-ink-soft hover:bg-surface-2',
                    statusFilter === f && 'bg-accent-soft text-accent'
                  )}
                >
                  {f === 'filled' ? 'Filled' : 'All'}
                </button>
              ))}
            </div>
          }
        />
        <div className="max-h-[420px] overflow-y-auto border-t border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-surface-2 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
              <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:text-right [&>th]:font-semibold">
                <th className="!pl-4 !text-left">Symbol</th>
                <th className="!text-left">Status</th>
                <th>Signal</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Days</th>
                <th>Return</th>
                <th>R</th>
                <th className="!pr-4 !text-left">Exit reason</th>
              </tr>
            </thead>
            <tbody>
              {visibleTrades.slice(0, 500).map((t, i) => (
                <tr key={`${t.symbol}-${t.signalDate}-${i}`} className="border-t border-border hover:bg-surface-2">
                  <td className="px-2 py-1.5 pl-4">
                    <Link to={`/symbol/${t.symbol}`} className="font-semibold text-accent hover:underline">
                      {t.symbol}
                    </Link>
                  </td>
                  <td className="px-2 py-1.5">
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="tnum px-2 py-1.5 text-right text-ink-soft">{fmtDateShort(t.signalDate)}</td>
                  <td className="tnum px-2 py-1.5 text-right text-ink-soft">{fmtDateShort(t.entryDate)}</td>
                  <td className="tnum px-2 py-1.5 text-right text-ink-soft">{fmtDateShort(t.exitDate)}</td>
                  <td className="tnum px-2 py-1.5 text-right">{t.holdingDays ?? '—'}</td>
                  <td
                    className={cn(
                      'tnum px-2 py-1.5 text-right',
                      (t.returnPct ?? 0) > 0 ? 'text-up' : (t.returnPct ?? 0) < 0 ? 'text-down' : ''
                    )}
                  >
                    {t.returnPct != null ? fmtPctRaw(t.returnPct * 100) : '—'}
                  </td>
                  <td
                    className={cn(
                      'tnum px-2 py-1.5 text-right font-semibold',
                      (t.rMultiple ?? 0) > 0 ? 'text-up' : (t.rMultiple ?? 0) < 0 ? 'text-down' : ''
                    )}
                  >
                    {fmtR(t.rMultiple)}
                  </td>
                  <td className="max-w-[160px] truncate px-2 py-1.5 pr-4 text-ink-faint">
                    {t.exitReason ?? t.exitPhase ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}

const statusTones: Partial<Record<TradeStatus, 'up' | 'down' | 'warn' | 'accent' | 'neutral'>> = {
  target: 'up',
  stopped: 'down',
  time_stop: 'warn',
  dead_money: 'warn',
  not_filled: 'neutral',
}

function StatusBadge({ status }: { status: TradeStatus }) {
  return <Badge tone={statusTones[status] ?? 'neutral'}>{status.replace(/_/g, ' ')}</Badge>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        {label}
      </span>
      {children}
    </label>
  )
}

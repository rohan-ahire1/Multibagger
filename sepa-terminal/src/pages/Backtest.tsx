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
import { ArrowDown, ArrowUp, ArrowUpDown, FlaskConical, Play, Settings2 } from 'lucide-react'
import { useLastBacktest, useRunBacktest } from '@/api/hooks'
import type { BacktestReport, BacktestTrade, TradeStatus } from '@/lib/types'
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
  StatCell,
} from '@/components/ui/primitives'
import { cn, fmtDate, fmtDateShort, fmtNum, fmtPctRaw, fmtR, symbolSignalPath } from '@/lib/utils'

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
    <div className="mx-auto max-w-7xl space-y-5 px-6 py-5">
      <PageHeader
        eyebrow="Strategy research"
        title="Backtest laboratory"
        description="Run the configured portfolio simulation, inspect risk-adjusted R metrics, and review every resulting trade."
        right={
          <div className="flex size-10 items-center justify-center rounded-xl border border-accent/15 bg-accent-soft text-accent shadow-[var(--shadow-card)]">
            <FlaskConical className="size-4.5" />
          </div>
        }
      />

      {/* Run form */}
      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border bg-surface-2/50 px-4 py-3">
          <Settings2 className="size-3.5 text-accent" />
          <span className="text-[11px] font-semibold text-ink">Simulation parameters</span>
          <span className="ml-auto text-[10px] text-ink-faint">Sizing and exits inherit config.yaml</span>
        </div>
        <div className="flex flex-wrap items-end gap-3 p-4">
        <Field label="Start date">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="tnum h-9 rounded-lg border border-border bg-surface-2/40 px-2.5 text-xs outline-none hover:border-border-strong focus:border-accent focus:bg-surface"
          />
        </Field>
        <Field label="End date">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="tnum h-9 rounded-lg border border-border bg-surface-2/40 px-2.5 text-xs outline-none hover:border-border-strong focus:border-accent focus:bg-surface"
          />
        </Field>
        <Field label="Exit engine">
          <select
            value={exitEngine}
            onChange={(e) => setExitEngine(e.target.value as typeof exitEngine)}
            className="h-9 rounded-lg border border-border bg-surface-2/40 px-2.5 text-xs outline-none hover:border-border-strong focus:border-accent focus:bg-surface"
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
            className="tnum h-9 w-24 rounded-lg border border-border bg-surface-2/40 px-2.5 text-xs outline-none hover:border-border-strong focus:border-accent focus:bg-surface"
          />
        </Field>
        <button
          onClick={onRun}
          disabled={run.isPending}
          className="flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-[11px] font-semibold text-white shadow-[0_6px_16px_color-mix(in_srgb,var(--color-accent)_20%,transparent)] hover:-translate-y-px hover:brightness-105 disabled:translate-y-0 disabled:opacity-50"
        >
          <Play className="size-3" />
          {run.isPending ? 'Running…' : 'Run backtest'}
        </button>
        <span className="ml-auto max-w-md text-right text-[10px] leading-relaxed text-ink-faint">
          Sizing/exits come from config.yaml. R-based metrics — for CAGR truth use{' '}
          <code className="rounded bg-surface-2 px-1">optimize_joint_wf.py --parity</code>.
        </span>
        </div>
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
  const [statusFilter, setStatusFilter] = useState<TradeStatus | 'filled' | 'all'>('filled')
  const [exitReasonFilter, setExitReasonFilter] = useState('all')
  const [sort, setSort] = useState<TradeSort | null>(null)

  const statusOptions = useMemo(
    () => [...new Set(trades.map((trade) => trade.status))].sort((a, b) => a.localeCompare(b)),
    [trades]
  )

  const exitReasonOptions = useMemo(
    () =>
      [...new Set(trades.map(getTradeExitReason).filter((reason): reason is string => reason != null))]
        .sort((a, b) => a.localeCompare(b)),
    [trades]
  )

  const visibleTrades = useMemo(() => {
    const rows = trades.filter((trade) => {
      const matchesStatus =
        statusFilter === 'all' ||
        (statusFilter === 'filled' ? trade.entryDate != null : trade.status === statusFilter)
      const matchesExitReason =
        exitReasonFilter === 'all' || getTradeExitReason(trade) === exitReasonFilter

      return matchesStatus && matchesExitReason
    })

    return rows.sort((a, b) => {
      if (!sort) return compareTradeRecency(a, b)

      const comparison = compareSortValues(
        getTradeSortValue(a, sort.key),
        getTradeSortValue(b, sort.key),
        sort.direction
      )
      return comparison || compareTradeRecency(a, b)
    })
  }, [exitReasonFilter, sort, statusFilter, trades])

  const toggleSort = (key: TradeSortKey) => {
    setSort((current) => {
      if (current?.key === key) {
        return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
      }

      return { key, direction: key === 'status' ? 'asc' : 'desc' }
    })
  }

  const filtersActive = statusFilter !== 'all' || exitReasonFilter !== 'all'

  return (
    <>
      {/* Summary */}
      <Card className="overflow-hidden">
        <div className="grid grid-cols-3 divide-x divide-border lg:grid-cols-6">
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
        <div className="grid grid-cols-3 divide-x divide-border border-t border-border bg-surface-2/35 lg:grid-cols-6">
          <StatCell label="Avg R" value={fmtR(summary.averageR)} />
          <StatCell label="Median R" value={fmtR(summary.medianR)} />
          <StatCell label="Avg MFE" value={fmtR(summary.avgMfeR)} />
          <StatCell label="Avg MAE" value={fmtR(summary.avgMaeR)} />
          <StatCell label="Losing streak" value={summary.longestLosingStreak} />
          <StatCell label="Exit engine" value={summary.exitEngine || summary.exitMode} />
        </div>
      </Card>

      {/* Equity curve */}
      <Card className="overflow-hidden">
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
                contentStyle={{
                  fontSize: 12,
                  borderRadius: 8,
                  borderColor: 'var(--color-border)',
                  backgroundColor: 'var(--color-surface)',
                  color: 'var(--color-ink)',
                }}
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
      <Card className="overflow-hidden">
        <CardHeader
          title={`Trades · ${visibleTrades.length}${filtersActive ? ` of ${trades.length}` : ''}`}
          className="flex-wrap gap-2"
          right={
            <div className="flex flex-wrap items-end justify-end gap-2">
              <FilterSelect
                label="Status"
                value={statusFilter}
                onChange={(value) => setStatusFilter(value as typeof statusFilter)}
              >
                <option value="filled">Filled trades</option>
                <option value="all">All statuses</option>
                {statusOptions.map((status) => (
                  <option key={status} value={status}>
                    {formatTradeLabel(status)}
                  </option>
                ))}
              </FilterSelect>
              <FilterSelect label="Exit reason" value={exitReasonFilter} onChange={setExitReasonFilter}>
                <option value="all">All exit reasons</option>
                {exitReasonOptions.map((reason) => (
                  <option key={reason} value={reason}>
                    {formatTradeLabel(reason)}
                  </option>
                ))}
              </FilterSelect>
            </div>
          }
        />
        <div className="max-h-[480px] overflow-auto border-t border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-surface-2/95 text-[9px] font-semibold uppercase tracking-[0.1em] text-ink-faint backdrop-blur">
              <tr className="[&>th]:px-2.5 [&>th]:py-2.5 [&>th]:text-right [&>th]:font-semibold">
                <th className="!pl-4 !text-left">Symbol</th>
                <SortableHeader
                  label="Status"
                  sortKey="status"
                  sort={sort}
                  onSort={toggleSort}
                  align="left"
                />
                <th>Signal</th>
                <th>Entry</th>
                <th>Exit</th>
                <SortableHeader label="Days" sortKey="holdingDays" sort={sort} onSort={toggleSort} />
                <SortableHeader label="Return" sortKey="returnPct" sort={sort} onSort={toggleSort} />
                <SortableHeader label="R" sortKey="rMultiple" sort={sort} onSort={toggleSort} />
                <th className="!pr-4 !text-left">Exit reason</th>
              </tr>
            </thead>
            <tbody>
              {visibleTrades.length === 0 && (
                <tr className="border-t border-border">
                  <td colSpan={9} className="px-4 py-8 text-center text-xs text-ink-faint">
                    No trades match these filters.
                  </td>
                </tr>
              )}
              {visibleTrades.slice(0, 500).map((t, i) => (
                <tr
                  key={`${t.symbol}-${t.signalDate}-${i}`}
                  className={cn(
                    'border-t border-border/80 hover:bg-surface-2',
                    (t.returnPct ?? 0) > 0 && 'bg-up-soft/30 hover:bg-up-soft/60',
                    (t.returnPct ?? 0) < 0 && 'bg-down-soft/30 hover:bg-down-soft/60'
                  )}
                >
                  <td className="px-2 py-1.5 pl-4">
                    <Link
                      to={symbolSignalPath(t.symbol, t.signalDate, 'backtest')}
                      className="font-semibold text-accent hover:underline"
                    >
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
                    {t.returnPct != null ? fmtPctRaw(t.returnPct) : '—'}
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
                    {getTradeExitReason(t) ?? '—'}
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

type TradeSortKey = 'status' | 'holdingDays' | 'returnPct' | 'rMultiple'
type SortDirection = 'asc' | 'desc'
type TradeSort = { key: TradeSortKey; direction: SortDirection }

function getTradeExitReason(trade: BacktestTrade) {
  return trade.exitReason ?? trade.exitPhase ?? null
}

function getTradeSortValue(trade: BacktestTrade, key: TradeSortKey): string | number | null {
  return trade[key]
}

function compareSortValues(
  a: string | number | null,
  b: string | number | null,
  direction: SortDirection
) {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1

  const comparison = typeof a === 'number' && typeof b === 'number'
    ? a - b
    : String(a).localeCompare(String(b))
  return direction === 'asc' ? comparison : -comparison
}

function compareTradeRecency(a: BacktestTrade, b: BacktestTrade) {
  return (b.exitDate ?? b.signalDate).localeCompare(a.exitDate ?? a.signalDate)
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = 'right',
}: {
  label: string
  sortKey: TradeSortKey
  sort: TradeSort | null
  onSort: (key: TradeSortKey) => void
  align?: 'left' | 'right'
}) {
  const active = sort?.key === sortKey
  const Icon = active ? (sort.direction === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown

  return (
    <th aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        title={`Sort by ${label.toLowerCase()}`}
        className={cn(
          'flex w-full items-center gap-1 text-[10px] font-semibold uppercase tracking-wider hover:text-ink',
          align === 'left' ? 'justify-start' : 'justify-end',
          active && 'text-accent'
        )}
      >
        {label}
        <Icon className="size-3 shrink-0" aria-hidden="true" />
      </button>
    </th>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: React.ReactNode
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-8 max-w-48 rounded-lg border border-border bg-surface-2/40 px-2.5 text-[11px] text-ink-soft outline-none hover:border-border-strong focus:border-accent focus:bg-surface"
      >
        {children}
      </select>
    </label>
  )
}

function formatTradeLabel(value: string) {
  return value.replace(/_/g, ' ')
}

const statusTones: Partial<Record<TradeStatus, 'up' | 'down' | 'warn' | 'accent' | 'neutral'>> = {
  open: 'accent',
  pending_entry: 'warn',
  target: 'up',
  stopped: 'down',
  time_stop: 'warn',
  dead_money: 'warn',
  not_filled: 'neutral',
}

function StatusBadge({ status }: { status: TradeStatus }) {
  return <Badge tone={statusTones[status] ?? 'neutral'}>{formatTradeLabel(status)}</Badge>
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

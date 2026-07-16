import { Link } from 'react-router'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ArrowRight, CalendarRange, ChartNoAxesCombined, ScanSearch } from 'lucide-react'
import {
  useBreadth,
  useBreadthHistory,
  useScanSessions,
  useSignals,
  useWatchlist,
} from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  PageHeader,
  Spinner,
  StatCell,
} from '@/components/ui/primitives'
import { fmtDate, fmtDateShort, fmtNum, fmtPct, symbolSignalPath } from '@/lib/utils'

const regimeTone = { Strong: 'up', Weak: 'down', Neutral: 'warn' } as const

export function Dashboard() {
  const scanDate = useUIStore((s) => s.scanDate)
  const setScanDate = useUIStore((s) => s.setScanDate)
  const { data: sessions, isLoading: datesLoading } = useScanSessions()
  const dates = sessions?.map((session) => session.date)
  const { data: breadth } = useBreadth(scanDate)
  const { data: signals } = useSignals(scanDate)
  const { data: watchlist } = useWatchlist(scanDate)
  const breadthHistory = useBreadthHistory(dates)
  const signalCounts = [...(sessions ?? []).slice(0, 30)].reverse()

  if (datesLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Loading scan history…" />
      </div>
    )
  }

  if (!sessions?.length) {
    return (
      <EmptyState
        title="No scan runs found"
        hint="Run `sepa run` to update data and scan the universe."
      />
    )
  }

  const breakouts = signals?.filter((s) => s.signalType === 'BREAKOUT') ?? []
  const watches = signals?.filter((s) => s.signalType === 'WATCH') ?? []

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-6 py-5">
      <PageHeader
        eyebrow="Daily market intelligence"
        title="Market overview"
        description={`Breadth, signal flow, and today’s review queue for ${fmtDate(scanDate)}.`}
        right={
          <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-[11px] text-ink-faint shadow-[var(--shadow-card)]">
            <CalendarRange className="size-3.5 text-accent" />
            {sessions.length} completed scan sessions
          </div>
        }
      />

      {/* Market health hero */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card className="relative overflow-hidden lg:col-span-4">
          <div className="absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-accent via-up to-transparent" />
          <div className="flex items-center justify-between px-5 pt-5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              Market regime
            </div>
            <ChartNoAxesCombined className="size-4 text-ink-faint" />
          </div>
          {breadth ? (
            <div className="px-5 pb-5">
              <div className="mt-4 flex items-end justify-between gap-3">
                <div>
                  <div className="text-[28px] font-semibold tracking-[-0.045em]">{breadth.regime}</div>
                  <div className="mt-1 text-[11px] text-ink-faint">As of {fmtDate(scanDate)}</div>
                </div>
                <Badge tone={regimeTone[breadth.regime]} className="mb-1 px-2.5 py-1">
                  <span className="size-1.5 rounded-full bg-current" />
                  {fmtPct(breadth.breadth, 0)} breadth
                </Badge>
              </div>
              <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${Math.max(2, Math.min(100, breadth.breadth * 100))}%` }}
                />
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-ink-faint">
                <span>{breadth.aboveSma50} of {breadth.totalEligible} above 50-SMA</span>
                <span className={breadth.marketFilterOn ? 'text-up' : 'text-warn'}>
                  Filter {breadth.marketFilterOn ? 'on' : 'off'}
                </span>
              </div>
            </div>
          ) : (
            <div className="flex min-h-32 items-center justify-center"><Spinner /></div>
          )}
        </Card>

        <Card className="overflow-hidden lg:col-span-8">
          <CardHeader
            title="Breadth history"
            subtitle="% of eligible universe above 50-SMA, last 30 scans. Strong ≥ 40%, Weak ≤ 20%."
          />
          <div className="h-44 px-2 pb-2">
            {breadthHistory.isLoading ? (
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <ResponsiveContainer>
                <AreaChart data={breadthHistory.data} margin={{ top: 4, right: 8, left: -18 }}>
                  <CartesianGrid stroke="var(--color-border)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={fmtDateShort}
                    tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={{ stroke: 'var(--color-border)' }}
                  />
                  <YAxis
                    domain={[0, 1]}
                    tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                    tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    formatter={(v) => fmtPct(Number(v))}
                    labelFormatter={(l) => fmtDate(String(l))}
                    contentStyle={{
                      fontSize: 12,
                      borderRadius: 8,
                      borderColor: 'var(--color-border)',
                      backgroundColor: 'var(--color-surface)',
                      color: 'var(--color-ink)',
                    }}
                  />
                  <ReferenceLine y={0.4} stroke="var(--color-up)" strokeDasharray="4 4" />
                  <ReferenceLine y={0.2} stroke="var(--color-down)" strokeDasharray="4 4" />
                  <Area
                    dataKey="breadth"
                    type="monotone"
                    stroke="var(--color-accent)"
                    strokeWidth={1.5}
                    fill="var(--color-accent-soft)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      {/* Signal activity + today's queue */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="overflow-hidden">
          <CardHeader
            title="Signal activity"
            subtitle="BREAKOUT and WATCH counts per scan, last 30 scans"
          />
          <div className="h-44 px-2 pb-2">
            <ResponsiveContainer>
                <BarChart data={signalCounts} margin={{ top: 4, right: 8, left: -24 }}>
                  <CartesianGrid stroke="var(--color-border)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={fmtDateShort}
                    tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={{ stroke: 'var(--color-border)' }}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fontSize: 10, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    labelFormatter={(l) => fmtDate(String(l))}
                    contentStyle={{
                      fontSize: 12,
                      borderRadius: 8,
                      borderColor: 'var(--color-border)',
                      backgroundColor: 'var(--color-surface)',
                      color: 'var(--color-ink)',
                    }}
                  />
                  {scanDate && (
                    <ReferenceLine x={scanDate} stroke="var(--color-ink-soft)" strokeDasharray="3 3" />
                  )}
                  <Bar
                    dataKey="breakoutCount"
                    name="Breakouts"
                    stackId="a"
                    fill="var(--color-up)"
                    cursor="pointer"
                    onClick={(entry) => setScanDate(String(entry.payload.date))}
                  />
                  <Bar
                    dataKey="watchCount"
                    name="Watch"
                    stackId="a"
                    fill="var(--color-accent)"
                    radius={[2, 2, 0, 0]}
                    cursor="pointer"
                    onClick={(entry) => setScanDate(String(entry.payload.date))}
                  />
                </BarChart>
              </ResponsiveContainer>
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader
            title={`Today's queue · ${fmtDate(scanDate)}`}
            right={
              <Link
                to="/scanner"
                className="flex h-7 items-center gap-1 rounded-md bg-accent-soft px-2.5 text-[11px] font-semibold text-accent hover:bg-accent hover:text-white"
              >
                Open setup desk <ArrowRight className="size-3" />
              </Link>
            }
          />
          <div className="grid grid-cols-3 divide-x divide-border border-y border-border bg-surface-2/45">
            <StatCell label="Breakouts" value={breakouts.length} tone={breakouts.length ? 'up' : 'neutral'} />
            <StatCell label="Watch signals" value={watches.length} />
            <StatCell label="Full watchlist" value={watchlist?.length ?? '—'} />
          </div>
          <div className="border-t border-border">
            {signals?.length ? (
              <table className="w-full text-xs">
                <tbody>
                  {[...breakouts, ...watches].slice(0, 6).map((s) => (
                    <tr key={s.symbol} className="border-b border-border/80 last:border-0 hover:bg-surface-2">
                      <td className="px-4 py-2">
                        <Link
                          to={symbolSignalPath(s.symbol, s.signalDate, 'dashboard')}
                          className="font-semibold text-ink hover:text-accent"
                        >
                          {s.symbol}
                        </Link>
                      </td>
                      <td className="py-1.5">
                        <Badge tone={s.signalType === 'BREAKOUT' ? 'up' : 'accent'}>{s.signalType}</Badge>
                      </td>
                      <td className="tnum py-1.5 text-right text-ink-soft">RS {Math.round(s.rsRank)}</td>
                      <td className="tnum px-4 py-2 text-right text-ink-soft">
                        {fmtNum(s.volMult, 1)}× vol
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState title="No signals for this date" />
            )}
          </div>
        </Card>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-border/70 bg-surface/60 px-4 py-2.5 text-[11px] text-ink-faint">
        <span className="flex items-center gap-2">
          <ScanSearch className="size-3.5 text-accent" />
          Scanner results always cover the complete configured NSE universe.
        </span>
        <span>Click a signal-activity bar to move to that completed scan date.</span>
      </div>
    </div>
  )
}

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
import { ArrowRight } from 'lucide-react'
import {
  useBreadth,
  useBreadthHistory,
  useScanDates,
  useSignalCounts,
  useSignals,
  useWatchlist,
} from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import { Badge, Card, CardHeader, EmptyState, Spinner, StatCell } from '@/components/ui/primitives'
import { fmtDate, fmtDateShort, fmtNum, fmtPct } from '@/lib/utils'

const regimeTone = { Strong: 'up', Weak: 'down', Neutral: 'warn' } as const

export function Dashboard() {
  const scanDate = useUIStore((s) => s.scanDate)
  const { data: dates, isLoading: datesLoading } = useScanDates()
  const { data: breadth } = useBreadth(scanDate)
  const { data: signals } = useSignals(scanDate)
  const { data: watchlist } = useWatchlist(scanDate)
  const breadthHistory = useBreadthHistory(dates)
  const signalCounts = useSignalCounts(dates)

  if (datesLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Loading scan history…" />
      </div>
    )
  }

  if (!dates?.length) {
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
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      {/* Market health hero */}
      <div className="grid grid-cols-4 gap-4">
        <Card className="col-span-1 flex flex-col justify-between p-4">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
            Market regime · {fmtDate(scanDate)}
          </div>
          {breadth ? (
            <>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-2xl font-bold">{breadth.regime}</span>
                <Badge tone={regimeTone[breadth.regime]}>{fmtPct(breadth.breadth, 0)}</Badge>
              </div>
              <div className="mt-1 text-xs text-ink-faint">
                {breadth.aboveSma50} of {breadth.totalEligible} eligible above 50-SMA
                {!breadth.marketFilterOn && ' · market filter OFF'}
              </div>
            </>
          ) : (
            <Spinner />
          )}
        </Card>

        <Card className="col-span-3">
          <CardHeader
            title="Breadth history"
            subtitle="% of eligible universe above 50-SMA, last 30 scans. Strong ≥ 40%, Weak ≤ 20%."
          />
          <div className="h-40 px-2 pb-2">
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
                    contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: 'var(--color-border)' }}
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
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader
            title="Signal activity"
            subtitle="BREAKOUT and WATCH counts per scan, last 30 scans"
          />
          <div className="h-44 px-2 pb-2">
            {signalCounts.isLoading ? (
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <ResponsiveContainer>
                <BarChart data={signalCounts.data} margin={{ top: 4, right: 8, left: -24 }}>
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
                    contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: 'var(--color-border)' }}
                  />
                  <Bar dataKey="breakouts" name="Breakouts" stackId="a" fill="var(--color-up)" />
                  <Bar dataKey="watch" name="Watch" stackId="a" fill="var(--color-accent)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title={`Today's queue · ${fmtDate(scanDate)}`}
            right={
              <Link
                to="/scanner"
                className="flex items-center gap-1 text-xs font-medium text-accent hover:underline"
              >
                Triage <ArrowRight className="size-3" />
              </Link>
            }
          />
          <div className="grid grid-cols-3 divide-x divide-border border-t border-border">
            <StatCell label="Breakouts" value={breakouts.length} tone={breakouts.length ? 'up' : 'neutral'} />
            <StatCell label="Watch" value={watches.length} />
            <StatCell label="Watchlist candidates" value={watchlist?.length ?? '—'} />
          </div>
          <div className="border-t border-border">
            {signals?.length ? (
              <table className="w-full text-xs">
                <tbody>
                  {[...breakouts, ...watches].slice(0, 6).map((s) => (
                    <tr key={s.symbol} className="border-b border-border last:border-0 hover:bg-surface-2">
                      <td className="px-4 py-1.5">
                        <Link to={`/symbol/${s.symbol}`} className="font-semibold text-accent hover:underline">
                          {s.symbol}
                        </Link>
                      </td>
                      <td className="py-1.5">
                        <Badge tone={s.signalType === 'BREAKOUT' ? 'up' : 'accent'}>{s.signalType}</Badge>
                      </td>
                      <td className="tnum py-1.5 text-right text-ink-soft">RS {Math.round(s.rsRank)}</td>
                      <td className="tnum px-4 py-1.5 text-right text-ink-soft">
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
    </div>
  )
}

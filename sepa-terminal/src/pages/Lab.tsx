import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { FlaskConical, Play, Trash2 } from 'lucide-react'
import {
  useCompareRuns,
  useCreateLabRun,
  useDeleteLabRun,
  useLabRuns,
  useLabStrategies,
} from '@/api/lab'
import { ParamForm, paramsAreValid } from '@/components/lab/ParamForm'
import type { LabRun, LabStrategy } from '@/lib/lab-types'
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
} from '@/components/ui/primitives'
import { cn, fmtDateShort } from '@/lib/utils'

type Tab = 'strategies' | 'runs' | 'compare'

// Distinguishable in both themes and for the common forms of colour blindness.
const SERIES_COLORS = ['#2563eb', '#e11d48', '#059669', '#d97706', '#7c3aed', '#0891b2']

export function Lab() {
  const [tab, setTab] = useState<Tab>('strategies')
  const [selectedRuns, setSelectedRuns] = useState<string[]>([])

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-6 py-5">
      <PageHeader
        eyebrow="Strategy research"
        title="Strategy lab"
        description="Run any registered strategy on the shared engine, then compare results on one equity curve and one leaderboard."
        right={
          <div className="flex size-10 items-center justify-center rounded-xl border border-accent/15 bg-accent-soft text-accent shadow-[var(--shadow-card)]">
            <FlaskConical className="size-4.5" />
          </div>
        }
      />

      <div className="flex gap-1 border-b border-border">
        {(['strategies', 'runs', 'compare'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-3 py-2 text-xs capitalize',
              tab === t
                ? 'border-b-2 border-accent font-semibold text-accent'
                : 'text-ink-faint hover:text-ink'
            )}
          >
            {t}
            {t === 'compare' && selectedRuns.length > 0 && (
              <span className="ml-1.5 text-[10px]">({selectedRuns.length})</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'strategies' && <StrategiesTab />}
      {tab === 'runs' && (
        <RunsTab
          selected={selectedRuns}
          setSelected={setSelectedRuns}
          onCompare={() => setTab('compare')}
        />
      )}
      {tab === 'compare' && <CompareTab runIds={selectedRuns} />}
    </div>
  )
}

function StrategiesTab() {
  const { data: strategies, isLoading, error } = useLabStrategies()
  const [activeId, setActiveId] = useState<string | null>(null)

  if (isLoading) return <Spinner label="Loading strategies" />
  if (error) return <ErrorState error={error} />
  if (!strategies?.length) return <EmptyState title="No strategies registered" />

  const active = strategies.find((s) => s.id === activeId) ?? null

  return (
    <div className="grid grid-cols-[1fr_420px] gap-4">
      <div className="space-y-2">
        {strategies.map((s) => (
          <button
            key={s.id}
            onClick={() => setActiveId(s.id)}
            className={cn(
              'w-full rounded-xl border p-3 text-left transition',
              activeId === s.id
                ? 'border-accent/40 bg-accent-soft'
                : 'border-border bg-surface hover:border-border-strong'
            )}
          >
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-ink">{s.name}</span>
              <Badge tone={s.kind === 'signal' ? 'accent' : 'neutral'}>{s.kind}</Badge>
              <span className="ml-auto font-mono text-[10px] text-ink-faint">{s.id}</span>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
              {s.description}
            </p>
          </button>
        ))}
      </div>

      {active ? (
        <RunPanel strategy={active} />
      ) : (
        <Card className="p-4">
          <EmptyState
            title="Pick a strategy"
            hint="Its parameters appear here, generated from the strategy's own metadata."
          />
        </Card>
      )}
    </div>
  )
}

function RunPanel({ strategy }: { strategy: LabStrategy }) {
  const navigate = useNavigate()
  const createRun = useCreateLabRun()

  const [values, setValues] = useState<Record<string, unknown>>(strategy.defaults)
  const [startDate, setStartDate] = useState('2015-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [label, setLabel] = useState('')

  // Switching strategy must reset the form: the previous strategy's parameter
  // names are meaningless here and would be rejected as unknown.
  useEffect(() => {
    setValues(strategy.defaults)
    setLabel('')
  }, [strategy.id, strategy.defaults])

  const valid = paramsAreValid(strategy.paramSpecs, values) && startDate < endDate

  const submit = () => {
    createRun.mutate(
      {
        strategyId: strategy.id,
        startDate,
        endDate,
        params: values,
        label: label || null,
      },
      { onSuccess: (res) => navigate(`/lab/runs/${res.runId}`) }
    )
  }

  return (
    <Card className="space-y-4 p-4">
      <div>
        <div className="text-xs font-semibold text-ink">{strategy.name}</div>
        <div className="mt-0.5 text-[10px] text-ink-faint">
          reads {strategy.dataRequirement === 'panel' ? 'the price panel' : 'stored signals'}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] text-ink-faint">Start</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="tnum h-8 rounded-lg border border-border bg-surface-2/40 px-2 text-xs outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] text-ink-faint">End</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="tnum h-8 rounded-lg border border-border bg-surface-2/40 px-2 text-xs outline-none focus:border-accent"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-[10px] text-ink-faint">Label (optional)</span>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="what are you testing?"
          className="h-8 rounded-lg border border-border bg-surface-2/40 px-2 text-xs outline-none focus:border-accent"
        />
      </label>

      <div className="border-t border-border pt-3">
        <ParamForm specs={strategy.paramSpecs} values={values} onChange={setValues} />
      </div>

      {startDate >= endDate && (
        <p className="text-[10px] text-danger">End date must be after start date.</p>
      )}
      {createRun.error && <ErrorState error={createRun.error} />}

      <button
        onClick={submit}
        disabled={!valid || createRun.isPending}
        className={cn(
          'flex h-9 w-full items-center justify-center gap-1.5 rounded-lg text-xs font-semibold',
          valid && !createRun.isPending
            ? 'bg-accent text-white hover:opacity-90'
            : 'cursor-not-allowed bg-surface-2 text-ink-faint'
        )}
      >
        <Play className="size-3.5" />
        {createRun.isPending ? 'Queueing…' : 'Run backtest'}
      </button>
    </Card>
  )
}

type SortKey = 'createdAt' | 'cagrPct' | 'maxDrawdownPct' | 'calmar' | 'sharpe' | 'trades'

function RunsTab({
  selected,
  setSelected,
  onCompare,
}: {
  selected: string[]
  setSelected: (v: string[]) => void
  onCompare: () => void
}) {
  const { data: runs, isLoading, error } = useLabRuns()
  const deleteRun = useDeleteLabRun()
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('createdAt')
  const [desc, setDesc] = useState(true)

  const sorted = useMemo(() => {
    if (!runs) return []
    const value = (r: LabRun): number | string => {
      if (sortKey === 'createdAt') return r.createdAt
      return r.metrics?.[sortKey] ?? Number.NEGATIVE_INFINITY
    }
    return [...runs].sort((a, b) => {
      const av = value(a)
      const bv = value(b)
      const cmp = typeof av === 'string' ? String(av).localeCompare(String(bv)) : (av as number) - (bv as number)
      return desc ? -cmp : cmp
    })
  }, [runs, sortKey, desc])

  if (isLoading) return <Spinner label="Loading runs" />
  if (error) return <ErrorState error={error} />
  if (!runs?.length)
    return <EmptyState title="No runs yet" hint="Run a strategy from the Strategies tab." />

  const toggle = (runId: string) =>
    setSelected(
      selected.includes(runId)
        ? selected.filter((id) => id !== runId)
        : selected.length >= 6
          ? selected
          : [...selected, runId]
    )

  const header = (key: SortKey, text: string) => (
    <th
      className="cursor-pointer px-2 py-1.5 text-right font-medium hover:text-ink"
      onClick={() => {
        if (sortKey === key) setDesc(!desc)
        else {
          setSortKey(key)
          setDesc(true)
        }
      }}
    >
      {text}
      {sortKey === key && <span className="ml-0.5">{desc ? '↓' : '↑'}</span>}
    </th>
  )

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-surface-2/50 px-3 py-2">
        <span className="text-[11px] font-semibold text-ink">{runs.length} runs</span>
        <span className="text-[10px] text-ink-faint">select 2–6 to compare</span>
        <button
          disabled={selected.length < 2}
          onClick={onCompare}
          className={cn(
            'ml-auto rounded-lg px-2.5 py-1 text-[11px] font-semibold',
            selected.length >= 2
              ? 'bg-accent text-white hover:opacity-90'
              : 'cursor-not-allowed bg-surface-2 text-ink-faint'
          )}
        >
          Compare ({selected.length})
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead className="border-b border-border text-[10px] uppercase text-ink-faint">
            <tr>
              <th className="w-8 px-2 py-1.5" />
              <th className="px-2 py-1.5 text-left font-medium">Strategy</th>
              <th className="px-2 py-1.5 text-left font-medium">Window</th>
              <th className="px-2 py-1.5 text-left font-medium">Status</th>
              {header('cagrPct', 'CAGR')}
              {header('maxDrawdownPct', 'Max DD')}
              {header('calmar', 'Calmar')}
              {header('sharpe', 'Sharpe')}
              {header('trades', 'Trades')}
              <th className="w-8 px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((run) => (
              <tr
                key={run.runId}
                className="cursor-pointer border-b border-border/50 hover:bg-surface-2/40"
                onClick={() => navigate(`/lab/runs/${run.runId}`)}
              >
                <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={selected.includes(run.runId)}
                    onChange={() => toggle(run.runId)}
                    disabled={run.status !== 'done'}
                  />
                </td>
                <td className="px-2 py-1.5">
                  <div className="font-medium text-ink">{run.strategyId}</div>
                  {run.label && <div className="text-[10px] text-ink-faint">{run.label}</div>}
                </td>
                <td className="tnum px-2 py-1.5 text-ink-faint">
                  {fmtDateShort(run.startDate)} – {fmtDateShort(run.endDate)}
                </td>
                <td className="px-2 py-1.5">
                  <RunStatusBadge run={run} />
                </td>
                <td className="tnum px-2 py-1.5 text-right">{pct(run.metrics?.cagrPct)}</td>
                <td className="tnum px-2 py-1.5 text-right">
                  {pct(run.metrics?.maxDrawdownPct)}
                </td>
                <td className="tnum px-2 py-1.5 text-right">{num(run.metrics?.calmar)}</td>
                <td className="tnum px-2 py-1.5 text-right">{num(run.metrics?.sharpe)}</td>
                <td className="tnum px-2 py-1.5 text-right">{run.metrics?.trades ?? '—'}</td>
                <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => deleteRun.mutate(run.runId)}
                    className="text-ink-faint hover:text-danger"
                    aria-label="Delete run"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export function RunStatusBadge({ run }: { run: { status: string; progressPct?: number; stage?: string | null } }) {
  if (run.status === 'done') return <Badge tone="up">done</Badge>
  if (run.status === 'failed') return <Badge tone="down">failed</Badge>
  return (
    <Badge tone="neutral">
      {run.stage ?? run.status}
      {run.progressPct ? ` ${Math.round(run.progressPct)}%` : ''}
    </Badge>
  )
}

function CompareTab({ runIds }: { runIds: string[] }) {
  const compare = useCompareRuns()
  const { mutate } = compare

  useEffect(() => {
    if (runIds.length >= 2) mutate(runIds)
  }, [runIds, mutate])

  if (runIds.length < 2)
    return (
      <EmptyState
        title="Select at least two runs"
        hint="Pick them on the Runs tab, then come back."
      />
    )
  if (compare.isPending) return <Spinner label="Aligning runs" />
  if (compare.error) return <ErrorState error={compare.error} />
  if (!compare.data) return null

  const { series, warnings, commonDays } = compare.data

  const merged = new Map<string, Record<string, number | string | null>>()
  for (const s of series) {
    for (const p of s.points) {
      const row = merged.get(p.date) ?? { date: p.date }
      row[s.runId] = p.value
      merged.set(p.date, row)
    }
  }
  const chartData = [...merged.values()].sort((a, b) =>
    String(a.date).localeCompare(String(b.date))
  )

  return (
    <div className="space-y-4">
      {warnings.map((w) => (
        <div
          key={w}
          className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-warning"
        >
          {w}
        </div>
      ))}

      <Card className="p-4">
        <div className="mb-2 text-[11px] font-semibold text-ink">
          Equity, rebased to 100 over {commonDays} shared sessions
        </div>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} width={50} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.map((s, i) => (
                <Line
                  key={s.runId}
                  type="monotone"
                  dataKey={s.runId}
                  name={s.label || s.strategyId}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  dot={false}
                  strokeWidth={1.5}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead className="border-b border-border text-[10px] uppercase text-ink-faint">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">Run</th>
              <th className="px-2 py-1.5 text-right font-medium">CAGR</th>
              <th className="px-2 py-1.5 text-right font-medium">Max DD</th>
              <th className="px-2 py-1.5 text-right font-medium">Calmar</th>
              <th className="px-2 py-1.5 text-right font-medium">Sharpe</th>
              <th className="px-2 py-1.5 text-right font-medium">Trades</th>
            </tr>
          </thead>
          <tbody>
            {series.map((s, i) => (
              <tr key={s.runId} className="border-b border-border/50">
                <td className="px-2 py-1.5">
                  <span
                    className="mr-1.5 inline-block size-2 rounded-full align-middle"
                    style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
                  />
                  {s.label || s.strategyId}
                </td>
                <td className="tnum px-2 py-1.5 text-right">{pct(s.metrics?.cagrPct)}</td>
                <td className="tnum px-2 py-1.5 text-right">
                  {pct(s.metrics?.maxDrawdownPct)}
                </td>
                <td className="tnum px-2 py-1.5 text-right">{num(s.metrics?.calmar)}</td>
                <td className="tnum px-2 py-1.5 text-right">{num(s.metrics?.sharpe)}</td>
                <td className="tnum px-2 py-1.5 text-right">{s.metrics?.trades ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

function pct(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${v.toFixed(2)}%`
}

function num(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : v.toFixed(2)
}

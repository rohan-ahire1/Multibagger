import { NavLink } from 'react-router'
import { Activity, Command } from 'lucide-react'
import { useScanDates, useBreadth } from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import { useEffect } from 'react'
import { cn, fmtDateShort, fmtPct } from '@/lib/utils'
import { Badge, Kbd } from '@/components/ui/primitives'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/scanner', label: 'Scanner', end: false },
  { to: '/backtest', label: 'Backtest', end: false },
]

const regimeTone = { Strong: 'up', Weak: 'down', Neutral: 'warn' } as const

export function TopBar() {
  const { data: dates } = useScanDates()
  const scanDate = useUIStore((s) => s.scanDate)
  const setScanDate = useUIStore((s) => s.setScanDate)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const { data: breadth } = useBreadth(scanDate)

  // Default to the latest scan date once dates load
  useEffect(() => {
    if (!scanDate && dates?.length) setScanDate(dates[0])
  }, [scanDate, dates, setScanDate])

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <div className="flex items-center gap-2">
        <Activity className="size-4 text-accent" />
        <span className="text-sm font-bold tracking-tight">SEPA Terminal</span>
      </div>

      <nav className="flex items-center gap-1">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                'rounded-md px-2.5 py-1 text-[13px] font-medium text-ink-soft hover:bg-surface-2 hover:text-ink',
                isActive && 'bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent'
              )
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        {breadth && (
          <Badge tone={regimeTone[breadth.regime]}>
            {breadth.regime} · {fmtPct(breadth.breadth, 0)} &gt;50MA
            {!breadth.marketFilterOn && ' · filter off'}
          </Badge>
        )}

        <select
          value={scanDate ?? ''}
          onChange={(e) => setScanDate(e.target.value)}
          className="tnum h-7 rounded-md border border-border bg-surface px-2 text-xs font-medium text-ink outline-none hover:border-border-strong focus:border-accent"
        >
          {!dates?.length && <option value="">no scans</option>}
          {dates?.map((d) => (
            <option key={d} value={d}>
              {fmtDateShort(d)} ({d})
            </option>
          ))}
        </select>

        <button
          onClick={toggleCommandPalette}
          className="flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-ink-faint hover:border-border-strong hover:text-ink-soft"
        >
          <Command className="size-3" />
          <span>Jump</span>
          <Kbd>⌘K</Kbd>
        </button>
      </div>
    </header>
  )
}

import { NavLink } from 'react-router'
import {
  Activity,
  Beaker,
  Command,
  FlaskConical,
  LayoutDashboard,
  Moon,
  ScanSearch,
  Sun,
} from 'lucide-react'
import { useScanSessions, useBreadth } from '@/api/hooks'
import { useUIStore } from '@/stores/ui'
import { useEffect } from 'react'
import { cn, fmtPct } from '@/lib/utils'
import { Badge, Kbd } from '@/components/ui/primitives'
import { ScanDatePicker } from './ScanDatePicker'

const NAV = [
  { to: '/', label: 'Overview', end: true, icon: LayoutDashboard },
  { to: '/scanner', label: 'Setup desk', end: false, icon: ScanSearch },
  { to: '/backtest', label: 'Research', end: false, icon: FlaskConical },
  { to: '/lab', label: 'Lab', end: false, icon: Beaker },
]

const regimeTone = { Strong: 'up', Weak: 'down', Neutral: 'warn' } as const

export function TopBar() {
  const { data: sessions, isLoading: sessionsLoading } = useScanSessions()
  const scanDate = useUIStore((s) => s.scanDate)
  const setScanDate = useUIStore((s) => s.setScanDate)
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette)
  const { data: breadth } = useBreadth(scanDate)

  // Default to the latest scan date once dates load
  useEffect(() => {
    if (!scanDate && sessions?.length) setScanDate(sessions[0].date)
  }, [scanDate, sessions, setScanDate])

  return (
    <header className="relative z-30 flex h-14 shrink-0 items-center gap-5 border-b border-border/80 bg-[var(--color-topbar)] px-5 shadow-[0_1px_0_rgba(0,0,0,0.02)] backdrop-blur-xl">
      <div className="flex min-w-40 items-center gap-2.5">
        <div className="relative flex size-8 items-center justify-center rounded-lg bg-accent text-white shadow-[0_5px_14px_color-mix(in_srgb,var(--color-accent)_24%,transparent)]">
          <Activity className="size-4" strokeWidth={2.2} />
          <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full border-2 border-surface bg-up" />
        </div>
        <div className="leading-none">
          <div className="text-[13px] font-bold tracking-[-0.025em]">SEPA</div>
          <div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
            Market terminal
          </div>
        </div>
      </div>

      <nav className="flex items-center gap-0.5 rounded-lg border border-border/80 bg-surface-2/80 p-1">
        {NAV.map((item) => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  'flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-semibold text-ink-soft hover:bg-surface hover:text-ink',
                  isActive && 'bg-surface text-accent shadow-sm hover:bg-surface hover:text-accent'
                )
              }
            >
              <Icon className="size-3.5" />
              {item.label}
            </NavLink>
          )
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2.5">
        {breadth && (
          <Badge tone={regimeTone[breadth.regime]} className="h-7 px-2.5">
            <span className="size-1.5 rounded-full bg-current" />
            {breadth.regime} · {fmtPct(breadth.breadth, 0)} &gt;50MA
            {!breadth.marketFilterOn && ' · filter off'}
          </Badge>
        )}

        <ScanDatePicker
          sessions={sessions ?? []}
          scanDate={scanDate}
          onChange={setScanDate}
          isLoading={sessionsLoading}
        />

        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
          title={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
          className="flex size-8 items-center justify-center rounded-lg border border-border bg-surface text-ink-faint shadow-sm hover:border-border-strong hover:bg-surface-2 hover:text-ink"
        >
          {theme === 'dark' ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
        </button>

        <button
          onClick={toggleCommandPalette}
          className="flex h-8 items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 text-[11px] font-medium text-ink-faint shadow-sm hover:border-border-strong hover:bg-surface-2 hover:text-ink-soft"
        >
          <Command className="size-3" />
          <span>Jump</span>
          <Kbd>⌘K</Kbd>
        </button>
      </div>
    </header>
  )
}

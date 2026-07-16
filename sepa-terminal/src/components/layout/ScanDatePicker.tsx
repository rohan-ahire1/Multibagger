import { useMemo, useState } from 'react'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { Popover } from 'radix-ui'
import type { ScanSessionSummary } from '@/lib/types'
import { cn, fmtDate } from '@/lib/utils'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function monthLabel(month: string): string {
  return new Date(`${month}-01T00:00:00`).toLocaleDateString('en-IN', {
    month: 'long',
    year: 'numeric',
  })
}

function shiftMonth(month: string, amount: number): string {
  const [year, monthNumber] = month.split('-').map(Number)
  const shifted = new Date(Date.UTC(year, monthNumber - 1 + amount, 1))
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, '0')}`
}

function calendarDays(month: string): Array<number | null> {
  const [year, monthNumber] = month.split('-').map(Number)
  const leadingBlanks = (new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay() + 6) % 7
  const daysInMonth = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate()
  const cells: Array<number | null> = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ]
  while (cells.length % 7) cells.push(null)
  return cells
}

interface ScanDatePickerProps {
  sessions: ScanSessionSummary[]
  scanDate: string | null
  onChange: (date: string) => void
  isLoading?: boolean
}

export function ScanDatePicker({
  sessions,
  scanDate,
  onChange,
  isLoading = false,
}: ScanDatePickerProps) {
  const [open, setOpen] = useState(false)
  const [breakoutsOnly, setBreakoutsOnly] = useState(true)
  const [monthChooserOpen, setMonthChooserOpen] = useState(false)
  const [visibleMonth, setVisibleMonth] = useState(
    () => scanDate?.slice(0, 7) ?? sessions[0]?.date.slice(0, 7) ?? new Date().toISOString().slice(0, 7)
  )
  const selectedIndex = sessions.findIndex((session) => session.date === scanDate)
  const selectedSession = selectedIndex >= 0 ? sessions[selectedIndex] : undefined
  const olderSession = selectedIndex >= 0 ? sessions[selectedIndex + 1] : undefined
  const newerSession = selectedIndex > 0 ? sessions[selectedIndex - 1] : undefined
  const sessionsByDate = useMemo(
    () => new Map(sessions.map((session) => [session.date, session])),
    [sessions]
  )
  const availableYears = useMemo(
    () => [...new Set(sessions.map((session) => session.date.slice(0, 4)))].sort(),
    [sessions]
  )
  const monthsWithSessions = useMemo(
    () => new Set(sessions.map((session) => session.date.slice(0, 7))),
    [sessions]
  )
  const monthCells = useMemo(() => calendarDays(visibleMonth), [visibleMonth])
  const newestMonth = sessions[0]?.date.slice(0, 7)
  const oldestMonth = sessions.at(-1)?.date.slice(0, 7)
  const visibleYear = visibleMonth.slice(0, 4)
  const visibleMonthNumber = visibleMonth.slice(5, 7)

  function selectDate(date: string) {
    onChange(date)
    setVisibleMonth(date.slice(0, 7))
    setMonthChooserOpen(false)
    setOpen(false)
  }

  function selectMonth(monthNumber: number) {
    setVisibleMonth(`${visibleYear}-${String(monthNumber).padStart(2, '0')}`)
    setMonthChooserOpen(false)
  }

  return (
    <div className="flex items-center">
      <button
        type="button"
        disabled={!olderSession}
        onClick={() => olderSession && selectDate(olderSession.date)}
        aria-label="Previous scan session"
        title="Previous scan session"
        className="flex size-8 items-center justify-center rounded-l-lg border border-r-0 border-border bg-surface text-ink-faint shadow-sm hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35"
      >
        <ChevronLeft className="size-3.5" />
      </button>

      <Popover.Root
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen)
          if (nextOpen) {
            if (scanDate) setVisibleMonth(scanDate.slice(0, 7))
            setMonthChooserOpen(false)
          }
        }}
      >
        <Popover.Trigger asChild>
          <button
            type="button"
            className="tnum flex h-8 min-w-44 items-center justify-center gap-1.5 border border-border bg-surface px-2.5 text-[11px] font-medium text-ink shadow-sm outline-none hover:border-border-strong hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent/35"
            aria-label="Choose scan date"
          >
            <CalendarDays className="size-3.5 text-ink-faint" />
            {isLoading ? (
              <span className="text-ink-faint">Loading scans…</span>
            ) : selectedSession ? (
              <>
                <span>{fmtDate(selectedSession.date)}</span>
                <span className={selectedSession.breakoutCount ? 'text-up' : 'text-ink-faint'}>
                  · {selectedSession.breakoutCount} BO
                </span>
              </>
            ) : (
              <span className="text-ink-faint">No scans</span>
            )}
            <ChevronDown className="ml-0.5 size-3 text-ink-faint" />
          </button>
        </Popover.Trigger>

        <Popover.Portal>
          <Popover.Content
            align="end"
            sideOffset={8}
            className="z-50 w-[430px] overflow-hidden rounded-xl border border-border bg-surface shadow-[var(--shadow-floating)] outline-none"
          >
            <div className="flex items-center justify-between border-b border-border p-2">
              <div className="flex rounded-md bg-surface-2 p-0.5">
                <div className="flex h-7 items-center gap-1.5 rounded bg-surface px-2.5 text-xs font-medium text-ink shadow-sm">
                  <CalendarDays className="size-3.5" /> Month
                </div>
                <button
                  type="button"
                  disabled={!sessions.length}
                  onClick={() => sessions[0] && selectDate(sessions[0].date)}
                  aria-label="Jump to latest completed scan session"
                  title="Jump to latest completed scan session"
                  className="flex h-7 items-center rounded px-2.5 text-xs font-medium text-ink-soft hover:bg-surface hover:text-ink disabled:cursor-not-allowed disabled:opacity-35"
                >
                  Today
                </button>
              </div>
              <button
                type="button"
                aria-pressed={breakoutsOnly}
                onClick={() => setBreakoutsOnly((value) => !value)}
                className={cn(
                  'h-7 rounded-md border px-2.5 text-[11px] font-semibold',
                  breakoutsOnly
                    ? 'border-up/30 bg-up-soft text-up'
                    : 'border-border text-ink-faint hover:text-ink-soft'
                )}
              >
                Breakout days only
              </button>
            </div>

            <div className="p-3">
                <div className="mb-3 flex items-center justify-between">
                  <button
                    type="button"
                    disabled={!oldestMonth || visibleMonth <= oldestMonth}
                    onClick={() => setVisibleMonth((month) => shiftMonth(month, -1))}
                    aria-label="Previous month"
                    className="flex size-7 items-center justify-center rounded-md border border-border text-ink-faint hover:bg-surface-2 hover:text-ink disabled:opacity-30"
                  >
                    <ChevronLeft className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setMonthChooserOpen((value) => !value)}
                    aria-expanded={monthChooserOpen}
                    aria-label="Choose year and month"
                    title="Choose year and month"
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-sm font-semibold text-ink hover:bg-surface-2"
                  >
                    {monthLabel(visibleMonth)}
                    <ChevronDown className={cn('size-3.5 text-ink-faint transition-transform', monthChooserOpen && 'rotate-180')} />
                  </button>
                  <button
                    type="button"
                    disabled={!newestMonth || visibleMonth >= newestMonth}
                    onClick={() => setVisibleMonth((month) => shiftMonth(month, 1))}
                    aria-label="Next month"
                    className="flex size-7 items-center justify-center rounded-md border border-border text-ink-faint hover:bg-surface-2 hover:text-ink disabled:opacity-30"
                  >
                    <ChevronRight className="size-3.5" />
                  </button>
                </div>
                {monthChooserOpen ? (
                  <div className="rounded-lg border border-border bg-surface-2 p-3">
                    <label className="mb-3 flex items-center justify-between gap-3 text-[11px] font-semibold text-ink-soft">
                      Year
                      <select
                        value={visibleYear}
                        onChange={(event) => setVisibleMonth(`${event.target.value}-${visibleMonthNumber}`)}
                        className="h-8 rounded-md border border-border bg-surface px-2 text-xs font-medium text-ink outline-none focus:border-accent"
                      >
                        {availableYears.map((year) => (
                          <option key={year} value={year}>{year}</option>
                        ))}
                      </select>
                    </label>
                    <div className="grid grid-cols-3 gap-1.5">
                      {MONTHS.map((month, index) => {
                        const monthNumber = String(index + 1).padStart(2, '0')
                        const monthKey = `${visibleYear}-${monthNumber}`
                        const available = monthsWithSessions.has(monthKey)
                        return (
                          <button
                            type="button"
                            key={month}
                            disabled={!available}
                            onClick={() => selectMonth(index + 1)}
                            className={cn(
                              'h-9 rounded-md border text-xs font-medium',
                              available && 'border-border bg-surface text-ink-soft hover:border-border-strong hover:text-ink',
                              !available && 'cursor-not-allowed border-transparent text-ink-faint opacity-35',
                              monthKey === visibleMonth && 'border-accent bg-accent-soft text-accent'
                            )}
                          >
                            {month}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ) : (
                <div className="grid grid-cols-7 gap-1">
                  {WEEKDAYS.map((weekday) => (
                    <div
                      key={weekday}
                      className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-ink-faint"
                    >
                      {weekday}
                    </div>
                  ))}
                  {monthCells.map((day, index) => {
                    if (day == null) return <div key={`blank-${index}`} className="h-12" />
                    const date = `${visibleMonth}-${String(day).padStart(2, '0')}`
                    const session = sessionsByDate.get(date)
                    const selectable = !!session && (!breakoutsOnly || session.breakoutCount > 0)
                    return (
                      <button
                        type="button"
                        key={date}
                        disabled={!selectable}
                        onClick={() => selectable && selectDate(date)}
                        aria-label={
                          session
                            ? `${fmtDate(date)}: ${session.breakoutCount} breakouts, ${session.watchCount} watch signals`
                            : fmtDate(date)
                        }
                        className={cn(
                          'tnum flex h-12 flex-col items-center justify-center rounded-md border border-transparent text-xs',
                          selectable && 'hover:border-border-strong hover:bg-surface-2',
                          !session && 'text-ink-faint opacity-25',
                          session && !selectable && 'text-ink-faint opacity-45',
                          date === scanDate && 'border-accent bg-accent-soft text-accent'
                        )}
                      >
                        <span className="font-medium">{day}</span>
                        {session && (
                          <span
                            className={cn(
                              'mt-0.5 text-[9px] font-bold',
                              session.breakoutCount ? 'text-up' : 'text-ink-faint'
                            )}
                          >
                            {session.breakoutCount ? `${session.breakoutCount} BO` : '—'}
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
                )}
                <div className="mt-3 border-t border-border pt-2 text-[10px] text-ink-faint">
                  Only dates with completed scan data are selectable.
                </div>
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      <button
        type="button"
        disabled={!newerSession}
        onClick={() => newerSession && selectDate(newerSession.date)}
        aria-label="Next scan session"
        title="Next scan session"
        className="flex size-8 items-center justify-center rounded-r-lg border border-l-0 border-border bg-surface text-ink-faint shadow-sm hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35"
      >
        <ChevronRight className="size-3.5" />
      </button>
    </div>
  )
}

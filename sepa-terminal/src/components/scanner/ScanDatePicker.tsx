import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, Calendar } from 'lucide-react'

interface ScanDatePickerProps {
  scanDate: string
  scanDates: string[]
  onDateChange: (date: string) => void
}

function formatDisplayDate(iso: string): string {
  if (!iso) return 'Select Date'
  const d = new Date(iso + 'T00:00:00')
  if (isNaN(d.getTime())) return 'Invalid Date'
  const day = d.toLocaleDateString('en-IN', { weekday: 'short' })
  const date = d.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
  return `${day}, ${date}`
}

export function ScanDatePicker({
  scanDate,
  scanDates,
  onDateChange,
}: ScanDatePickerProps) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0 })

  const currentIdx = scanDates.indexOf(scanDate)
  const hasPrev = currentIdx < scanDates.length - 1
  const hasNext = currentIdx > 0

  const updatePosition = useCallback(() => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      setDropdownPos({ top: rect.bottom + 4, left: rect.left })
    }
  }, [])

  useEffect(() => {
    if (!open) return
    updatePosition()
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
        triggerRef.current && !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open, updatePosition])

  function goPrev() {
    if (hasPrev) onDateChange(scanDates[currentIdx + 1])
  }

  function goNext() {
    if (hasNext) onDateChange(scanDates[currentIdx - 1])
  }

  const isLatest = currentIdx === 0

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={goPrev}
        disabled={!hasPrev}
        className="p-1 rounded hover:bg-bg-elevated text-text-dim hover:text-text-secondary disabled:opacity-30 disabled:cursor-default transition-colors"
      >
        <ChevronLeft size={14} />
      </button>

      <button
        ref={triggerRef}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-bg-elevated transition-colors"
      >
        <Calendar size={12} className="text-text-dim" />
        <span className="text-xs font-medium tabular-nums text-text-primary">
          {formatDisplayDate(scanDate)}
        </span>
        {isLatest && (
          <span className="text-[9px] uppercase tracking-wider px-1 py-0.5 rounded bg-green-bg text-green">
            latest
          </span>
        )}
      </button>

      <button
        onClick={goNext}
        disabled={!hasNext}
        className="p-1 rounded hover:bg-bg-elevated text-text-dim hover:text-text-secondary disabled:opacity-30 disabled:cursor-default transition-colors"
      >
        <ChevronRight size={14} />
      </button>

      {open &&
        createPortal(
          <div
            ref={dropdownRef}
            style={{ position: 'fixed', top: dropdownPos.top, left: dropdownPos.left }}
            className="z-[9999] w-56 max-h-64 overflow-y-auto bg-bg-surface border border-border-strong rounded-md shadow-lg"
          >
            {scanDates.map((d, i) => {
              const isActive = d === scanDate
              return (
                <button
                  key={d}
                  onClick={() => {
                    onDateChange(d)
                    setOpen(false)
                  }}
                  className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
                    isActive
                      ? 'bg-green/[0.08] text-green'
                      : 'text-text-secondary hover:bg-bg-elevated'
                  }`}
                >
                  <span className="tabular-nums">{formatDisplayDate(d)}</span>
                  {i === 0 && (
                    <span className="text-[9px] uppercase tracking-wider text-green">
                      latest
                    </span>
                  )}
                </button>
              )
            })}
          </div>,
          document.body
        )}
    </div>
  )
}

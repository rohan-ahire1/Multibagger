import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn('rounded-lg border border-border bg-surface', className)}>{children}</div>
  )
}

export function CardHeader({
  title,
  subtitle,
  right,
  className,
}: {
  title: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-start justify-between px-4 pt-3 pb-2', className)}>
      <div>
        <div className="text-[13px] font-semibold tracking-wide text-ink">{title}</div>
        {subtitle && <div className="mt-0.5 text-xs text-ink-faint">{subtitle}</div>}
      </div>
      {right}
    </div>
  )
}

type BadgeTone = 'up' | 'down' | 'warn' | 'accent' | 'neutral'

const badgeTones: Record<BadgeTone, string> = {
  up: 'bg-up-soft text-up',
  down: 'bg-down-soft text-down',
  warn: 'bg-warn-soft text-warn',
  accent: 'bg-accent-soft text-accent',
  neutral: 'bg-surface-2 text-ink-soft',
}

export function Badge({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: BadgeTone
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold',
        badgeTones[tone],
        className
      )}
    >
      {children}
    </span>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[18px] items-center justify-center rounded border border-border-strong bg-surface px-1 py-px font-mono text-[10px] text-ink-soft shadow-[0_1px_0_var(--color-border-strong)]">
      {children}
    </kbd>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-faint">
      <span className="size-3.5 animate-spin rounded-full border-2 border-border-strong border-t-accent" />
      {label ?? 'Loading…'}
    </div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-10 text-center">
      <div className="text-sm font-medium text-ink-soft">{title}</div>
      {hint && <div className="text-xs text-ink-faint">{hint}</div>}
    </div>
  )
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div className="m-3 rounded-md border border-down/30 bg-down-soft px-3 py-2 text-xs text-down">
      {error instanceof Error ? error.message : 'Something went wrong'}
    </div>
  )
}

export function StatCell({
  label,
  value,
  tone,
  className,
}: {
  label: string
  value: ReactNode
  tone?: 'up' | 'down' | 'neutral'
  className?: string
}) {
  return (
    <div className={cn('px-3 py-2', className)}>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        {label}
      </div>
      <div
        className={cn(
          'tnum mt-0.5 text-sm font-semibold',
          tone === 'up' && 'text-up',
          tone === 'down' && 'text-down'
        )}
      >
        {value}
      </div>
    </div>
  )
}

import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        'rounded-xl border border-border/90 bg-surface shadow-[var(--shadow-card)]',
        className
      )}
    >
      {children}
    </div>
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
    <div className={cn('flex items-start justify-between gap-4 px-4 pt-4 pb-3', className)}>
      <div>
        <div className="text-[13px] font-semibold tracking-[-0.01em] text-ink">{title}</div>
        {subtitle && <div className="mt-1 text-[11px] leading-relaxed text-ink-faint">{subtitle}</div>}
      </div>
      {right}
    </div>
  )
}

type BadgeTone = 'up' | 'down' | 'warn' | 'accent' | 'neutral'

const badgeTones: Record<BadgeTone, string> = {
  up: 'border-up/15 bg-up-soft text-up',
  down: 'border-down/15 bg-down-soft text-down',
  warn: 'border-warn/15 bg-warn-soft text-warn',
  accent: 'border-accent/15 bg-accent-soft text-accent',
  neutral: 'border-border bg-surface-2 text-ink-soft',
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
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold tracking-[0.01em]',
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
    <kbd className="inline-flex min-w-[18px] items-center justify-center rounded border border-border-strong/80 bg-surface px-1 py-px font-mono text-[9px] text-ink-soft shadow-[0_1px_0_var(--color-border-strong)]">
      {children}
    </kbd>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-faint">
      <span className="size-4 animate-spin rounded-full border-2 border-border-strong border-t-accent" />
      {label ?? 'Loading…'}
    </div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-6 py-12 text-center">
      <div className="mb-1 flex size-8 items-center justify-center rounded-full border border-border bg-surface-2">
        <span className="size-1.5 rounded-full bg-ink-faint" />
      </div>
      <div className="text-sm font-semibold text-ink-soft">{title}</div>
      {hint && <div className="max-w-md text-xs leading-relaxed text-ink-faint">{hint}</div>}
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
    <div className={cn('px-3.5 py-3', className)}>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
        {label}
      </div>
      <div
        className={cn(
          'tnum mt-1 text-[15px] font-semibold tracking-[-0.02em]',
          tone === 'up' && 'text-up',
          tone === 'down' && 'text-down'
        )}
      >
        {value}
      </div>
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  right,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  right?: ReactNode
}) {
  return (
    <div className="flex items-end justify-between gap-6">
      <div>
        {eyebrow && (
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
            {eyebrow}
          </div>
        )}
        <h1 className="text-[22px] font-semibold tracking-[-0.035em] text-ink">{title}</h1>
        {description && <div className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-faint">{description}</div>}
      </div>
      {right}
    </div>
  )
}

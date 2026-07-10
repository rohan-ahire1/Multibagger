import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function snakeToCamel(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(snakeToCamel)
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()),
        snakeToCamel(v),
      ])
    )
  }
  return obj
}

export function camelToSnake(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(camelToSnake)
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        k.replace(/([A-Z])/g, (c) => `_${c.toLowerCase()}`),
        camelToSnake(v),
      ])
    )
  }
  return obj
}

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const inrCompact = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 })

export function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return inr.format(v)
}

export function fmtCompact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return inrCompact.format(v)
}

export function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v == null || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(decimals)}%`
}

/** For values already expressed in percent units (e.g. 4.2 meaning 4.2%). */
export function fmtPctRaw(v: number | null | undefined, decimals = 1): string {
  if (v == null || Number.isNaN(v)) return '—'
  return `${v.toFixed(decimals)}%`
}

export function fmtR(v: number | null | undefined, decimals = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(decimals)}R`
}

export function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(decimals)
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
}

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

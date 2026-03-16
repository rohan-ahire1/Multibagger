export const formatINR = (value: number): string =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value)

export const formatINRCompact = (value: number): string => {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e7) return `${sign}\u20B9${(abs / 1e7).toFixed(2)}Cr`
  if (abs >= 1e5) return `${sign}\u20B9${(abs / 1e5).toFixed(2)}L`
  return formatINR(value)
}

export const formatPrice = (value: number | null | undefined): string => {
  if (value == null || isNaN(value)) return '\u2014'
  return value >= 100
    ? Math.round(value).toLocaleString('en-IN')
    : value.toFixed(2)
}

export const formatPct = (value: number): string => {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export const formatR = (value: number): string => {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}R`
}

export const formatVolMult = (value: number | null | undefined): string => {
  if (value == null || isNaN(value)) return '\u2014'
  return `${value.toFixed(1)}x`
}

export const formatDate = (dateStr: string): string => {
  const [year, month, day] = dateStr.split('-').map(Number)
  const d =
    Number.isFinite(year) && Number.isFinite(month) && Number.isFinite(day)
      ? new Date(year, month - 1, day)
      : new Date(dateStr)
  return d.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

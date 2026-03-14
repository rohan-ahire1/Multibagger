export const COLORS = {
  green: '#26A69A',
  greenBg: 'rgba(38, 166, 154, 0.125)',
  red: '#EF5350',
  redBg: 'rgba(239, 83, 80, 0.125)',
  amber: '#F5A623',
  amberBg: 'rgba(245, 166, 35, 0.125)',
  blue: '#3B82F6',
  blueBg: 'rgba(59, 130, 246, 0.125)',
  purple: '#8B5CF6',
  purpleBg: 'rgba(139, 92, 246, 0.125)',
  candleBull: 'rgba(38,166,154,0.9)',
  candleBear: 'rgba(239,83,80,0.9)',
  sma50: '#e8b03e',
  sma150: '#3b82f6',
  sma200: '#ef4444',
  linePivot: '#2563eb',
  lineEntry: '#16a34a',
  lineStop: '#dc2626',
  lineExtended: '#f59e0b',
} as const

export const pnlColor = (value: number) =>
  value >= 0 ? COLORS.green : COLORS.red

export const rsColor = (rank: number) =>
  rank >= 85 ? COLORS.green : rank >= 70 ? COLORS.amber : COLORS.red

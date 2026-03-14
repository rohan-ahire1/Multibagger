import { useMemo } from 'react'
import { useSymbolSignalHistory } from '@/api/useSignalHistory'
import { useBacktestStore } from '@/stores/backtest'
import { useUIStore } from '@/stores/ui'
import { StatusPill } from '@/components/shared/StatusPill'
import { formatPrice } from '@/lib/formatters'
import type { BacktestTrade } from '@/lib/types'

interface BacktestHistoryProps {
  symbol: string | null | undefined
}

function tradeStatusVariant(status: BacktestTrade['status']): 'green' | 'red' | 'amber' | 'blue' {
  switch (status) {
    case 'target':
      return 'green'
    case 'stopped':
      return 'red'
    case 'time_stop':
      return 'amber'
    default:
      return 'blue'
  }
}

function tradeStatusLabel(status: BacktestTrade['status']): string {
  switch (status) {
    case 'target':
      return 'Target'
    case 'stopped':
      return 'Stopped'
    case 'time_stop':
      return 'Time'
    case 'not_filled':
      return 'No Fill'
    case 'incomplete_data':
      return 'Incomplete'
    case 'skipped_overlap':
      return 'Skipped'
    default:
      return status
  }
}

function rColor(r: number | null): string {
  if (r === null) return 'text-text-dim'
  if (r > 0) return 'text-green'
  if (r < 0) return 'text-red'
  return 'text-text-dim'
}

function formatR(r: number | null): string {
  if (r === null) return '—'
  const sign = r >= 0 ? '+' : ''
  return `${sign}${r.toFixed(1)}R`
}

export function BacktestHistory({ symbol }: BacktestHistoryProps) {
  const { data } = useSymbolSignalHistory(symbol ?? undefined)
  const selectTrade = useBacktestStore((s) => s.selectTrade)
  const setWorkspace = useUIStore((s) => s.setWorkspace)

  const trades = data?.trades ?? []
  const signals = data?.signals ?? []

  const summary = useMemo(() => {
    const closed = trades.filter(
      (t) => t.status === 'stopped' || t.status === 'target' || t.status === 'time_stop'
    )
    const wins = closed.filter((t) => t.rMultiple !== null && t.rMultiple > 0)
    const losses = closed.filter((t) => t.rMultiple !== null && t.rMultiple <= 0)
    const winRate = closed.length > 0 ? Math.round((wins.length / closed.length) * 100) : 0
    const avgR =
      closed.length > 0
        ? closed.reduce((sum, t) => sum + (t.rMultiple ?? 0), 0) / closed.length
        : 0
    return { wins: wins.length, losses: losses.length, winRate, avgR }
  }, [trades])

  const lastSignal = useMemo(() => {
    if (signals.length === 0) return null
    const sorted = [...signals].sort((a, b) => b.signalDate.localeCompare(a.signalDate))
    return sorted[0]
  }, [signals])

  const displayTrades = trades.slice(0, 10)

  function handleTradeClick(trade: BacktestTrade) {
    if (!symbol) return
    selectTrade({ symbol, signalDate: trade.signalDate })
    setWorkspace(4)
  }

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        Backtest history (this stock)
      </h3>

      {/* Summary line */}
      {trades.length > 0 ? (
        <div className="text-[11px] text-text-secondary mb-2 tabular-nums">
          {summary.wins}W / {summary.losses}L &mdash; WR {summary.winRate}% &mdash; Avg{' '}
          <span className={rColor(summary.avgR)}>{formatR(summary.avgR)}</span>
        </div>
      ) : null}

      {/* Trades table */}
      {displayTrades.length > 0 ? (
        <div className="max-h-[200px] overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-text-dim border-b border-border-default">
                <th className="text-left pb-1 font-normal">Signal</th>
                <th className="text-right pb-1 font-normal">Entry</th>
                <th className="text-right pb-1 font-normal">Exit</th>
                <th className="text-right pb-1 font-normal">R</th>
                <th className="text-right pb-1 font-normal">Status</th>
              </tr>
            </thead>
            <tbody>
              {displayTrades.map((trade) => (
                <tr
                  key={trade.signalDate}
                  className="border-b border-border-default/50 hover:bg-bg-elevated cursor-pointer transition-colors"
                  onClick={() => handleTradeClick(trade)}
                >
                  <td className="py-1 text-text-dim tabular-nums">{trade.signalDate}</td>
                  <td className="py-1 text-right text-text-secondary tabular-nums">
                    {trade.entryPrice !== null ? formatPrice(trade.entryPrice) : '—'}
                  </td>
                  <td className="py-1 text-right text-text-secondary tabular-nums">
                    {trade.exitPrice !== null ? formatPrice(trade.exitPrice) : '—'}
                  </td>
                  <td className={`py-1 text-right tabular-nums ${rColor(trade.rMultiple)}`}>
                    {formatR(trade.rMultiple)}
                  </td>
                  <td className="py-1 text-right">
                    <StatusPill
                      label={tradeStatusLabel(trade.status)}
                      variant={tradeStatusVariant(trade.status)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-text-dim italic">
          (run backtest to see trade details)
        </p>
      )}

      {/* Last signal */}
      {lastSignal && (
        <div className="mt-2 text-[11px] text-text-dim">
          Last: {lastSignal.signalType} on {lastSignal.signalDate}
        </div>
      )}
    </div>
  )
}

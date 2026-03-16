import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import type { BacktestTrade } from '@/lib/types'
import { formatPrice, formatR, formatDate, formatPct } from '@/lib/formatters'
import { StatusPill } from '@/components/shared/StatusPill'
import { useBacktestStore } from '@/stores/backtest'

interface TradeListProps {
  trades: BacktestTrade[]
}

const columnHelper = createColumnHelper<BacktestTrade>()

type StatusPillVariant = 'green' | 'red' | 'amber' | 'blue' | 'purple'

function statusPillProps(status: BacktestTrade['status']): { label: string; variant: StatusPillVariant } {
  switch (status) {
    case 'target':
      return { label: 'Target', variant: 'green' }
    case 'stopped':
      return { label: 'Stopped', variant: 'red' }
    case 'time_stop':
      return { label: 'Time Stop', variant: 'amber' }
    case 'not_filled':
      return { label: 'Not Filled', variant: 'blue' }
    case 'incomplete_data':
      return { label: 'No Data', variant: 'blue' }
    case 'skipped_overlap':
      return { label: 'Skipped', variant: 'blue' }
    case 'skipped_split':
      return { label: 'Split Skip', variant: 'purple' }
    case 'dead_money':
      return { label: 'Dead $', variant: 'amber' }
    case 'skipped_portfolio':
      return { label: 'Skipped', variant: 'blue' }
    default:
      return { label: status, variant: 'blue' }
  }
}

const columns = [
  columnHelper.accessor('symbol', {
    header: 'Symbol',
    size: 80,
    cell: (info) => (
      <span className="text-[12px] font-medium text-white">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor('signalDate', {
    header: 'Signal',
    size: 90,
    cell: (info) => (
      <span className="tabular-nums text-zinc-400">{formatDate(info.getValue())}</span>
    ),
  }),
  columnHelper.accessor('returnPct', {
    header: 'Return %',
    size: 78,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      if (val == null) return <span className="text-zinc-500">—</span>
      const color = val >= 0 ? 'text-green-400' : 'text-red-400'
      return <span className={`tabular-nums font-mono font-semibold ${color}`}>{formatPct(val)}</span>
    },
  }),
  columnHelper.accessor('rMultiple', {
    header: 'R-Multiple',
    size: 82,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      if (val == null) return <span className="text-zinc-500">—</span>
      const color = val >= 0 ? 'text-green-400' : 'text-red-400'
      return <span className={`tabular-nums font-mono font-semibold ${color}`}>{formatR(val)}</span>
    },
  }),
  columnHelper.accessor('holdingDays', {
    header: 'Days',
    size: 52,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return <span className="tabular-nums text-zinc-300">{val ?? '—'}</span>
    },
  }),
  columnHelper.accessor('status', {
    header: 'Status',
    size: 80,
    cell: (info) => {
      const { label, variant } = statusPillProps(info.getValue())
      return <StatusPill label={label} variant={variant} />
    },
  }),
  columnHelper.accessor('exitPhase', {
    header: 'Phase',
    size: 65,
    cell: (info) => {
      const val = info.getValue()
      if (!val) return <span className="text-zinc-500">—</span>
      const labels: Record<string, string> = {
        initial_stop: 'INIT',
        breakeven: 'BE',
        partial_profit: 'PARTIAL',
        trailing: 'TRAIL',
        tighten: 'TIGHT',
        exit: 'EXIT',
      }
      return <span className="text-[11px] font-mono text-zinc-400">{labels[val] ?? val}</span>
    },
  }),
  columnHelper.accessor('entryPrice', {
    header: 'Entry',
    size: 70,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return (
        <span className="tabular-nums text-zinc-300">
          {val != null ? formatPrice(val) : '—'}
        </span>
      )
    },
  }),
  columnHelper.accessor('exitPrice', {
    header: 'Exit',
    size: 70,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return (
        <span className="tabular-nums text-zinc-300">
          {val != null ? formatPrice(val) : '—'}
        </span>
      )
    },
  }),
  columnHelper.accessor('exitReason', {
    header: 'Reason',
    size: 85,
    cell: (info) => {
      const val = info.getValue()
      if (!val) return <span className="text-zinc-500">—</span>
      return <span className="text-[11px] font-mono text-zinc-400">{val}</span>
    },
  }),
]

export function TradeList({ trades }: TradeListProps) {
  const { selectedTrade, selectTrade } = useBacktestStore()

  const [sorting, setSorting] = useState<SortingState>([
    { id: 'signalDate', desc: true },
  ])

  const table = useReactTable({
    data: trades,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (trades.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500 text-xs">
        No trades
      </div>
    )
  }

  return (
    <div className="overflow-y-auto flex-1">
      <table className="w-full">
        <thead className="sticky top-0 z-10">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="bg-zinc-900/90">
              {headerGroup.headers.map((header) => {
                const align =
                  (header.column.columnDef.meta as { align?: string })?.align ?? 'left'
                return (
                  <th
                    key={header.id}
                    className={`text-[10px] uppercase tracking-wider text-zinc-500 px-3 py-2 font-normal cursor-pointer select-none hover:text-zinc-300 transition-colors ${
                      align === 'right' ? 'text-right' : 'text-left'
                    }`}
                    style={{ width: header.getSize() }}
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <span className="flex items-center gap-1 justify-start">
                      {align === 'right' && (
                        <span className="text-[9px] text-zinc-600">
                          {header.column.getIsSorted() === 'asc'
                            ? '▲'
                            : header.column.getIsSorted() === 'desc'
                            ? '▼'
                            : ''}
                        </span>
                      )}
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                      {align !== 'right' && (
                        <span className="text-[9px] text-zinc-600">
                          {header.column.getIsSorted() === 'asc'
                            ? '▲'
                            : header.column.getIsSorted() === 'desc'
                            ? '▼'
                            : ''}
                        </span>
                      )}
                    </span>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const trade = row.original
            const isSelected =
              selectedTrade?.symbol === trade.symbol &&
              selectedTrade?.signalDate === trade.signalDate
            const pnl = trade.rMultiple ?? trade.returnPct ?? 0
            const tone = pnl > 0
              ? (isSelected ? 'bg-emerald-950/60' : 'bg-emerald-950/25 hover:bg-emerald-950/40')
              : pnl < 0
              ? (isSelected ? 'bg-red-950/60' : 'bg-red-950/20 hover:bg-red-950/35')
              : (isSelected ? 'bg-zinc-800' : 'hover:bg-zinc-900')
            return (
              <tr
                key={`${trade.symbol}-${trade.signalDate}`}
                onClick={() => selectTrade({ symbol: trade.symbol, signalDate: trade.signalDate })}
                className={`cursor-pointer text-xs transition-colors ${tone}`}
                style={{ height: 34 }}
              >
                {row.getVisibleCells().map((cell) => {
                  const align =
                    (cell.column.columnDef.meta as { align?: string })?.align ?? 'left'
                  return (
                    <td
                      key={cell.id}
                      className={`px-3 py-1.5 ${align === 'right' ? 'text-right' : 'text-left'}`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

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
import { formatPrice, formatR, formatDate } from '@/lib/formatters'
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
  columnHelper.accessor('rMultiple', {
    header: 'R',
    size: 50,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      if (val == null) return <span className="text-zinc-500">—</span>
      const color = val >= 0 ? 'text-green-400' : 'text-red-400'
      return <span className={`tabular-nums font-mono ${color}`}>{formatR(val)}</span>
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
            return (
              <tr
                key={`${trade.symbol}-${trade.signalDate}`}
                onClick={() => selectTrade({ symbol: trade.symbol, signalDate: trade.signalDate })}
                className={`cursor-pointer text-xs transition-colors ${
                  isSelected ? 'bg-zinc-800' : 'hover:bg-zinc-900'
                }`}
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

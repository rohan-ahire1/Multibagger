import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import type { SetupRow } from '@/lib/types'
import { formatPrice, formatVolMult } from '@/lib/formatters'
import { StatusPill } from '@/components/shared/StatusPill'

interface ScannerTableProps {
  rows: SetupRow[]
  selectedId: string | null
  onSelect: (id: string) => void
  isLoading?: boolean
}

const columnHelper = createColumnHelper<SetupRow>()

const columns = [
  columnHelper.accessor('symbol', {
    header: 'Symbol',
    size: 120,
    cell: (info) => (
      <span className="text-[13px] font-medium text-white">
        {info.getValue()}
      </span>
    ),
  }),
  columnHelper.accessor('signalType', {
    header: 'Signal',
    size: 70,
    cell: (info) => {
      const val = info.getValue()
      if (val === 'BREAKOUT') return <StatusPill label="BREAK" variant="green" />
      if (val === 'WATCH') return <StatusPill label="WATCH" variant="amber" />
      return <span className="text-text-dim">&mdash;</span>
    },
  }),
  columnHelper.accessor('close', {
    header: 'Close',
    size: 80,
    meta: { align: 'right' },
    cell: (info) => (
      <span className="tabular-nums">{formatPrice(info.getValue())}</span>
    ),
  }),
  columnHelper.accessor('pivot', {
    header: 'Pivot',
    size: 80,
    meta: { align: 'right' },
    cell: (info) => (
      <span className="tabular-nums">{formatPrice(info.getValue())}</span>
    ),
  }),
  columnHelper.accessor('rsRank', {
    header: 'RS',
    size: 50,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      const color =
        val >= 85 ? 'text-green' : val >= 70 ? 'text-amber' : 'text-red'
      return <span className={`tabular-nums ${color}`}>{Math.round(val)}</span>
    },
  }),
  columnHelper.accessor('volMult', {
    header: 'Vol',
    size: 60,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return (
        <span className={`tabular-nums ${val >= 1.5 ? 'text-green' : ''}`}>
          {formatVolMult(val)}
        </span>
      )
    },
  }),
  columnHelper.accessor('contractionCount', {
    header: 'Contr.',
    size: 50,
    meta: { align: 'right' },
    cell: (info) => {
      const val = info.getValue()
      return (
        <span className="tabular-nums text-text-secondary">
          {val != null ? val : '\u2014'}
        </span>
      )
    },
  }),
]

export function ScannerTable({ rows, selectedId, onSelect, isLoading }: ScannerTableProps) {
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
  })

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 border-2 border-text-dim border-t-green rounded-full animate-spin" />
          Loading signals...
        </div>
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
        No signals for this date
      </div>
    )
  }

  return (
    <div className="overflow-y-auto flex-1">
      <table className="w-full">
        <thead className="sticky top-0 z-10">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="bg-bg-secondary">
              {headerGroup.headers.map((header) => {
                const align =
                  (header.column.columnDef.meta as { align?: string })?.align ??
                  'left'
                return (
                  <th
                    key={header.id}
                    className={`text-[10px] uppercase tracking-wider text-text-faint px-3 py-2 font-normal ${
                      align === 'right' ? 'text-right' : 'text-left'
                    }`}
                    style={{ width: header.getSize() }}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const isSelected = row.id === selectedId
            return (
              <tr
                key={row.id}
                onClick={() => onSelect(row.id)}
                className={`cursor-pointer text-xs transition-colors ${
                  isSelected
                    ? 'bg-green/[0.08]'
                    : 'hover:bg-bg-elevated'
                }`}
                style={{ height: 36 }}
              >
                {row.getVisibleCells().map((cell) => {
                  const align =
                    (cell.column.columnDef.meta as { align?: string })?.align ??
                    'left'
                  return (
                    <td
                      key={cell.id}
                      className={`px-3 py-1.5 ${
                        align === 'right' ? 'text-right' : 'text-left'
                      }`}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
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

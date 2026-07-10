import { Command } from 'cmdk'
import { useNavigate } from 'react-router'
import { useMemo } from 'react'
import { LayoutDashboard, ScanSearch, FlaskConical, TrendingUp } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useSignals, useWatchlist } from '@/api/hooks'

export function CommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen)
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen)
  const scanDate = useUIStore((s) => s.scanDate)
  const navigate = useNavigate()

  const { data: signals } = useSignals(open ? scanDate : null)
  const { data: watchlist } = useWatchlist(open ? scanDate : null)

  const symbols = useMemo(() => {
    const set = new Set<string>()
    signals?.forEach((s) => set.add(s.symbol))
    watchlist?.forEach((w) => set.add(w.symbol))
    return [...set].sort()
  }, [signals, watchlist])

  const go = (path: string) => {
    setOpen(false)
    navigate(path)
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-ink/20 pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <div onClick={(e) => e.stopPropagation()} className="w-[560px]">
        <Command
          label="Command palette"
          className="overflow-hidden rounded-xl border border-border bg-surface shadow-2xl"
        >
          <Command.Input
            autoFocus
            placeholder="Jump to view or symbol…"
            className="w-full border-b border-border bg-transparent px-4 py-3 text-sm outline-none placeholder:text-ink-faint"
          />
          <Command.List className="max-h-[320px] overflow-y-auto p-1.5">
            <Command.Empty className="py-6 text-center text-sm text-ink-faint">
              No results
            </Command.Empty>

            <Command.Group
              heading="Views"
              className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-faint"
            >
              <PaletteItem onSelect={() => go('/')} icon={<LayoutDashboard className="size-4" />}>
                Dashboard
              </PaletteItem>
              <PaletteItem
                onSelect={() => go('/scanner')}
                icon={<ScanSearch className="size-4" />}
              >
                Scanner
              </PaletteItem>
              <PaletteItem
                onSelect={() => go('/backtest')}
                icon={<FlaskConical className="size-4" />}
              >
                Backtest
              </PaletteItem>
            </Command.Group>

            {symbols.length > 0 && (
              <Command.Group
                heading={`Symbols (${scanDate ?? ''})`}
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-faint"
              >
                {symbols.map((sym) => (
                  <PaletteItem
                    key={sym}
                    onSelect={() => go(`/symbol/${sym}`)}
                    icon={<TrendingUp className="size-4" />}
                  >
                    {sym}
                  </PaletteItem>
                ))}
              </Command.Group>
            )}
          </Command.List>
        </Command>
      </div>
    </div>
  )
}

function PaletteItem({
  children,
  icon,
  onSelect,
}: {
  children: React.ReactNode
  icon: React.ReactNode
  onSelect: () => void
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm text-ink-soft data-[selected=true]:bg-accent-soft data-[selected=true]:text-accent"
    >
      <span className="text-ink-faint">{icon}</span>
      {children}
    </Command.Item>
  )
}

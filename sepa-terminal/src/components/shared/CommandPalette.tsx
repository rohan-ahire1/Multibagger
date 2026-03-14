import { useEffect, useCallback } from 'react'
import { Command } from 'cmdk'
import {
  Search,
  LayoutDashboard,
  Filter,
  Play,
  X,
} from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useScannerStore } from '@/stores/scanner'

export function CommandPalette() {
  const setWorkspace = useUIStore((s) => s.setWorkspace)
  const setSectorFilter = useScannerStore((s) => s.setSectorFilter)
  const setRsFilter = useScannerStore((s) => s.setRsFilter)

  const close = useCallback(() => {
    useUIStore.setState({ commandPaletteOpen: false })
  }, [])

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [close])

  const runAction = (action: () => void) => {
    action()
    close()
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) close()
      }}
    >
      <div className="mt-20 h-fit w-[560px]">
        <Command
          className="bg-bg-surface border border-border-strong rounded-xl shadow-2xl overflow-hidden"
          label="Command Palette"
        >
          {/* Input area */}
          <div className="flex items-center gap-3 px-4 border-b border-border-strong">
            <Search className="w-4 h-4 text-text-muted shrink-0" />
            <Command.Input
              className="flex-1 bg-transparent text-[15px] text-text-primary placeholder:text-text-dim outline-none border-none py-3.5 w-full"
              placeholder="Search symbols, actions, views..."
              autoFocus
            />
            <button
              onClick={close}
              className="text-[10px] text-text-faint bg-bg-elevated border border-border-strong rounded px-1.5 py-0.5 shrink-0 hover:text-text-muted transition-colors"
            >
              ESC
            </button>
          </div>

          {/* Results area */}
          <Command.List className="max-h-[360px] overflow-y-auto py-2">
            <Command.Empty className="px-4 py-8 text-center text-text-dim text-sm">
              No results found.
            </Command.Empty>

            {/* Navigation group */}
            <Command.Group
              heading="Navigation"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-text-faint [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => setWorkspace(1))}
                value="go to market scanner"
              >
                <LayoutDashboard className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Go to Market + Scanner</span>
                  <span className="text-[11px] text-text-dim">Switch to workspace 1</span>
                </div>
              </Command.Item>

              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => setWorkspace(2))}
                value="go to analysis"
              >
                <LayoutDashboard className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Go to Analysis</span>
                  <span className="text-[11px] text-text-dim">Switch to workspace 2</span>
                </div>
              </Command.Item>

              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => setWorkspace(3))}
                value="go to portfolio"
              >
                <LayoutDashboard className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Go to Portfolio</span>
                  <span className="text-[11px] text-text-dim">Switch to workspace 3</span>
                </div>
              </Command.Item>
            </Command.Group>

            <Command.Separator className="h-px bg-border-default mx-4 my-1" />

            {/* Actions group */}
            <Command.Group
              heading="Actions"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-text-faint [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() =>
                  runAction(() => {
                    setSectorFilter(null)
                    setRsFilter(null)
                  })
                }
                value="clear all filters"
              >
                <X className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Clear all filters</span>
                  <span className="text-[11px] text-text-dim">Reset sector and RS filters</span>
                </div>
              </Command.Item>

              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => setRsFilter(80))}
                value="filter rs greater than 80"
              >
                <Filter className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Filter: RS &gt; 80</span>
                  <span className="text-[11px] text-text-dim">Show stocks with RS rating above 80</span>
                </div>
              </Command.Item>

              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => setRsFilter(90))}
                value="filter rs greater than 90"
              >
                <Filter className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Filter: RS &gt; 90</span>
                  <span className="text-[11px] text-text-dim">Show stocks with RS rating above 90</span>
                </div>
              </Command.Item>
            </Command.Group>

            <Command.Separator className="h-px bg-border-default mx-4 my-1" />

            {/* Quick Commands group */}
            <Command.Group
              heading="Quick Commands"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-text-faint [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => {})}
                value="run scanner for today"
              >
                <Play className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Run scanner for today</span>
                  <span className="text-[11px] text-text-dim">Execute scanner with current date</span>
                </div>
              </Command.Item>

              <Command.Item
                className="px-4 py-2.5 flex items-center gap-3 cursor-pointer rounded-lg mx-2 text-text-secondary data-[selected=true]:bg-bg-elevated data-[selected=true]:border-l-2 data-[selected=true]:border-green"
                onSelect={() => runAction(() => {})}
                value="run backtest"
              >
                <Play className="w-4 h-4 text-text-muted shrink-0" />
                <div className="flex flex-col">
                  <span className="text-sm text-text-primary">Run backtest</span>
                  <span className="text-[11px] text-text-dim">Run backtesting simulation</span>
                </div>
              </Command.Item>
            </Command.Group>
          </Command.List>

          {/* Footer */}
          <div className="flex items-center gap-4 px-4 py-2 border-t border-border-strong text-[11px] text-text-dim">
            <span>
              <kbd className="bg-bg-elevated border border-border-strong rounded px-1 py-0.5 text-[10px] mr-1">↑↓</kbd>
              navigate
            </span>
            <span>
              <kbd className="bg-bg-elevated border border-border-strong rounded px-1 py-0.5 text-[10px] mr-1">Enter</kbd>
              select
            </span>
            <span>
              <kbd className="bg-bg-elevated border border-border-strong rounded px-1 py-0.5 text-[10px] mr-1">Esc</kbd>
              close
            </span>
          </div>
        </Command>
      </div>
    </div>
  )
}

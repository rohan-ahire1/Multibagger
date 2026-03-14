import type { ChartTimeframe, ChartOverlays } from '@/lib/types'

interface ChartToolbarProps {
  timeframe: ChartTimeframe
  overlays: ChartOverlays
  onTimeframeChange: (tf: ChartTimeframe) => void
  onOverlayToggle: (key: keyof ChartOverlays) => void
}

function ToolbarButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
        active
          ? 'bg-green-bg text-text-primary'
          : 'text-text-dim hover:text-text-muted'
      }`}
    >
      {label}
    </button>
  )
}

function Divider() {
  return <div className="w-px h-3.5 bg-border-strong" />
}

export function ChartToolbar({
  timeframe,
  overlays,
  onTimeframeChange,
  onOverlayToggle,
}: ChartToolbarProps) {
  return (
    <div className="flex items-center gap-1 px-2 py-1.5">
      {/* Timeframe group */}
      <ToolbarButton
        label="D"
        active={timeframe === 'D'}
        onClick={() => onTimeframeChange('D')}
      />
      <ToolbarButton
        label="W"
        active={timeframe === 'W'}
        onClick={() => onTimeframeChange('W')}
      />

      <Divider />

      {/* MA toggles */}
      <ToolbarButton
        label="50"
        active={overlays.sma50}
        onClick={() => onOverlayToggle('sma50')}
      />
      <ToolbarButton
        label="150"
        active={overlays.sma150}
        onClick={() => onOverlayToggle('sma150')}
      />
      <ToolbarButton
        label="200"
        active={overlays.sma200}
        onClick={() => onOverlayToggle('sma200')}
      />

      <Divider />

      {/* Ref lines & RS line */}
      <ToolbarButton
        label="Ref"
        active={overlays.refLines}
        onClick={() => onOverlayToggle('refLines')}
      />
      <ToolbarButton
        label="RS"
        active={overlays.rsLine}
        onClick={() => onOverlayToggle('rsLine')}
      />
    </div>
  )
}

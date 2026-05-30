import { AlertTriangle, Check, Crosshair } from 'lucide-react'
import type { ExplanationItem, ExplanationMetric } from '@/lib/types'

interface QualificationPanelProps {
  narrative?: string
  strengths: ExplanationItem[]
  risks: ExplanationItem[]
  nextActions?: ExplanationItem[]
  metrics?: ExplanationMetric[]
}

function ItemList({ items, tone }: { items: ExplanationItem[]; tone: 'green' | 'amber' | 'blue' }) {
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, i) => (
        <div key={`${item.text}-${i}`} className="flex items-start gap-2">
          <span
            className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${
              tone === 'green' ? 'bg-green' : tone === 'amber' ? 'bg-amber' : 'bg-blue'
            }`}
          />
          <div className="min-w-0">
            <div className="text-[12px] text-text-primary leading-snug">{item.text}</div>
            <div className="text-[11px] text-text-dim leading-snug mt-0.5">{item.detail}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function QualificationPanel({
  narrative,
  strengths,
  risks,
  nextActions = [],
  metrics = [],
}: QualificationPanelProps) {
  return (
    <div className="flex flex-col gap-4">
      {narrative && (
        <div className="text-[12px] leading-relaxed text-text-secondary">
          {narrative}
        </div>
      )}

      {metrics.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded border border-border-default px-2 py-1.5">
              <div className="text-[10px] uppercase text-text-faint">{metric.label}</div>
              <div className="text-[13px] text-text-primary tabular-nums">{metric.value}</div>
              <div className="text-[10px] text-text-dim truncate">{metric.context}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <Check className="w-3.5 h-3.5 text-green" />
            <span className="text-[11px] uppercase font-medium text-green">
              Why it qualifies
            </span>
          </div>
          <ItemList items={strengths} tone="green" />
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-amber" />
            <span className="text-[11px] uppercase font-medium text-amber">
              Risk flags
            </span>
          </div>
          <ItemList items={risks} tone="amber" />
        </div>
      </div>

      {nextActions.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-border-default pt-3">
          <div className="flex items-center gap-1.5">
            <Crosshair className="w-3.5 h-3.5 text-blue" />
            <span className="text-[11px] uppercase font-medium text-blue">
              Next action
            </span>
          </div>
          <ItemList items={nextActions} tone="blue" />
        </div>
      )}
    </div>
  )
}

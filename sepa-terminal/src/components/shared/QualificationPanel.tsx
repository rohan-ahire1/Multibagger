import { Check, AlertTriangle } from 'lucide-react'

interface QualificationPanelProps {
  passItems: Array<{ text: string }>
  warnItems: Array<{ text: string }>
}

export function QualificationPanel({ passItems, warnItems }: QualificationPanelProps) {
  return (
    <div className="grid grid-cols-2 gap-4">
      {/* Left column — Why it qualifies */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Check className="w-3.5 h-3.5 text-green" />
          <span className="text-[11px] uppercase font-medium text-green">
            Why it qualifies
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          {passItems.map((item, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="mt-1.5 w-1 h-1 rounded-full bg-green shrink-0" />
              <span className="text-[11px] text-text-muted">{item.text}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Right column — Risk flags */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 text-amber" />
          <span className="text-[11px] uppercase font-medium text-amber">
            Risk flags
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          {warnItems.map((item, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="mt-1.5 w-1 h-1 rounded-full bg-amber shrink-0" />
              <span className="text-[11px] text-text-muted">{item.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { ParamSpec } from '@/lib/lab-types'
import { cn } from '@/lib/utils'

type Values = Record<string, unknown>

/**
 * Renders a strategy's parameters from its ParamSpec metadata alone.
 *
 * Nothing here knows about any particular strategy: a new strategy appears
 * with a working form as soon as it declares its specs on the Python side.
 */
export function ParamForm({
  specs,
  values,
  onChange,
}: {
  specs: ParamSpec[]
  values: Values
  onChange: (next: Values) => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  const groups = useMemo(() => {
    const byGroup = new Map<string, ParamSpec[]>()
    for (const spec of specs) {
      const list = byGroup.get(spec.group) ?? []
      list.push(spec)
      byGroup.set(spec.group, list)
    }
    return [...byGroup.entries()]
  }, [specs])

  const hasAdvanced = specs.some((s) => s.advanced)

  const set = (name: string, value: unknown) => onChange({ ...values, [name]: value })

  return (
    <div className="space-y-4">
      {groups.map(([group, groupSpecs]) => {
        const visible = groupSpecs.filter((s) => showAdvanced || !s.advanced)
        if (visible.length === 0) return null
        return (
          <fieldset key={group} className="space-y-2">
            <legend className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
              {group}
            </legend>
            <div className="grid grid-cols-2 gap-2.5">
              {visible.map((spec) => (
                <ParamField
                  key={spec.name}
                  spec={spec}
                  value={values[spec.name]}
                  onChange={(v) => set(spec.name, v)}
                />
              ))}
            </div>
          </fieldset>
        )
      })}

      {hasAdvanced && (
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1 text-[11px] text-ink-faint hover:text-ink"
        >
          {showAdvanced ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
          {showAdvanced ? 'Hide' : 'Show'} advanced
        </button>
      )}
    </div>
  )
}

function ParamField({
  spec,
  value,
  onChange,
}: {
  spec: ParamSpec
  value: unknown
  onChange: (v: unknown) => void
}) {
  const inputClass =
    'h-8 w-full rounded-lg border border-border bg-surface-2/40 px-2 text-xs outline-none ' +
    'hover:border-border-strong focus:border-accent focus:bg-surface'

  // Guard the numeric bounds client-side so an out-of-range value is visible
  // before the request round-trips and comes back a 422.
  const numeric = spec.type === 'int' || spec.type === 'float'
  const outOfRange =
    numeric &&
    typeof value === 'number' &&
    ((spec.min !== null && value < spec.min) || (spec.max !== null && value > spec.max))

  return (
    <label className="flex flex-col gap-1" title={spec.help || undefined}>
      <span className="text-[10px] text-ink-faint">{spec.label}</span>

      {spec.type === 'bool' ? (
        <button
          type="button"
          role="switch"
          aria-checked={value === true}
          onClick={() => onChange(!value)}
          className={cn(
            'h-8 rounded-lg border px-2 text-left text-xs',
            value
              ? 'border-accent/40 bg-accent-soft text-accent'
              : 'border-border bg-surface-2/40 text-ink-faint'
          )}
        >
          {value ? 'On' : 'Off'}
        </button>
      ) : spec.type === 'enum' ? (
        <select
          className={inputClass}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {(spec.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : spec.type === 'date' ? (
        <input
          type="date"
          className={inputClass}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : numeric ? (
        <input
          type="number"
          className={cn(inputClass, 'tnum', outOfRange && 'border-danger text-danger')}
          value={value === null || value === undefined ? '' : String(value)}
          min={spec.min ?? undefined}
          max={spec.max ?? undefined}
          step={spec.step ?? (spec.type === 'int' ? 1 : 'any')}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') return onChange(null)
            const parsed = spec.type === 'int' ? parseInt(raw, 10) : parseFloat(raw)
            onChange(Number.isNaN(parsed) ? null : parsed)
          }}
        />
      ) : (
        <input
          type="text"
          className={inputClass}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      )}

      {outOfRange && (
        <span className="text-[10px] text-danger">
          must be between {spec.min ?? '-∞'} and {spec.max ?? '∞'}
        </span>
      )}
    </label>
  )
}

/** True when every numeric value sits inside its declared bounds. */
export function paramsAreValid(specs: ParamSpec[], values: Values): boolean {
  return specs.every((spec) => {
    const value = values[spec.name]
    if (spec.type !== 'int' && spec.type !== 'float') return true
    if (typeof value !== 'number') return value === null || value === undefined
    if (spec.min !== null && value < spec.min) return false
    if (spec.max !== null && value > spec.max) return false
    return true
  })
}

const variantStyles: Record<string, string> = {
  green: 'bg-green-bg text-green',
  red: 'bg-red-bg text-red',
  amber: 'bg-amber-bg text-amber',
  blue: 'bg-blue-bg text-blue',
  purple: 'bg-purple-bg text-purple',
}

interface StatusPillProps {
  label: string
  variant: 'green' | 'red' | 'amber' | 'blue' | 'purple'
}

export function StatusPill({ label, variant }: StatusPillProps) {
  return (
    <span
      className={`text-[11px] font-medium px-2 py-0.5 rounded ${variantStyles[variant]}`}
    >
      {label}
    </span>
  )
}

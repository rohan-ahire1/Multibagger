import { useCallback } from 'react'
import { useReviewsStore } from '@/stores/reviews'

interface TradeThesisProps {
  symbol: string
  scanDate: string
}

export function TradeThesis({ symbol, scanDate }: TradeThesisProps) {
  const thesis = useReviewsStore(
    (s) => s.reviews[`${symbol}:${scanDate}`]?.thesis ?? ''
  )
  const setThesis = useReviewsStore((s) => s.setThesis)

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setThesis(symbol, scanDate, e.target.value)
    },
    [symbol, scanDate, setThesis]
  )

  return (
    <div>
      <h3 className="text-xs font-medium text-text-primary mb-2">
        Trade thesis
      </h3>
      <textarea
        data-thesis-input
        placeholder="Why am I taking this trade? What would invalidate it?"
        value={thesis}
        onChange={handleChange}
        className="bg-bg-surface border border-border-subtle rounded-md min-h-[48px] p-2 text-xs text-text-muted focus:text-text-secondary w-full resize-none focus:border-green/50 focus:outline-none"
      />
    </div>
  )
}

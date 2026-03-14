import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { OHLCVBar } from '@/lib/types'

export function useOHLCV(symbol: string | null) {
  return useQuery({
    queryKey: ['ohlcv', symbol],
    queryFn: () => apiFetch<OHLCVBar[]>(`/ohlcv/${symbol}`),
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}

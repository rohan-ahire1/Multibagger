import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { snakeToCamel } from '@/lib/case'
import type { SymbolSignalHistory } from '@/lib/types'

export function useSymbolSignalHistory(symbol: string | undefined) {
  return useQuery<SymbolSignalHistory>({
    queryKey: ['signal-history', symbol],
    queryFn: async () => {
      const raw = await apiFetch<unknown>(`/analysis/${symbol}/signal-history`)
      return snakeToCamel(raw) as SymbolSignalHistory
    },
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  })
}

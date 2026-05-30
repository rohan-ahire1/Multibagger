import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { snakeToCamel } from '@/lib/case'
import type { Signal, WatchlistCandidate } from '@/lib/types'

export function useSignals(date: string) {
  return useQuery({
    queryKey: ['signals', date],
    queryFn: async () => {
      const raw = await apiFetch<unknown>(`/scanner/signals?date=${date}`)
      return snakeToCamel(raw) as Signal[]
    },
    enabled: Boolean(date),
    staleTime: 5 * 60 * 1000,
  })
}

export function useWatchlist(date: string) {
  return useQuery({
    queryKey: ['watchlist', date],
    queryFn: async () => {
      const raw = await apiFetch<unknown>(`/scanner/watchlist?date=${date}`)
      return snakeToCamel(raw) as WatchlistCandidate[]
    },
    enabled: Boolean(date),
    staleTime: 5 * 60 * 1000,
  })
}

export function useScanDates() {
  return useQuery({
    queryKey: ['scan-dates'],
    queryFn: () => apiFetch<string[]>('/scanner/dates'),
    staleTime: 60 * 1000,
  })
}

export interface MarketBreadth {
  date: string
  aboveSma50: number
  totalEligible: number
  breadth: number
  regime: 'Strong' | 'Weak' | 'Neutral'
  marketFilterOn: boolean
}

export function useMarketBreadth(date: string) {
  return useQuery({
    queryKey: ['market-breadth', date],
    queryFn: () => apiFetch<MarketBreadth>(`/scanner/breadth?date=${date}`),
    enabled: Boolean(date),
    staleTime: 5 * 60 * 1000,
  })
}


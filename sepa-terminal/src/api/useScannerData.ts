import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { Signal, WatchlistCandidate } from '@/lib/types'

export function useSignals(date: string) {
  return useQuery({
    queryKey: ['signals', date],
    queryFn: () => apiFetch<Signal[]>(`/scanner/signals?date=${date}`),
    staleTime: 5 * 60 * 1000,
  })
}

export function useWatchlist(date: string) {
  return useQuery({
    queryKey: ['watchlist', date],
    queryFn: () => apiFetch<WatchlistCandidate[]>(`/scanner/watchlist?date=${date}`),
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

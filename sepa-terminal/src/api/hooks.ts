import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, apiPost } from './client'
import { camelToSnake } from '@/lib/utils'
import type {
  BacktestReport,
  BacktestRunRequest,
  DailyDiagnostics,
  MarketBreadth,
  OHLCVBar,
  ScanSessionSummary,
  SetupExplanation,
  Signal,
  SymbolSignalHistory,
  WatchlistCandidate,
} from '@/lib/types'

const DAY = 24 * 60 * 60 * 1000

export function useScanDates() {
  return useQuery({
    queryKey: ['scan-dates'],
    queryFn: () => apiFetch<string[]>('/scanner/dates'),
    staleTime: 5 * 60 * 1000,
  })
}

export function useScanSessions() {
  return useQuery({
    queryKey: ['scan-sessions'],
    queryFn: () => apiFetch<ScanSessionSummary[]>('/scanner/sessions'),
    staleTime: 5 * 60 * 1000,
  })
}

export function useSignals(date: string | null) {
  return useQuery({
    queryKey: ['signals', date],
    queryFn: () => apiFetch<Signal[]>(`/scanner/signals?date=${date}`),
    enabled: !!date,
    staleTime: DAY,
  })
}

export function useWatchlist(date: string | null) {
  return useQuery({
    queryKey: ['watchlist', date],
    queryFn: () => apiFetch<WatchlistCandidate[]>(`/scanner/watchlist?date=${date}`),
    enabled: !!date,
    staleTime: DAY,
  })
}

export function useBreadth(date: string | null) {
  return useQuery({
    queryKey: ['breadth', date],
    queryFn: () => apiFetch<MarketBreadth>(`/scanner/breadth?date=${date}`),
    enabled: !!date,
    staleTime: DAY,
  })
}

/** Breadth for the N most recent scan dates (dashboard history). */
export function useBreadthHistory(dates: string[] | undefined, count = 30) {
  const recent = (dates ?? []).slice(0, count)
  return useQueries({
    queries: recent.map((d) => ({
      queryKey: ['breadth', d],
      queryFn: () => apiFetch<MarketBreadth>(`/scanner/breadth?date=${d}`),
      staleTime: DAY,
    })),
    combine: (results) => ({
      isLoading: results.some((r) => r.isLoading),
      data: results
        .map((r) => r.data)
        .filter((d): d is MarketBreadth => !!d)
        .sort((a, b) => a.date.localeCompare(b.date)),
    }),
  })
}

/** Signal counts for recent scan dates (dashboard activity strip). */
export function useSignalCounts(dates: string[] | undefined, count = 30) {
  const recent = (dates ?? []).slice(0, count)
  return useQueries({
    queries: recent.map((d) => ({
      queryKey: ['signals', d],
      queryFn: () => apiFetch<Signal[]>(`/scanner/signals?date=${d}`),
      staleTime: DAY,
    })),
    combine: (results) => ({
      isLoading: results.some((r) => r.isLoading),
      data: recent
        .map((date, i) => {
          const rows = results[i]?.data
          if (!rows) return null
          return {
            date,
            breakouts: rows.filter((s) => s.signalType === 'BREAKOUT').length,
            watch: rows.filter((s) => s.signalType === 'WATCH').length,
          }
        })
        .filter((d): d is { date: string; breakouts: number; watch: number } => !!d)
        .sort((a, b) => a.date.localeCompare(b.date)),
    }),
  })
}

export function useOHLCV(symbol: string | null) {
  return useQuery({
    queryKey: ['ohlcv', symbol],
    // Full history by design (see CLAUDE.md: charts render all available history)
    queryFn: () => apiFetch<OHLCVBar[]>(`/ohlcv/${symbol}`),
    enabled: !!symbol,
    staleTime: DAY,
  })
}

export function useDiagnostics(symbol: string | null, date: string | null) {
  return useQuery({
    queryKey: ['diagnostics', symbol, date],
    queryFn: () => apiFetch<DailyDiagnostics>(`/analysis/${symbol}/diagnostics?date=${date}`),
    enabled: !!symbol && !!date,
    staleTime: DAY,
    retry: false,
  })
}

export function useSetupExplanation(symbol: string | null, date: string | null) {
  return useQuery({
    queryKey: ['setup-explanation', symbol, date],
    queryFn: () => apiFetch<SetupExplanation>(`/analysis/${symbol}/setup-explanation?date=${date}`),
    enabled: !!symbol && !!date,
    staleTime: DAY,
    retry: false,
  })
}

export function useSignalHistory(symbol: string | null) {
  return useQuery({
    queryKey: ['signal-history', symbol],
    queryFn: () => apiFetch<SymbolSignalHistory>(`/analysis/${symbol}/signal-history`),
    enabled: !!symbol,
    staleTime: DAY,
  })
}

export function useLastBacktest() {
  return useQuery({
    queryKey: ['backtest', 'last'],
    queryFn: () => apiFetch<BacktestReport | null>('/backtest/last'),
    staleTime: Infinity,
    retry: false,
  })
}

export function useRunBacktest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (req: BacktestRunRequest) =>
      apiPost<BacktestReport>('/backtest/run', camelToSnake(req)),
    onSuccess: (report) => {
      queryClient.setQueryData(['backtest', 'last'], report)
    },
  })
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { snakeToCamel } from '@/lib/case'
import type { BacktestParams, BacktestReport } from '@/lib/types'

export function useBacktestLast() {
  return useQuery<BacktestReport | null>({
    queryKey: ['backtest', 'last'],
    queryFn: async () => {
      try {
        const raw = await apiFetch<unknown>('/backtest/last')
        return snakeToCamel(raw) as BacktestReport
      } catch {
        return null
      }
    },
    staleTime: Infinity,
  })
}

export function useRunBacktest() {
  const queryClient = useQueryClient()
  return useMutation<BacktestReport, Error, BacktestParams>({
    mutationFn: async (params) => {
      const body = {
        start_date: params.startDate,
        end_date: params.endDate,
        exit_mode: params.exitMode,
        target_r: params.targetR,
        pending_days: params.pendingDays,
        max_hold_days: params.maxHoldDays,
        trail_activation_r: params.trailActivationR,
      }
      const raw = await apiFetch<unknown>('/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return snakeToCamel(raw) as BacktestReport
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['backtest', 'last'], data)
    },
  })
}

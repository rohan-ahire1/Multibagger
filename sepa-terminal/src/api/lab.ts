import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, apiPost } from './client'
import { camelToSnake } from '@/lib/utils'
import type {
  CompareResult,
  CreateRunRequest,
  LabRun,
  LabRunDetail,
  LabStrategy,
  RunStatusPayload,
} from '@/lib/lab-types'

export function useLabStrategies() {
  return useQuery({
    queryKey: ['lab', 'strategies'],
    queryFn: async () =>
      (await apiFetch<{ strategies: LabStrategy[] }>('/lab/strategies')).strategies,
    staleTime: 10 * 60 * 1000,
  })
}

export function useLabRuns(strategyId?: string) {
  return useQuery({
    queryKey: ['lab', 'runs', strategyId ?? 'all'],
    queryFn: async () =>
      (
        await apiFetch<{ runs: LabRun[] }>(
          strategyId ? `/lab/runs?strategy_id=${strategyId}` : '/lab/runs'
        )
      ).runs,
    // A queued or running row changes without any action from this client, so
    // the list needs to refresh on its own while work is outstanding.
    refetchInterval: (query) => {
      const runs = query.state.data as LabRun[] | undefined
      const active = runs?.some((r) => r.status === 'queued' || r.status === 'running')
      return active ? 3000 : false
    },
  })
}

export function useLabRun(runId: string | undefined) {
  return useQuery({
    queryKey: ['lab', 'run', runId],
    queryFn: () => apiFetch<LabRunDetail>(`/lab/runs/${runId}`),
    enabled: !!runId,
  })
}

/** Polls only while the run is unfinished, then stops. */
export function useLabRunStatus(runId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['lab', 'run-status', runId],
    queryFn: () => apiFetch<RunStatusPayload>(`/lab/runs/${runId}/status`),
    enabled: !!runId && enabled,
    refetchInterval: (query) => {
      const data = query.state.data as RunStatusPayload | undefined
      if (!data) return 2000
      return data.status === 'done' || data.status === 'failed' ? false : 2000
    },
  })
}

export function useCreateLabRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (req: CreateRunRequest) =>
      apiPost<{ runId: string; status: string }>('/lab/runs', camelToSnake(req), 30_000),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lab', 'runs'] })
    },
  })
}

export function useDeleteLabRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      apiFetch<{ deleted: string }>(`/lab/runs/${runId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lab', 'runs'] })
    },
  })
}

export function useCompareRuns() {
  return useMutation({
    mutationFn: (runIds: string[]) =>
      apiPost<CompareResult>('/lab/compare', { run_ids: runIds }, 60_000),
  })
}

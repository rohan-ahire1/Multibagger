import { snakeToCamel } from '@/lib/utils'

const BASE_URL = '/api'

export async function apiFetch<T>(path: string, init?: RequestInit, timeoutMs = 20_000): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${BASE_URL}${path}`, { ...init, signal: controller.signal })
    if (!res.ok) {
      let detail = ''
      try {
        const body = await res.json()
        detail = body?.detail ? `: ${body.detail}` : ''
      } catch {
        // non-JSON error body
      }
      throw new Error(`API ${res.status} on ${path}${detail}`)
    }
    return snakeToCamel(await res.json()) as T
  } finally {
    clearTimeout(timeout)
  }
}

export function apiPost<T>(path: string, body: unknown, timeoutMs = 300_000): Promise<T> {
  return apiFetch<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    timeoutMs
  )
}

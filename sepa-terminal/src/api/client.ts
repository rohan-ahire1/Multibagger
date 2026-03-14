const BASE_URL = '/api'

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15_000)
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
    })
    if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
    return res.json()
  } finally {
    clearTimeout(timeout)
  }
}

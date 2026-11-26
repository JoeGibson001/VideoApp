// Small fetch wrapper: JSON, CSRF header for cookie-authenticated writes, typed errors.

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : (detail as { message?: string })?.message || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

function readCookie(name: string): string | null {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : null
}

export interface RequestOpts {
  method?: string
  body?: unknown
  params?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  form?: FormData
}

export function buildUrl(path: string, params?: RequestOpts['params']): string {
  const url = new URL(path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  return url.pathname + url.search
}

export async function api<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const method = opts.method || 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  let body: BodyInit | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  if (method !== 'GET' && method !== 'HEAD') {
    const csrf = readCookie('vs_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }
  const res = await fetch(buildUrl(path, opts.params), {
    method, headers, body, signal: opts.signal, credentials: 'same-origin',
  })
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try { data = JSON.parse(text) } catch { data = text }
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data
    throw new ApiError(res.status, detail)
  }
  return data as T
}

export const get = <T,>(path: string, params?: RequestOpts['params'], signal?: AbortSignal) =>
  api<T>(path, { params, signal })
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'POST', body })
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'PUT', body })
export const patch = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'PATCH', body })
export const del = <T,>(path: string, params?: RequestOpts['params']) => api<T>(path, { method: 'DELETE', params })

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as { message?: string } | string
    if (typeof d === 'string') return d
    if (d && typeof d === 'object' && 'message' in d && d.message) return d.message
    return `Request failed (${e.status})`
  }
  if (e instanceof Error) return e.message
  return 'Something went wrong'
}

// ---- rating queue: optimistic saves, retried when offline (DESIGN.md 14) ----------------------

type PendingRating = { videoId: number; value: 1 | -1 | null; at: number }
const QUEUE_KEY = 'vs_rating_queue'

function loadQueue(): PendingRating[] {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]') } catch { return [] }
}
function saveQueue(q: PendingRating[]) {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)) } catch { /* ignore */ }
}

let flushing = false
export async function flushRatings(): Promise<void> {
  if (flushing) return
  flushing = true
  try {
    let q = loadQueue()
    while (q.length) {
      const item = q[0]
      try {
        if (item.value === null) await del(`/api/videos/${item.videoId}/rating`)
        else await put(`/api/videos/${item.videoId}/rating`, { value: item.value })
      } catch (e) {
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
          // not retryable (deleted video, logged out): drop it
        } else {
          break
        }
      }
      q = loadQueue().filter((p) => !(p.videoId === item.videoId && p.at === item.at))
      saveQueue(q)
    }
  } finally {
    flushing = false
  }
}

export function queueRating(videoId: number, value: 1 | -1 | null) {
  const q = loadQueue().filter((p) => p.videoId !== videoId)
  q.push({ videoId, value, at: Date.now() })
  saveQueue(q)
  void flushRatings()
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => void flushRatings())
  setInterval(() => void flushRatings(), 15000)
}

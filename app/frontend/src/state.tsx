import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { get, patch, post } from './api'
import type { User } from './types'

// ---- auth -------------------------------------------------------------------------------------

interface AuthCtx {
  user: User | null
  loading: boolean
  login: (u: string, p: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  updateSettings: (s: Record<string, unknown>) => Promise<void>
}

const AuthContext = createContext<AuthCtx>(null as unknown as AuthCtx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const r = await get<{ user: User | null }>('/api/auth/me')
      setUser(r.user)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const r = await post<{ user: User }>('/api/auth/login', { username, password })
    setUser(r.user)
  }, [])

  const logout = useCallback(async () => {
    try { await post('/api/auth/logout') } finally { setUser(null) }
  }, [])

  const updateSettings = useCallback(async (s: Record<string, unknown>) => {
    const r = await patch<{ settings: Record<string, unknown> }>('/api/auth/settings', { settings: s })
    setUser((u) => (u ? { ...u, settings: r.settings } : u))
  }, [])

  const value = useMemo(() => ({ user, loading, login, logout, refresh, updateSettings }),
    [user, loading, login, logout, refresh, updateSettings])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)

// ---- toast ------------------------------------------------------------------------------------

const ToastContext = createContext<(msg: string) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState('')
  const [show, setShow] = useState(false)
  const timer = useRef<number>()
  const toast = useCallback((m: string) => {
    setMsg(m)
    setShow(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setShow(false), 1700)
  }, [])
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className={'toast' + (show ? ' show' : '')} role="status" aria-live="polite">{msg}</div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

// ---- session playback prefs ---------------------------------------------------------------------

function readSession(key: string): string | null {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function writeSession(key: string, val: string) {
  try { sessionStorage.setItem(key, val) } catch { /* ignore */ }
}

interface PlaybackCtx { muted: boolean; setMuted: (m: boolean) => void }
const PlaybackContext = createContext<PlaybackCtx>({ muted: true, setMuted: () => {} })

export function PlaybackProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const stored = readSession('vs_muted')
  const [muted, setMutedState] = useState<boolean>(stored === null ? true : stored === '1')
  useEffect(() => {
    if (readSession('vs_muted') === null && user?.settings?.mute_default === false) setMutedState(false)
  }, [user])
  const setMuted = useCallback((m: boolean) => {
    setMutedState(m)
    writeSession('vs_muted', m ? '1' : '0')
  }, [])
  return <PlaybackContext.Provider value={{ muted, setMuted }}>{children}</PlaybackContext.Provider>
}

export const usePlayback = () => useContext(PlaybackContext)

// ---- anonymous ratings, kept in memory for the session (DESIGN.md 8) ---------------------------

export const anonRatings = new Map<number, 1 | -1>()

// ---- hooks ------------------------------------------------------------------------------------

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

export function usePrefersReducedMotion(): boolean {
  const [r, setR] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!mq) return
    const fn = () => setR(mq.matches)
    mq.addEventListener('change', fn)
    return () => mq.removeEventListener('change', fn)
  }, [])
  return r
}

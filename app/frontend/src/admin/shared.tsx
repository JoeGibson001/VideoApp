import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, errorMessage, get, post } from '../api'
import { Spinner } from '../components/common'
import { LIBRARY_SORTS } from '../sorts'
import { useToast } from '../state'

/** Responses may be a bare list or `{items: [...]}`. */
export function listOf<T>(r: unknown): T[] {
  if (Array.isArray(r)) return r as T[]
  if (r && typeof r === 'object' && Array.isArray((r as { items?: unknown }).items)) return (r as { items: T[] }).items
  return []
}

/** errorMessage plus pydantic validation lists (`[{loc, msg}]`). */
export function errText(e: unknown): string {
  if (e instanceof ApiError && Array.isArray(e.detail)) {
    const parts = (e.detail as { msg?: string; loc?: unknown[] }[]).map((d) => {
      const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : ''
      return (loc ? loc + ': ' : '') + (d.msg || 'invalid')
    })
    return parts.join('; ') || `Request failed (${e.status})`
  }
  return errorMessage(e)
}

export const isOffline = (e: unknown) => e instanceof ApiError && (e.status === 503 || e.status === 502 || e.status === 504)

/** Load data on mount / when deps change, with reload and in-place updates. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)
  const reload = useCallback(async (quiet = false) => {
    const n = ++seq.current
    if (!quiet) setLoading(true)
    try {
      const d = await fn()
      if (n === seq.current) { setData(d); setError(null) }
    } catch (e) {
      if (n === seq.current) setError(e)
    } finally {
      if (n === seq.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => { void reload() }, [reload])
  return { data, setData, error, loading, reload }
}

/** Runs an async action with a busy flag and toast/err reporting. */
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = useCallback(async <T,>(fn: () => Promise<T>, okMsg?: string): Promise<T | undefined> => {
    setBusy(true)
    setError('')
    try {
      const r = await fn()
      if (okMsg) toast(okMsg)
      return r
    } catch (e) {
      setError(errText(e))
      return undefined
    } finally {
      setBusy(false)
    }
  }, [toast])
  return { busy, error, setError, run }
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return <div className="error" role="alert">{typeof error === 'string' ? error : errText(error)}</div>
}

export function Loading({ loading, children }: { loading: boolean; children?: ReactNode }) {
  return loading ? <Spinner /> : <>{children}</>
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (pages <= 1) return <div className="pager">{total} item{total === 1 ? '' : 's'}</div>
  return (
    <div className="pager">
      <button className="btn small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Prev</button>
      <span>Page {page} of {pages} ({total})</span>
      <button className="btn small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  )
}

/** Generic renderer for maintenance-style results. */
export function JsonView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">-</span>
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">none</span>
    return (
      <div className="json">
        <span>{value.length} item{value.length === 1 ? '' : 's'}</span>
        <ul>{value.slice(0, 200).map((v, i) => <li key={i}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</li>)}</ul>
        {value.length > 200 ? <span className="muted">...and {value.length - 200} more</span> : null}
      </div>
    )
  }
  if (typeof value === 'object') {
    return (
      <div className="kv json">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <KvPair key={k} k={k.replace(/_/g, ' ')} v={<JsonView value={v} />} />
        ))}
      </div>
    )
  }
  if (typeof value === 'boolean') return <span>{value ? 'yes' : 'no'}</span>
  return <span>{String(value)}</span>
}

function KvPair({ k, v }: { k: string; v: ReactNode }) {
  return <><span>{k}</span><span>{v}</span></>
}

export function FileButton({ label, accept, onFile, disabled }: {
  label: string; accept: string; onFile: (f: File) => void; disabled?: boolean
}) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <>
      <button className="btn small" type="button" disabled={disabled} onClick={() => ref.current?.click()}>{label}</button>
      <input ref={ref} type="file" accept={accept} hidden onChange={(e) => {
        const f = e.target.files?.[0]
        e.target.value = ''
        if (f) onFile(f)
      }} />
    </>
  )
}

export function uploadForm(file: File): FormData {
  const fd = new FormData()
  fd.append('file', file)
  return fd
}

// ---- merge preview (DESIGN.md 9.1) ---------------------------------------------------------------

export interface MergePreviewData {
  from: { id: number; name: string; handle?: string }[]
  into: { id: number; name: string; handle?: string }
  videos_affected: number
  videos_with_both: number
  categories_affected: number
  users: { user_id: number; username: string; score_before: number; score_after: number }[]
}

/**
 * Shows the merge preview and confirms. With `allowSwap` and a single "from" id, the user can flip the
 * direction (used by duplicate suggestions).
 */
export function MergePreview({ kind, fromIds, intoId, allowSwap, onDone, onCancel }: {
  kind: 'tag' | 'creator'
  fromIds: number[]
  intoId: number
  allowSwap?: boolean
  onDone: () => void
  onCancel: () => void
}) {
  const [dir, setDir] = useState({ from: fromIds, into: intoId })
  const base = kind === 'tag' ? '/api/admin/tags' : '/api/admin/creators'
  const { data, error, loading } = useLoad(
    () => get<MergePreviewData>(`${base}/merge-preview`, { from: dir.from.join(','), into: dir.into }),
    [base, dir.from.join(','), dir.into])
  const act = useAction()
  const label = (x: { name: string; handle?: string }) => (kind === 'tag' ? x.name : `${x.name}${x.handle ? ` (${x.handle})` : ''}`)
  const confirm = async () => {
    const r = await act.run(() => post(`${base}/merge`, { from_ids: dir.from, into_id: dir.into }), 'Merged')
    if (r !== undefined) onDone()
  }
  return (
    <div>
      <h3>Merge {kind}s</h3>
      <ErrorBox error={error} />
      {loading ? <Spinner /> : data ? (
        <>
          <p>
            Merge <b>{data.from.map(label).join(', ')}</b> into <b>{label(data.into)}</b>.
            {kind === 'tag' ? ' The old names become aliases.' : ' The old handles become aliases.'}
          </p>
          {allowSwap && dir.from.length === 1 ? (
            <button className="btn small" onClick={() => setDir({ from: [dir.into], into: dir.from[0] })}>
              Swap direction (keep {label(data.from[0])})
            </button>
          ) : null}
          <div className="stats">
            <div className="stat"><b>{data.videos_affected}</b><small>videos affected</small></div>
            {kind === 'tag' ? <div className="stat"><b>{data.videos_with_both}</b><small>videos with both</small></div> : null}
            <div className="stat"><b>{data.categories_affected}</b><small>moods affected</small></div>
          </div>
          {data.users.length ? (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>User</th><th className="num">Score before</th><th className="num">After</th></tr></thead>
                <tbody>
                  {data.users.map((u) => (
                    <tr key={u.user_id}>
                      <td>{u.username}</td>
                      <td className="num">{u.score_before}</td>
                      <td className="num">{u.score_before === u.score_after ? u.score_after : <b>{u.score_after}</b>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn primary" disabled={!data || act.busy} onClick={() => void confirm()}>
          {act.busy ? 'Merging...' : 'Confirm merge'}
        </button>
      </div>
    </div>
  )
}

interface DeletePreviewData { type: 'source' | 'creator' | 'tag'; id: number; name: string; videos: number; creators?: number }

/**
 * Danger-zone delete for a source, creator or tag. Shows how many videos (and creators) will go,
 * and asks the admin to type the name before anything that deletes videos.
 * Tags can also be deleted on their own, keeping their videos.
 */
export function DangerDelete({ kind, id, onDeleted }: { kind: 'source' | 'creator' | 'tag'; id: number; onDeleted: () => void }) {
  const [preview, setPreview] = useState<DeletePreviewData | null>(null)
  const [withVideos, setWithVideos] = useState(kind !== 'tag')
  const [typed, setTyped] = useState('')
  const toast = useToast()
  const act = useAction()
  const base = kind === 'source' ? 'sources' : kind === 'creator' ? 'creators' : 'tags'

  const open = async () => {
    const p = await act.run(() => get<DeletePreviewData>(`/api/admin/${base}/${id}/delete-preview`))
    if (p) { setPreview(p); setTyped(''); setWithVideos(kind !== 'tag') }
  }
  const run = async () => {
    if (!preview) return
    const params = kind === 'tag' && withVideos ? '?delete_videos=true' : ''
    const r = await act.run(() => api<{ videos_deleted: number }>(`/api/admin/${base}/${id}${params}`, { method: 'DELETE' }))
    if (r) {
      toast(`Deleted ${preview.name}${r.videos_deleted ? ` and ${r.videos_deleted} video${r.videos_deleted === 1 ? '' : 's'}` : ''}`)
      setPreview(null)
      onDeleted()
    }
  }
  const deletesVideos = !!preview && preview.videos > 0 && (kind !== 'tag' || withVideos)
  const label = kind === 'source' ? 'source' : kind === 'creator' ? 'creator' : 'tag'

  return (
    <div className="card danger-zone">
      <div className="section-title" style={{ marginTop: 0 }}>Danger zone</div>
      {!preview ? (
        <button className="btn danger" disabled={act.busy} onClick={() => void open()}>
          {kind === 'tag' ? 'Delete tag...' : `Delete ${label} and all its videos...`}
        </button>
      ) : (
        <div>
          {kind === 'tag' ? (
            <>
              <label className="check">
                <input type="radio" name={`del-${id}`} checked={!withVideos} onChange={() => setWithVideos(false)} />
                Delete the tag only (its {preview.videos} video{preview.videos === 1 ? '' : 's'} are kept)
              </label>
              <label className="check">
                <input type="radio" name={`del-${id}`} checked={withVideos} disabled={!preview.videos} onChange={() => setWithVideos(true)} />
                Delete the tag and all {preview.videos} video{preview.videos === 1 ? '' : 's'} that have it
              </label>
            </>
          ) : (
            <p style={{ margin: '0 0 8px' }}>
              This permanently deletes <b>{preview.name}</b>, <b>{preview.videos}</b> video{preview.videos === 1 ? '' : 's'}
              {kind === 'source' ? <> and <b>{preview.creators}</b> creator{preview.creators === 1 ? '' : 's'}</> : null}, including
              the video, poster and metadata files, plus everyone's ratings of those videos.
              {' Active watches in the Importer are not removed; pause them first or they will import again.'}
            </p>
          )}
          {deletesVideos ? (
            <label className="field">Type <b>{preview.name}</b> to confirm. This cannot be undone.
              <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type the name to confirm" />
            </label>
          ) : null}
          <div className="btnrow">
            <button className="btn" onClick={() => setPreview(null)}>Cancel</button>
            <button className="btn danger" disabled={act.busy || (deletesVideos && typed.trim() !== preview.name)} onClick={() => void run()}>
              {deletesVideos ? `Delete ${preview.videos} video${preview.videos === 1 ? '' : 's'}` : `Delete ${label}`}
            </button>
          </div>
        </div>
      )}
      <ErrorBox error={act.error} />
    </div>
  )
}

/** Same sort options as the user library. Scores are the admin's own ratings. */
export function SortSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Sort" style={{ flex: 'none', width: 'auto' }}>
      {LIBRARY_SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  )
}

/** Remember a per-page choice (e.g. sort) in this browser. */
export function useStoredState(key: string, initial: string): [string, (v: string) => void] {
  const [v, setV] = useState(() => {
    try { return localStorage.getItem(key) || initial } catch { return initial }
  })
  const set = (nv: string) => {
    setV(nv)
    try { localStorage.setItem(key, nv) } catch { /* storage unavailable */ }
  }
  return [v, set]
}

export function scoreText(score: number | undefined): string {
  const n = Math.round(score || 0)
  return n > 0 ? `+${n}` : String(n)
}

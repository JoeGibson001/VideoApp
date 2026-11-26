import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, get, post } from '../api'
import { useToast } from '../state'
import type { Watch, WatchStatus } from '../types'
import { Spinner, formatDateTime } from './common'
import { Eye } from './Icons'

// ---- indicator ---------------------------------------------------------------------------------

const STATE_LABEL: Record<WatchStatus['state'], string> = {
  watching: 'Watching', paused: 'Paused', error: 'Error', none: 'Not watched', offline: 'Offline',
}

/** Small eye pill for creator headers and lists (DESIGN.md 11.8). */
export function WatchIndicator({ state, title, onClick }: {
  state: WatchStatus['state']; title?: string; onClick?: () => void
}) {
  const cls = `pill watchpill ${state}`
  const inner = <><Eye size={12} /><span className="label">{STATE_LABEL[state]}</span></>
  if (onClick) return <button type="button" className={cls} title={title} onClick={onClick}>{inner}</button>
  return <span className={cls} title={title}>{inner}</span>
}

// ---- filters (shared with the admin Watches page) ------------------------------------------------

export interface WatchFilters {
  max_duration_s?: number | null
  min_duration_s?: number | null
  title_include?: string[] | string | null
  title_exclude?: string[] | string | null
  vertical_only?: boolean
  uploaded_after?: string | null
}

export interface FilterDraft {
  max_duration_s: string; min_duration_s: string; title_include: string; title_exclude: string
  vertical_only: boolean; uploaded_after: string
}

const joinList = (v: unknown) => (Array.isArray(v) ? v.join(', ') : typeof v === 'string' ? v : '')

export function draftFromFilters(f?: Record<string, unknown> | null): FilterDraft {
  const x = (f || {}) as WatchFilters
  return {
    max_duration_s: x.max_duration_s != null ? String(x.max_duration_s) : '',
    min_duration_s: x.min_duration_s != null ? String(x.min_duration_s) : '',
    title_include: joinList(x.title_include),
    title_exclude: joinList(x.title_exclude),
    vertical_only: !!x.vertical_only,
    uploaded_after: x.uploaded_after || '',
  }
}

export function filtersFromDraft(d: FilterDraft): WatchFilters {
  const out: WatchFilters = {}
  const num = (s: string) => (s.trim() && !isNaN(Number(s)) ? Number(s) : null)
  const list = (s: string) => s.split(',').map((t) => t.trim()).filter(Boolean)
  const max = num(d.max_duration_s)
  const min = num(d.min_duration_s)
  if (max !== null) out.max_duration_s = max
  if (min !== null) out.min_duration_s = min
  if (list(d.title_include).length) out.title_include = list(d.title_include)
  if (list(d.title_exclude).length) out.title_exclude = list(d.title_exclude)
  if (d.vertical_only) out.vertical_only = true
  if (d.uploaded_after) out.uploaded_after = d.uploaded_after
  return out
}

export const isShortsOnly = (d: FilterDraft) => d.vertical_only && d.max_duration_s.trim() === '180'

export function FiltersForm({ value, onChange }: { value: FilterDraft; onChange: (d: FilterDraft) => void }) {
  const set = (k: keyof FilterDraft, v: string | boolean) => onChange({ ...value, [k]: v })
  return (
    <div>
      <label className="check">
        <input type="checkbox" checked={isShortsOnly(value)} onChange={(e) => onChange(e.target.checked
          ? { ...value, max_duration_s: '180', vertical_only: true }
          : { ...value, max_duration_s: '', vertical_only: false })} />
        Shorts only (vertical, up to 3 minutes)
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label className="field">Min duration (s)
          <input type="number" min={0} value={value.min_duration_s} onChange={(e) => set('min_duration_s', e.target.value)} />
        </label>
        <label className="field">Max duration (s)
          <input type="number" min={0} value={value.max_duration_s} onChange={(e) => set('max_duration_s', e.target.value)} />
        </label>
      </div>
      <label className="field">Title must include (comma separated)
        <input type="text" value={value.title_include} onChange={(e) => set('title_include', e.target.value)} />
      </label>
      <label className="field">Title must not include (comma separated)
        <input type="text" value={value.title_exclude} onChange={(e) => set('title_exclude', e.target.value)} />
      </label>
      <label className="field">Uploaded after
        <input type="date" value={value.uploaded_after} onChange={(e) => set('uploaded_after', e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={value.vertical_only} onChange={(e) => set('vertical_only', e.target.checked)} />
        Vertical videos only
      </label>
    </div>
  )
}

export const emptyDraft = (): FilterDraft => draftFromFilters({})

export const splitTags = (s: string) => s.split(',').map((t) => t.trim()).filter(Boolean)

// ---- section -------------------------------------------------------------------------------------

type Dialog = { kind: 'create' } | { kind: 'resume'; watch: Watch } | null

/**
 * Watch toggle(s) for one creator (DESIGN.md 11.8). Off->On creates (with a first-run dialog) or resumes
 * (asking import_missed vs only_new); On->Off pauses. Used on the admin creator page and the feed's
 * creator sheet.
 */
export function WatchSection({ creatorId, endpoint, defaultTags, description }: {
  creatorId?: number
  /** Any GET/POST watch-status endpoint with the creator contract, e.g. a source tag's. */
  endpoint?: string
  /** Prefilled "extra tags" in the create dialog. */
  defaultTags?: string[]
  /** Explains what URL gets watched, shown in the create dialog. */
  description?: string
}) {
  const url = endpoint || `/api/admin/creators/${creatorId}/watch`
  const toast = useToast()
  const [status, setStatus] = useState<WatchStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState<Dialog>(null)

  const load = useCallback(async () => {
    try {
      setStatus(await get<WatchStatus>(url))
      setError('')
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [url])

  useEffect(() => { setLoading(true); setDialog(null); void load() }, [load])

  const send = async (body: Record<string, unknown>, ok: string) => {
    setBusy(true)
    setError('')
    try {
      setStatus(await post<WatchStatus>(url, body))
      setDialog(null)
      toast(ok)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const checkNow = async (w: Watch) => {
    setBusy(true)
    setError('')
    try {
      await post(`/api/admin/watches/${w.id}/run`)
      toast('Check started')
      window.setTimeout(() => void load(), 3000)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const onToggle = (w: Watch | null, on: boolean) => {
    if (on) {
      if (!w) setDialog({ kind: 'create' })
      else if (!w.enabled) setDialog({ kind: 'resume', watch: w })
    } else if (w && w.enabled) {
      void send({ enabled: false, watch_id: w.id }, 'Watch paused')
    }
  }

  if (loading && !status) return <div className="watch-section"><Spinner /></div>
  const offline = status?.state === 'offline'
  const watches = status?.watches || []

  return (
    <div className="watch-section">
      {status ? <div className="rowline" style={{ margin: '8px 0' }}><WatchIndicator state={status.state} /></div> : null}
      {offline ? <div className="notice">Importer offline. Watch settings are unavailable right now.</div> : null}
      {error ? <div className="error" role="alert">{error}</div> : null}

      {watches.length === 0 ? (
        <label className="check">
          <input type="checkbox" role="switch" checked={dialog?.kind === 'create'} disabled={offline || busy || !status}
            onChange={(e) => (e.target.checked ? onToggle(null, true) : setDialog(null))} />
          Watch for new videos
        </label>
      ) : watches.map((w) => (
        <div key={w.id} className="card" style={{ padding: 10 }}>
          <label className="check">
            <input type="checkbox" role="switch" checked={w.enabled} disabled={offline || busy}
              onChange={(e) => onToggle(w, e.target.checked)} />
            <span style={{ flex: 1, minWidth: 0 }}>
              Watch for new videos{watches.length > 1 ? <span className="muted"> ({w.name || w.url})</span> : null}
            </span>
          </label>
          {w.erroring || (w.consecutive_failures || 0) >= 3 ? (
            <div className="notice">Failing{w.consecutive_failures ? ` (${w.consecutive_failures} in a row)` : ''}: {w.last_error || 'unknown error'}</div>
          ) : null}
          <div className="kv">
            <span>Last checked</span><span>{formatDateTime(w.last_checked_at)}{w.last_new_count != null ? ` (${w.last_new_count} new)` : ''}</span>
            <span>Next check</span><span>{w.enabled ? formatDateTime(w.next_run_at) : 'Paused'}</span>
            <span>Imported</span><span>{w.imported_count ?? 0} video{w.imported_count === 1 ? '' : 's'}</span>
          </div>
          <div className="btnrow">
            <button className="btn small" disabled={offline || busy} onClick={() => void checkNow(w)}>Check now</button>
            <Link className="btn small" to={`/admin/watches?id=${w.id}`}>Edit</Link>
          </div>
          {dialog?.kind === 'resume' && dialog.watch.id === w.id ? (
            <ResumeDialog busy={busy} onCancel={() => setDialog(null)}
              onPick={(mode) => void send({ enabled: true, watch_id: w.id, resume_mode: mode }, 'Watch resumed')} />
          ) : null}
        </div>
      ))}

      {dialog?.kind === 'create' ? (
        <CreateDialog busy={busy} onCancel={() => setDialog(null)} defaultTags={defaultTags} description={description}
          onCreate={(body) => void send({ enabled: true, ...body }, 'Watch created')} />
      ) : null}
    </div>
  )
}

function ResumeDialog({ busy, onPick, onCancel }: { busy: boolean; onPick: (m: 'import_missed' | 'only_new') => void; onCancel: () => void }) {
  return (
    <div className="card" style={{ background: 'var(--panel2)' }}>
      <b>Resume watch</b>
      <p className="muted" style={{ fontSize: 13 }}>What should happen to videos posted while the watch was paused?</p>
      <div className="btnrow">
        <button className="btn primary" disabled={busy} onClick={() => onPick('import_missed')}>Import what was posted while paused</button>
        <button className="btn" disabled={busy} onClick={() => onPick('only_new')}>Only new from now</button>
      </div>
      <button className="btn small" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  )
}

function CreateDialog({ busy, onCreate, onCancel, defaultTags, description }: {
  defaultTags?: string[]; description?: string
  busy: boolean; onCreate: (body: Record<string, unknown>) => void; onCancel: () => void
}) {
  const [mode, setMode] = useState<'new_only' | 'backfill_latest'>('new_only')
  const [count, setCount] = useState('20')
  const [tags, setTags] = useState((defaultTags || []).join(', '))
  const [filters, setFilters] = useState<FilterDraft>(emptyDraft)
  const [more, setMore] = useState(false)
  const submit = () => {
    const body: Record<string, unknown> = { initial_mode: mode, tags: splitTags(tags), filters: filtersFromDraft(filters) }
    if (mode === 'backfill_latest') body.backfill_count = Math.max(1, Number(count) || 20)
    onCreate(body)
  }
  return (
    <div className="card" style={{ background: 'var(--panel2)' }}>
      <b>Start watching</b>
      <p className="muted" style={{ fontSize: 13, margin: '4px 0' }}>{description || "The creator's profile URL is checked once a day for new videos."}</p>
      <label className="check">
        <input type="radio" name="initial_mode" checked={mode === 'new_only'} onChange={() => setMode('new_only')} />
        Only videos posted from now on
      </label>
      <label className="check">
        <input type="radio" name="initial_mode" checked={mode === 'backfill_latest'} onChange={() => setMode('backfill_latest')} />
        Also import the latest
        <input type="number" min={1} max={5000} value={count} style={{ width: 90 }} aria-label="Backfill count"
          onChange={(e) => { setCount(e.target.value); setMode('backfill_latest') }} />
        videos
      </label>
      <label className="check">
        <input type="checkbox" checked={isShortsOnly(filters)} onChange={(e) => setFilters(e.target.checked
          ? { ...filters, max_duration_s: '180', vertical_only: true }
          : { ...filters, max_duration_s: '', vertical_only: false })} />
        Shorts only
      </label>
      <label className="field">Extra tags (comma separated)
        <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} />
      </label>
      <button className="btn small" type="button" onClick={() => setMore((m) => !m)}>{more ? 'Hide filters' : 'More filters'}</button>
      {more ? <FiltersForm value={filters} onChange={setFilters} /> : null}
      <div className="btnrow">
        <button className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={submit}>{busy ? 'Creating...' : 'Start watching'}</button>
      </div>
    </div>
  )
}

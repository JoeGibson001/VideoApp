import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { del, get, patch, post } from '../api'
import { EmptyState, Spinner, formatDateTime } from '../components/common'
import { ChevronLeft, External } from '../components/Icons'
import {
  FiltersForm, WatchIndicator, draftFromFilters, emptyDraft, filtersFromDraft, splitTags, type FilterDraft,
} from '../components/WatchSection'
import { useToast } from '../state'
import type { Watch, WatchStatus } from '../types'
import { ErrorBox, isOffline, listOf, useAction, useLoad } from './shared'

interface WatchRun {
  id: number; watch_id: number; trigger?: string; started_at: string | null; finished_at: string | null
  listed_count: number | null; new_count: number | null; queued_count: number | null; error: string | null
}
interface WatchItem {
  watch_id: number; source_video_id: string; url?: string | null; title?: string | null; status: string
  job_id: number | null; detail: string | null; retry_count?: number; first_seen_at: string | null; updated_at?: string | null
}

const isErroring = (w: Watch) => !!w.erroring || (w.consecutive_failures || 0) >= 3
const stateOf = (w: Watch): WatchStatus['state'] => (isErroring(w) ? 'error' : w.enabled ? 'watching' : 'paused')

export default function Watches() {
  const [params, setParams] = useSearchParams()
  const id = Number(params.get('id')) || null
  if (id) return <WatchDetail key={id} id={id} onBack={() => setParams({})} />
  return <WatchList onOpen={(wid) => setParams({ id: String(wid) })} />
}

/** Pause, or resume with the paused-period choice (DESIGN.md 11.7). */
function useWatchToggle(onDone: () => void) {
  const toast = useToast()
  const act = useAction()
  const [resuming, setResuming] = useState<number | null>(null)
  const pause = async (w: Watch) => {
    const r = await act.run(() => patch(`/api/admin/watches/${w.id}`, { enabled: false }))
    if (r !== undefined) { toast('Paused'); onDone() }
  }
  const resume = async (w: Watch, mode: 'import_missed' | 'only_new') => {
    const r = await act.run(() => patch(`/api/admin/watches/${w.id}`, { enabled: true, resume_mode: mode }))
    if (r !== undefined) { toast('Resumed'); setResuming(null); onDone() }
  }
  const checkNow = async (w: Watch) => {
    const r = await act.run(() => post(`/api/admin/watches/${w.id}/run`))
    if (r !== undefined) { toast('Check started'); window.setTimeout(onDone, 3000) }
  }
  return { act, resuming, setResuming, pause, resume, checkNow }
}

function ResumeChoice({ busy, onPick, onCancel }: { busy: boolean; onPick: (m: 'import_missed' | 'only_new') => void; onCancel: () => void }) {
  return (
    <div className="card" style={{ background: 'var(--panel2)' }}>
      <b>Resume watch</b>
      <div className="btnrow">
        <button className="btn primary" disabled={busy} onClick={() => onPick('import_missed')}>Import what was posted while paused</button>
        <button className="btn" disabled={busy} onClick={() => onPick('only_new')}>Only new from now</button>
        <button className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function WatchList({ onOpen }: { onOpen: (id: number) => void }) {
  const list = useLoad(async () => listOf<Watch>(await get('/api/admin/watches')), [])
  const [adding, setAdding] = useState(false)
  const t = useWatchToggle(() => void list.reload(true))
  const items = list.data || []
  const erroring = items.filter(isErroring)
  const resumingWatch = items.find((w) => w.id === t.resuming)

  return (
    <div>
      {isOffline(list.error) ? <div className="notice">Importer offline. Watches cannot be loaded right now.</div> : <ErrorBox error={list.error} />}
      <div className="btnrow">
        <button className="btn primary" onClick={() => setAdding((a) => !a)}>{adding ? 'Close' : 'Add watch'}</button>
        <button className="btn" onClick={() => void list.reload()}>Refresh</button>
      </div>
      {adding ? <CreateWatch onCreated={(w) => { setAdding(false); void list.reload(true); if (w?.id) onOpen(w.id) }} /> : null}
      {erroring.length ? (
        <div className="notice">{erroring.length} watch{erroring.length === 1 ? ' is' : 'es are'} failing. See the amber rows below.</div>
      ) : null}
      <ErrorBox error={t.act.error} />
      {resumingWatch ? (
        <ResumeChoice busy={t.act.busy} onCancel={() => t.setResuming(null)} onPick={(m) => void t.resume(resumingWatch, m)} />
      ) : null}
      {list.loading && !list.data ? <Spinner /> : !items.length ? (list.error ? null : <EmptyState>No watches yet</EmptyState>) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Watch</th><th>State</th><th>Last checked</th><th>Next run</th><th className="num">New</th><th className="num">Imported</th><th></th></tr>
            </thead>
            <tbody>
              {items.map((w) => (
                <tr key={w.id} className={'clickable' + (isErroring(w) ? ' warnrow' : '')} onClick={() => onOpen(w.id)}>
                  <td className="title">
                    {w.name || w.url}
                    <span className="sub">{w.kind}{w.source_domain ? ` - ${w.source_domain}` : ''} - {w.url}</span>
                    {isErroring(w) ? <span className="sub amber">Failed {w.consecutive_failures}x: {w.last_error || 'unknown error'}</span> : null}
                  </td>
                  <td><WatchIndicator state={stateOf(w)} /></td>
                  <td className="nowrap">{formatDateTime(w.last_checked_at)}</td>
                  <td className="nowrap">{w.enabled ? formatDateTime(w.next_run_at) : <span className="muted">paused</span>}</td>
                  <td className="num">{w.last_new_count ?? '-'}</td>
                  <td className="num">{w.imported_count ?? 0}</td>
                  <td className="num nowrap" onClick={(e) => e.stopPropagation()}>
                    <button className="btn small" disabled={t.act.busy} onClick={() => void t.checkNow(w)}>Check now</button>{' '}
                    {w.enabled
                      ? <button className="btn small" disabled={t.act.busy} onClick={() => void t.pause(w)}>Pause</button>
                      : <button className="btn small" disabled={t.act.busy} onClick={() => t.setResuming(w.id)}>Resume</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CreateWatch({ onCreated }: { onCreated: (w: Watch | null) => void }) {
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [tags, setTags] = useState('')
  const [mode, setMode] = useState<'new_only' | 'backfill_latest' | 'backfill_all'>('new_only')
  const [count, setCount] = useState('20')
  const [filters, setFilters] = useState<FilterDraft>(emptyDraft)
  const [enabled, setEnabled] = useState(true)
  const act = useAction()
  const submit = async () => {
    const body: Record<string, unknown> = {
      url: url.trim(), tags: splitTags(tags), initial_mode: mode, filters: filtersFromDraft(filters), enabled,
    }
    if (name.trim()) body.name = name.trim()
    if (mode === 'backfill_latest') body.backfill_count = Math.max(1, Number(count) || 20)
    const r = await act.run(() => post<Watch>('/api/admin/watches', body), 'Watch created')
    if (r !== undefined) onCreated(r)
  }
  return (
    <div className="card">
      <label className="field">Channel, channel tab or playlist URL
        <input type="url" value={url} placeholder="https://..." onChange={(e) => setUrl(e.target.value)} />
      </label>
      <label className="field">Name (optional, defaults to the channel or playlist title)
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">Extra tags (comma separated)
        <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} />
      </label>
      <label className="field">On the first run
        <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          <option value="new_only">Only import videos posted from now on</option>
          <option value="backfill_latest">Import the latest N videos</option>
          <option value="backfill_all">Import everything (capped)</option>
        </select>
      </label>
      {mode === 'backfill_latest' ? (
        <label className="field">Number of latest videos
          <input type="number" min={1} max={5000} value={count} onChange={(e) => setCount(e.target.value)} />
        </label>
      ) : null}
      <div className="section-title">Filters</div>
      <FiltersForm value={filters} onChange={setFilters} />
      <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Enabled</label>
      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy || !url.trim()} onClick={() => void submit()}>{act.busy ? 'Creating...' : 'Create watch'}</button>
      </div>
    </div>
  )
}

const ITEM_STATUSES = ['', 'baseline', 'queued', 'imported', 'skipped_exists', 'filtered', 'failed', 'unavailable']

function WatchDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const w = useLoad(() => get<Watch>(`/api/admin/watches/${id}`), [id])
  const runs = useLoad(async () => listOf<WatchRun>(await get(`/api/admin/watches/${id}/runs`, { limit: 30 })), [id])
  const [itemStatus, setItemStatus] = useState('')
  const items = useLoad(async () => listOf<WatchItem>(await get(`/api/admin/watches/${id}/items`, { status: itemStatus || undefined, limit: 300 })),
    [id, itemStatus])
  const reloadAll = () => { void w.reload(true); void runs.reload(true); void items.reload(true) }
  const t = useWatchToggle(reloadAll)
  const del_ = useAction()

  if (w.loading && !w.data) return <Spinner />
  if (!w.data) {
    return (
      <div>
        <button className="btn small" onClick={onBack}><ChevronLeft size={16} /> Watches</button>
        {isOffline(w.error) ? <div className="notice">Importer offline</div> : <ErrorBox error={w.error || 'Not found'} />}
      </div>
    )
  }
  const watch = w.data
  const remove = async () => {
    if (!window.confirm(`Delete watch "${watch.name || watch.url}"? Videos it imported are kept.`)) return
    const r = await del_.run(() => del(`/api/admin/watches/${id}`), 'Watch deleted')
    if (r !== undefined) onBack()
  }

  return (
    <div>
      <button className="btn small" onClick={onBack}><ChevronLeft size={16} /> Watches</button>
      <div className="admin-detail-head">
        <h2>{watch.name || watch.url}</h2>
        <WatchIndicator state={stateOf(watch)} />
      </div>
      <div className="muted" style={{ fontSize: 13, wordBreak: 'break-all' }}>
        <a href={watch.url} target="_blank" rel="noreferrer">{watch.url} <External /></a>
      </div>
      {isErroring(watch) ? (
        <div className="notice">Failing {watch.consecutive_failures} run{watch.consecutive_failures === 1 ? '' : 's'} in a row: {watch.last_error || 'unknown error'}</div>
      ) : watch.last_error ? <div className="muted" style={{ fontSize: 13 }}>Last error: {watch.last_error}</div> : null}

      <div className="card">
        <div className="kv">
          <span>Kind</span><span>{watch.kind}{watch.source_domain ? ` (${watch.source_domain})` : ''}</span>
          <span>Creator</span><span>{watch.creator_handle ? `${watch.creator_handle} @ ${watch.creator_source_domain}` : '-'}</span>
          <span>Created</span><span>{formatDateTime(watch.created_at)} ({watch.initial_mode}{watch.initial_mode === 'backfill_latest' && watch.backfill_count ? ` ${watch.backfill_count}` : ''})</span>
          <span>Last checked</span><span>{formatDateTime(watch.last_checked_at)}</span>
          <span>Last success</span><span>{formatDateTime(watch.last_success_at)}</span>
          <span>New last run</span><span>{watch.last_new_count ?? '-'}</span>
          <span>Next run</span><span>{watch.enabled ? formatDateTime(watch.next_run_at) : `Paused ${formatDateTime(watch.paused_at)}`}</span>
          <span>Imported</span><span>{watch.imported_count ?? 0}</span>
        </div>
        <ErrorBox error={t.act.error || del_.error} />
        <div className="btnrow">
          <button className="btn primary" disabled={t.act.busy} onClick={() => void t.checkNow(watch)}>Check now</button>
          {watch.enabled
            ? <button className="btn" disabled={t.act.busy} onClick={() => void t.pause(watch)}>Pause</button>
            : <button className="btn" disabled={t.act.busy} onClick={() => t.setResuming(watch.id)}>Resume</button>}
          <button className="btn danger" disabled={del_.busy} onClick={() => void remove()}>Delete watch</button>
        </div>
        {t.resuming === watch.id ? (
          <ResumeChoice busy={t.act.busy} onCancel={() => t.setResuming(null)} onPick={(m) => void t.resume(watch, m)} />
        ) : null}
      </div>

      <EditWatch key={`${watch.id}-${watch.name}-${JSON.stringify(watch.filters)}-${watch.tags.join(',')}`} watch={watch}
        onSaved={(nw) => w.setData(nw)} />

      <div className="section-title"><span>Run history</span><button className="btn small" onClick={() => void runs.reload()}>Refresh</button></div>
      <ErrorBox error={runs.error} />
      {runs.loading && !runs.data ? <Spinner /> : !(runs.data || []).length ? <div className="muted">No runs yet</div> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Started</th><th>Trigger</th><th className="num">Listed</th><th className="num">New</th><th className="num">Queued</th><th>Result</th></tr></thead>
            <tbody>
              {(runs.data || []).map((r) => (
                <tr key={r.id} className={r.error ? 'warnrow' : ''}>
                  <td className="nowrap">{formatDateTime(r.started_at)}</td>
                  <td>{r.trigger || '-'}</td>
                  <td className="num">{r.listed_count ?? '-'}</td>
                  <td className="num">{r.new_count ?? '-'}</td>
                  <td className="num">{r.queued_count ?? '-'}</td>
                  <td>{r.error ? <span className="amber">{r.error}</span> : r.finished_at ? 'ok' : <span className="status running">running</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="section-title">
        <span>Items</span>
        <select style={{ width: 'auto', minHeight: 34, padding: '4px 8px' }} value={itemStatus} aria-label="Item status filter"
          onChange={(e) => setItemStatus(e.target.value)}>
          {ITEM_STATUSES.map((s) => <option key={s} value={s}>{s || 'all statuses'}</option>)}
        </select>
      </div>
      <ErrorBox error={items.error} />
      {items.loading && !items.data ? <Spinner /> : !(items.data || []).length ? <div className="muted">No items</div> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Status</th><th>Video</th><th>First seen</th></tr></thead>
            <tbody>
              {(items.data || []).map((it) => (
                <tr key={it.source_video_id}>
                  <td className="nowrap"><span className={`status ${it.status}`}>{it.status.replace(/_/g, ' ')}</span></td>
                  <td className="title">
                    {it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title || it.source_video_id}</a> : (it.title || it.source_video_id)}
                    <span className="sub">{it.source_video_id}{it.job_id ? ` - job #${it.job_id}` : ''}{it.retry_count ? ` - ${it.retry_count} retries` : ''}</span>
                    {it.detail ? <span className="sub">{it.detail}</span> : null}
                  </td>
                  <td className="nowrap">{formatDateTime(it.first_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function EditWatch({ watch, onSaved }: { watch: Watch; onSaved: (w: Watch) => void }) {
  const [name, setName] = useState(watch.name || '')
  const [tags, setTags] = useState((watch.tags || []).join(', '))
  const [filters, setFilters] = useState<FilterDraft>(() => draftFromFilters(watch.filters))
  const act = useAction()
  const save = async () => {
    const r = await act.run(() => patch<Watch>(`/api/admin/watches/${watch.id}`, {
      name: name.trim() || undefined, tags: splitTags(tags), filters: filtersFromDraft(filters),
    }), 'Saved')
    if (r) onSaved(r)
  }
  return (
    <div className="card">
      <div className="section-title" style={{ marginTop: 0 }}>Settings</div>
      <label className="field">Name<input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Extra tags (comma separated)<input type="text" value={tags} onChange={(e) => setTags(e.target.value)} /></label>
      <FiltersForm value={filters} onChange={setFilters} />
      <ErrorBox error={act.error} />
      <div className="btnrow"><button className="btn primary" disabled={act.busy} onClick={() => void save()}>Save settings</button></div>
    </div>
  )
}

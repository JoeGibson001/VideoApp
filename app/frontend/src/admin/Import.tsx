import { useEffect, useState } from 'react'
import { del, get, post } from '../api'
import { EmptyState, Spinner, formatDateTime } from '../components/common'
import { ErrorBox, errText, isOffline, listOf, useAction, useLoad } from './shared'

export interface ImportJob {
  id: number
  url: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'skipped' | string
  progress: number | null
  error: string | null
  detail?: string | null
  outcome?: string | null
  video_id: number | null
  title: string | null
  watch_id?: number | null
  watch_name: string | null
  parent_id?: number | null
  created_at: string | null
  finished_at: string | null
}

interface Health { online: boolean; error?: string; version?: string; yt_dlp_version?: string; jobs?: Record<string, number> }

const ACTIVE = new Set(['queued', 'running'])

export default function Import() {
  const [urls, setUrls] = useState('')
  const [tags, setTags] = useState('')
  const [expand, setExpand] = useState(false)
  const [maxItems, setMaxItems] = useState('50')
  const [submitted, setSubmitted] = useState('')
  const act = useAction()
  const health = useLoad(() => get<Health>('/api/admin/importer/health'), [])
  const jobs = useLoad(async () => listOf<ImportJob>(await get('/api/admin/imports', { limit: 50 })), [])
  const offline = health.data?.online === false || isOffline(jobs.error)
  const list = jobs.data || []
  const anyActive = list.some((j) => ACTIVE.has(j.status))

  useEffect(() => {
    if (!anyActive) return
    const t = window.setInterval(() => void jobs.reload(true), 3000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyActive, jobs.reload])

  const submit = async () => {
    const lines = urls.split(/\r?\n/).map((u) => u.trim()).filter(Boolean)
    if (!lines.length) { act.setError('Enter at least one URL'); return }
    const body: Record<string, unknown> = {
      urls: lines,
      tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      expand_playlists: expand,
    }
    if (expand && Number(maxItems) > 0) body.max_items = Number(maxItems)
    const r = await act.run(() => post<{ job_ids?: (number | null)[] }>('/api/admin/import', body))
    if (r !== undefined) {
      const n = r?.job_ids?.length ?? lines.length
      setSubmitted(`Queued ${n} job${n === 1 ? '' : 's'}`)
      setUrls('')
      void jobs.reload(true)
      void health.reload(true)
    }
  }

  return (
    <div>
      {offline ? (
        <div className="notice">Importer offline{health.data?.error ? `: ${health.data.error}` : ''}. Imports cannot be submitted right now.</div>
      ) : health.data ? (
        <div className="muted" style={{ fontSize: 12 }}>
          Importer online{health.data.version ? ` v${health.data.version}` : ''}{health.data.yt_dlp_version ? `, yt-dlp ${health.data.yt_dlp_version}` : ''}
          {health.data.jobs ? ` - ${Object.entries(health.data.jobs).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ')}` : ''}
        </div>
      ) : null}

      <div className="card">
        <label className="field">URLs (one per line)
          <textarea value={urls} rows={5} placeholder="https://..." onChange={(e) => setUrls(e.target.value)} />
        </label>
        <label className="field">Tags (comma separated, optional)
          <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={expand} onChange={(e) => setExpand(e.target.checked)} />
          Expand playlists / profiles
        </label>
        {expand ? (
          <label className="field">Max items per playlist
            <input type="number" min={1} max={1000} value={maxItems} onChange={(e) => setMaxItems(e.target.value)} />
          </label>
        ) : null}
        <ErrorBox error={act.error} />
        {submitted ? <div className="muted">{submitted}</div> : null}
        <div className="btnrow">
          <button className="btn primary" disabled={act.busy || offline || !urls.trim()} onClick={() => void submit()}>
            {act.busy ? 'Submitting...' : 'Import'}
          </button>
        </div>
      </div>

      <div className="section-title">
        <span>Recent jobs {anyActive ? '(refreshing)' : ''}</span>
        <button className="btn small" onClick={() => void jobs.reload()}>Refresh</button>
      </div>
      {jobs.error && !isOffline(jobs.error) ? <ErrorBox error={jobs.error} /> : null}
      {jobs.loading && !jobs.data ? <Spinner /> : !list.length ? (offline ? null : <EmptyState>No import jobs</EmptyState>) : (
        <JobTable jobs={list} onChanged={() => void jobs.reload(true)} />
      )}
    </div>
  )
}

export function JobTable({ jobs, onChanged }: { jobs: ImportJob[]; onChanged: () => void }) {
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const act = async (j: ImportJob, kind: 'retry' | 'cancel') => {
    setBusy(j.id)
    setErr('')
    try {
      if (kind === 'retry') await post(`/api/admin/imports/${j.id}/retry`)
      else await del(`/api/admin/imports/${j.id}`)
      onChanged()
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(null)
    }
  }
  return (
    <>
      <ErrorBox error={err} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Status</th><th>Item</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td className="nowrap">
                  <span className={`status ${j.status}`}>{j.status}</span>
                  {j.status === 'running' && j.progress != null ? (
                    <div className="progress" style={{ marginTop: 6 }}><i style={{ width: `${Math.max(0, Math.min(100, Math.round(j.progress)))}%` }} /></div>
                  ) : null}
                </td>
                <td className="title">
                  {j.title || <a href={j.url} target="_blank" rel="noreferrer">{j.url}</a>}
                  {j.title ? <span className="sub"><a href={j.url} target="_blank" rel="noreferrer">{j.url}</a></span> : null}
                  {j.video_id ? <span className="sub">Video #{j.video_id}</span> : null}
                  {j.watch_name ? <span className="sub">via watch: {j.watch_name}</span> : null}
                  {j.error ? <span className="sub" style={{ color: '#fca5a5' }}>{j.error}</span> : null}
                  {!j.error && j.status === 'skipped' && (j.detail || j.outcome) ? <span className="sub">{j.detail || j.outcome}</span> : null}
                </td>
                <td className="nowrap">{formatDateTime(j.created_at)}{j.finished_at ? <span className="sub">done {formatDateTime(j.finished_at)}</span> : null}</td>
                <td className="num nowrap">
                  {j.status === 'failed' || j.status === 'skipped' ? (
                    <button className="btn small" disabled={busy === j.id} onClick={() => void act(j, 'retry')}>Retry</button>
                  ) : null}
                  {ACTIVE.has(j.status) ? (
                    <button className="btn small danger" disabled={busy === j.id} onClick={() => void act(j, 'cancel')}>Cancel</button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

import { useState } from 'react'
import { get, post } from '../api'
import { Spinner } from '../components/common'
import { errText, JsonView } from './shared'

interface Task {
  key: string
  label: string
  path: string
  help: string
  confirm?: string
}

const TASKS: Task[] = [
  { key: 'stats', label: 'Recompute all stats', path: '/api/admin/recompute-stats',
    help: 'Rebuilds every user\'s tag, creator and source scores from ratings.' },
  { key: 'rescan', label: 'Rescan media', path: '/api/admin/rescan-media',
    help: 'Finds files on disk with no database row (orphans) and rows whose file is missing.' },
  { key: 'rebuild', label: 'Rebuild from media', path: '/api/admin/rebuild-from-media',
    help: 'Creates database rows for every sidecar JSON in the media folder that has no video yet.',
    confirm: 'Rebuild the library from the media folder? This can take a while on a large library.' },
  { key: 'reread', label: 'Re-read all metadata', path: '/api/admin/reread-all',
    help: 'Re-applies title, description, creator and tags from each video\'s sidecar JSON.',
    confirm: 'Re-read metadata for every video? Manual edits to titles and descriptions may be overwritten.' },
]

type Result = { ok: true; data: unknown; at: Date } | { ok: false; error: string; at: Date }

export default function Maintenance() {
  const [busy, setBusy] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, Result>>({})
  const run = async (t: Task) => {
    if (t.confirm && !window.confirm(t.confirm)) return
    setBusy(t.key)
    try {
      const data = await post(t.path)
      setResults((r) => ({ ...r, [t.key]: { ok: true, data, at: new Date() } }))
    } catch (e) {
      setResults((r) => ({ ...r, [t.key]: { ok: false, error: errText(e), at: new Date() } }))
    } finally {
      setBusy(null)
    }
  }
  return (
    <div>
      <CleanupCard />
      {TASKS.map((t) => {
        const r = results[t.key]
        return (
          <div className="card" key={t.key}>
            <div className="rowline">
              <div style={{ flex: 1 }}>
                <b>{t.label}</b>
                <div className="muted" style={{ fontSize: 13 }}>{t.help}</div>
              </div>
              <button className={'btn' + (t.confirm ? ' danger' : ' primary')} disabled={busy !== null} onClick={() => void run(t)}>
                {busy === t.key ? 'Running...' : 'Run'}
              </button>
            </div>
            {busy === t.key ? <Spinner /> : null}
            {r ? (
              <div style={{ marginTop: 10 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Finished {r.at.toLocaleTimeString()}</div>
                {r.ok ? <JsonView value={r.data} /> : <div className="error">{r.error}</div>}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

interface EmptyItems {
  tags: { id: number; name: string }[]
  creators: { id: number; name: string; handle: string }[]
  sources: { id: number; name: string; domain: string }[]
}
type Kind = 'tag' | 'creator' | 'source'

/** Find and delete tags, creators and sources that have no videos. */
function CleanupCard() {
  const [found, setFound] = useState<EmptyItems | null>(null)
  const [types, setTypes] = useState<Record<Kind, boolean>>({ tag: true, creator: true, source: true })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<string | null>(null)

  const scan = async () => {
    setBusy(true); setError(''); setDone(null)
    try { setFound(await get<EmptyItems>('/api/admin/cleanup')) } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  const run = async () => {
    const chosen = (Object.keys(types) as Kind[]).filter((k) => types[k])
    setBusy(true); setError('')
    try {
      const r = await post<{ tags_deleted: number; creators_deleted: number; sources_deleted: number }>('/api/admin/cleanup', { types: chosen })
      setDone(`Deleted ${r.tags_deleted} tag(s), ${r.creators_deleted} creator(s), ${r.sources_deleted} source(s).`)
      setFound(await get<EmptyItems>('/api/admin/cleanup'))
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const groups: { kind: Kind; label: string; names: string[] }[] = found ? [
    { kind: 'tag', label: 'Tags', names: found.tags.map((t) => `#${t.name}`) },
    { kind: 'creator', label: 'Creators', names: found.creators.map((c) => `${c.name} (${c.handle})`) },
    { kind: 'source', label: 'Sources', names: found.sources.map((s) => `${s.name} (${s.domain})`) },
  ] : []
  const total = groups.reduce((n, g) => n + (types[g.kind] ? g.names.length : 0), 0)

  return (
    <div className="card">
      <div className="rowline">
        <div style={{ flex: 1 }}>
          <b>Clean up empty items</b>
          <div className="muted" style={{ fontSize: 13 }}>
            Finds tags, creators and sources with no videos and deletes them (with their aliases and category links).
            Hidden videos still count as videos. A source is only removed once it has no creators left.
          </div>
        </div>
        <button className="btn primary" disabled={busy} onClick={() => void scan()}>{found ? 'Scan again' : 'Find'}</button>
      </div>
      {busy ? <Spinner /> : null}
      {error ? <div className="error">{error}</div> : null}
      {done ? <div className="notice">{done}</div> : null}
      {found ? (
        <div style={{ marginTop: 10 }}>
          {groups.map((g) => (
            <div key={g.kind} style={{ marginBottom: 8 }}>
              <label className="check">
                <input type="checkbox" checked={types[g.kind]} onChange={(e) => setTypes((t) => ({ ...t, [g.kind]: e.target.checked }))} />
                <b>{g.label}: {g.names.length}</b>
              </label>
              {g.names.length ? (
                <div className="muted" style={{ fontSize: 12, maxHeight: 96, overflowY: 'auto' }}>{g.names.join(', ')}</div>
              ) : null}
            </div>
          ))}
          <div className="btnrow">
            <button className="btn danger" disabled={busy || total === 0}
              onClick={() => { if (window.confirm(`Delete ${total} empty item(s)? This cannot be undone.`)) void run() }}>
              {total ? `Delete ${total} empty item${total === 1 ? '' : 's'}` : 'Nothing to clean up'}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

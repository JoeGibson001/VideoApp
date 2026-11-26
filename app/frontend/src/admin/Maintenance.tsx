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
      <WipeCard />
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


interface WipePreview { videos: number; creators: number; sources: number; tags: number; ratings: number; collections: number; categories: number; phrase: string }
interface WipeResult {
  videos_deleted: number; creators_deleted: number; sources_deleted: number; tags_deleted: number; files_deleted: number
  importer: { cleared: boolean; error?: string; jobs_cancelled?: number; watches_deleted?: number; history_deleted?: number }
}

/** Delete all library content and media files, for testing. Needs the phrase typed in. */
function WipeCard() {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<WipePreview | null>(null)
  const [typed, setTyped] = useState('')
  const [userData, setUserData] = useState(false)
  const [importer, setImporter] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<WipeResult | null>(null)

  const start = async () => {
    setError(''); setDone(null); setTyped('')
    try { setPreview(await get<WipePreview>('/api/admin/wipe-all/preview')); setOpen(true) } catch (e) { setError(errText(e)) }
  }
  const run = async () => {
    setBusy(true); setError('')
    try {
      const r = await post<WipeResult>('/api/admin/wipe-all', { confirm: typed, include_user_data: userData, clear_importer: importer })
      setDone(r); setOpen(false); setPreview(null)
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  const phrase = preview?.phrase || 'DELETE ALL'

  return (
    <div className="card danger-zone">
      <div className="rowline">
        <div style={{ flex: 1 }}>
          <b>Delete all content</b>
          <div className="muted" style={{ fontSize: 13 }}>
            For testing: deletes every video, creator, source and tag, all ratings and every media file. Users and logins are kept.
          </div>
        </div>
        {!open ? <button className="btn danger" disabled={busy} onClick={() => void start()}>Delete all...</button> : null}
      </div>
      {open && preview ? (
        <div style={{ marginTop: 10 }}>
          <p style={{ margin: '0 0 8px' }}>
            This permanently deletes <b>{preview.videos}</b> videos, <b>{preview.creators}</b> creators, <b>{preview.sources}</b> sources,
            {' '}<b>{preview.tags}</b> tags and <b>{preview.ratings}</b> ratings, plus all files in the media folder. It cannot be undone.
          </p>
          <label className="check">
            <input type="checkbox" checked={importer} onChange={(e) => setImporter(e.target.checked)} />
            Also cancel running imports, delete all watches and clear the import history
          </label>
          <label className="check">
            <input type="checkbox" checked={userData} onChange={(e) => setUserData(e.target.checked)} />
            Also delete my {preview.collections} collection{preview.collections === 1 ? '' : 's'}, {preview.categories} categor{preview.categories === 1 ? 'y' : 'ies'} and recent searches
            (Favorites and Liked are always kept, empty)
          </label>
          <label className="field">Type <b>{phrase}</b> to confirm
            <input type="text" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} aria-label="Type the phrase to confirm" />
          </label>
          <div className="btnrow">
            <button className="btn" disabled={busy} onClick={() => { setOpen(false); setPreview(null) }}>Cancel</button>
            <button className="btn danger" disabled={busy || typed.trim() !== phrase} onClick={() => void run()}>
              {busy ? 'Deleting...' : 'Delete everything'}
            </button>
          </div>
        </div>
      ) : null}
      {busy ? <Spinner /> : null}
      {error ? <div className="error">{error}</div> : null}
      {done ? (
        <div className="notice">
          Deleted {done.videos_deleted} videos, {done.creators_deleted} creators, {done.sources_deleted} sources, {done.tags_deleted} tags
          and {done.files_deleted} files.
          {done.importer.cleared
            ? ` Importer: ${done.importer.jobs_cancelled} import(s) cancelled, ${done.importer.watches_deleted} watch(es) deleted, ${done.importer.history_deleted} history item(s) cleared.`
            : done.importer.error ? ` Importer was not cleared: ${done.importer.error}` : ''}
        </div>
      ) : null}
    </div>
  )
}

import { useState } from 'react'
import { post } from '../api'
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

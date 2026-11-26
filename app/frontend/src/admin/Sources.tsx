import { useState } from 'react'
import { api, get, patch } from '../api'
import { Avatar, EmptyState, Spinner } from '../components/common'
import type { SourceRef } from '../types'
import { DangerDelete, ErrorBox, FileButton, uploadForm, useAction, useLoad } from './shared'

interface AdminSource extends SourceRef { video_count: number }

export default function Sources() {
  const list = useLoad(() => get<{ items: AdminSource[] }>('/api/admin/sources'), [])
  const items = list.data?.items || []
  const update = (s: SourceRef) =>
    list.setData((d) => (d ? { items: d.items.map((x) => (x.id === s.id ? { ...x, ...s } : x)) } : d))
  return (
    <div>
      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : !items.length ? <EmptyState>No sources yet</EmptyState> : (
        items.map((s) => <SourceRow key={s.id} source={s} onUpdated={update} onDeleted={() => void list.reload(true)} />)
      )}
    </div>
  )
}

function SourceRow({ source, onUpdated, onDeleted }: {
  source: AdminSource; onUpdated: (s: SourceRef) => void; onDeleted: () => void
}) {
  const [name, setName] = useState(source.name)
  const [showDelete, setShowDelete] = useState(false)
  const act = useAction()
  const save = async () => {
    const r = await act.run(() => patch<SourceRef>(`/api/admin/sources/${source.id}`, { name }), 'Saved')
    if (r) { onUpdated(r); setName(r.name) }
  }
  const upload = async (f: File) => {
    const r = await act.run(() => api<SourceRef>(`/api/admin/sources/${source.id}/icon`, { method: 'POST', form: uploadForm(f) }), 'Icon updated')
    if (r) onUpdated(r)
  }
  return (
    <div className="card">
      <div className="admin-detail-head">
        <Avatar name={source.name} url={source.icon_url} size={40} square />
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{source.name}</b>
          <div className="muted" style={{ fontSize: 12 }}>{source.domain} - {source.video_count} video{source.video_count === 1 ? '' : 's'}</div>
        </div>
        <FileButton label="Icon..." accept="image/png,image/jpeg,image/webp,image/svg+xml,image/x-icon,.ico" disabled={act.busy}
          onFile={(f) => void upload(f)} />
      </div>
      <div className="rowline">
        <input type="text" value={name} aria-label={`Display name for ${source.domain}`} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save() }} />
        <button className="btn small primary" disabled={act.busy || !name.trim() || name === source.name} onClick={() => void save()}>Rename</button>
        <button className="btn small" aria-expanded={showDelete} onClick={() => setShowDelete((x) => !x)}>Delete...</button>
      </div>
      <ErrorBox error={act.error} />
      {showDelete ? <DangerDelete kind="source" id={source.id} onDeleted={onDeleted} /> : null}
    </div>
  )
}

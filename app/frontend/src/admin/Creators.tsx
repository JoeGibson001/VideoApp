import { useEffect, useState } from 'react'
import { api, get, patch } from '../api'
import { Avatar, EmptyState, Sheet, Spinner } from '../components/common'
import { External, Search } from '../components/Icons'
import { SearchPicker } from '../components/SearchPicker'
import { WatchSection } from '../components/WatchSection'
import { useDebounced } from '../state'
import type { CreatorRef, SourceRef } from '../types'
import { DangerDelete, ErrorBox, FileButton, MergePreview, SortSelect, scoreText, uploadForm, useAction, useLoad, useStoredState } from './shared'

interface AdminCreator extends CreatorRef {
  source: SourceRef
  video_count: number
  aliases: string[]
  score?: number
}

type Modal =
  | { kind: 'edit'; id: number }
  | { kind: 'pick'; fromIds: number[]; sourceId: number }
  | { kind: 'preview'; fromIds: number[]; intoId: number }
  | null

export default function Creators() {
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim(), 250)
  const [sort, setSort] = useStoredState('admin.creators.sort', 'name_asc')
  const list = useLoad(() => get<{ items: AdminCreator[] }>('/api/admin/creators', { q: dq, sort }), [dq, sort])
  const [selected, setSelected] = useState<number[]>([])
  const [modal, setModal] = useState<Modal>(null)
  const [selErr, setSelErr] = useState('')
  const items = list.data?.items || []
  const byId = (id: number) => items.find((c) => c.id === id)

  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const startMerge = (ids: number[]) => {
    const sources = new Set(ids.map((id) => byId(id)?.source_id))
    if (sources.size !== 1) { setSelErr('Creators can only be merged within the same source.'); return }
    setSelErr('')
    setModal({ kind: 'pick', fromIds: ids, sourceId: [...sources][0] as number })
  }
  const close = () => setModal(null)
  const editing = modal?.kind === 'edit' ? byId(modal.id) : undefined

  return (
    <div>
      <div className="admin-head-sub">
        <div className="searchbox">
          <Search />
          <input type="search" value={q} placeholder="Search creators" aria-label="Search creators" onChange={(e) => setQ(e.target.value)} />
        </div>
        <SortSelect value={sort} onChange={setSort} />
        {selected.length ? (
          <>
            <span className="muted">{selected.length} selected</span>
            <button className="btn small primary" onClick={() => startMerge(selected)}>Merge into...</button>
            <button className="btn small" onClick={() => setSelected([])}>Clear</button>
          </>
        ) : null}
      </div>
      <ErrorBox error={selErr || list.error} />
      {list.loading && !list.data ? <Spinner /> : !items.length ? <EmptyState>No creators</EmptyState> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th></th><th></th><th>Name</th><th>Source</th><th className="num">Videos</th><th className="num">Score</th></tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => setModal({ kind: 'edit', id: c.id })}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${c.name}`} checked={selected.includes(c.id)} onChange={() => toggle(c.id)} />
                  </td>
                  <td><Avatar name={c.name} url={c.avatar_url} size={32} /></td>
                  <td className="title">{c.name}<span className="sub">{c.handle}{c.aliases?.length ? ` (aka ${c.aliases.join(', ')})` : ''}</span></td>
                  <td>{c.source?.name}</td>
                  <td className="num">{c.video_count}</td>
                  <td className="num">{scoreText(c.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Sheet open={modal !== null} onClose={close} label="Creator">
        {modal?.kind === 'edit' ? (
          editing ? (
            <CreatorEditor key={editing.id} creator={editing}
              onSaved={() => void list.reload(true)}
              onMerge={() => startMerge([editing.id])}
              onDeleted={() => { close(); setSelected((s) => s.filter((x) => x !== editing.id)); void list.reload(true) }} />
          ) : <Spinner />
        ) : modal?.kind === 'pick' ? (
          <div>
            <h3>Merge {modal.fromIds.map((id) => byId(id)?.name || `#${id}`).join(', ')} into...</h3>
            <p className="muted" style={{ fontSize: 13 }}>Pick the creator that survives. Only creators from the same source are shown.</p>
            <SearchPicker types={['creator']} placeholder="Find target creator"
              exclude={(r) => modal.fromIds.includes(r.id) || (r.source_id !== undefined && r.source_id !== modal.sourceId)}
              onPick={(r) => setModal({ kind: 'preview', fromIds: modal.fromIds, intoId: r.id })} />
            <div className="btnrow"><button className="btn" onClick={close}>Cancel</button></div>
          </div>
        ) : modal?.kind === 'preview' ? (
          <MergePreview kind="creator" fromIds={modal.fromIds} intoId={modal.intoId} onCancel={close}
            onDone={() => { close(); setSelected([]); void list.reload(true) }} />
        ) : null}
      </Sheet>
    </div>
  )
}

function CreatorEditor({ creator, onSaved, onMerge, onDeleted }: {
  creator: AdminCreator; onSaved: () => void; onMerge: () => void; onDeleted: () => void
}) {
  const [name, setName] = useState(creator.name)
  const [handle, setHandle] = useState(creator.handle)
  const [url, setUrl] = useState(creator.profile_url || '')
  const [avatar, setAvatar] = useState(creator.avatar_url)
  const act = useAction()
  useEffect(() => { setAvatar(creator.avatar_url) }, [creator.avatar_url])

  const save = async () => {
    const r = await act.run(() => patch(`/api/admin/creators/${creator.id}`, { name, handle, profile_url: url }), 'Saved')
    if (r) onSaved()
  }
  const upload = async (f: File) => {
    const r = await act.run(() => api<CreatorRef>(`/api/admin/creators/${creator.id}/avatar`, { method: 'POST', form: uploadForm(f) }), 'Avatar updated')
    if (r) { setAvatar(r.avatar_url); onSaved() }
  }

  return (
    <div>
      <div className="admin-detail-head">
        <Avatar name={name || creator.name} url={avatar} size={56} />
        <h2>{creator.name}</h2>
        <FileButton label="Avatar..." accept="image/png,image/jpeg,image/webp,image/svg+xml" disabled={act.busy} onFile={(f) => void upload(f)} />
      </div>
      <div className="muted" style={{ fontSize: 13 }}>
        {creator.source?.name} - {creator.video_count} video{creator.video_count === 1 ? '' : 's'}
        {creator.aliases?.length ? ` - aliases: ${creator.aliases.join(', ')}` : ''}
      </div>
      <label className="field">Display name<input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Handle<input type="text" value={handle} onChange={(e) => setHandle(e.target.value)} /></label>
      <label className="field">Profile URL
        <div className="rowline" style={{ marginTop: 4 }}>
          <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
          {url ? <a className="btn small" href={url} target="_blank" rel="noreferrer" aria-label="Open profile"><External /></a> : null}
        </div>
      </label>
      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy} onClick={() => void save()}>Save</button>
        <button className="btn" disabled={act.busy} onClick={onMerge}>Merge into...</button>
      </div>
      <div className="section-title">Watch</div>
      <WatchSection creatorId={creator.id} />
      <DangerDelete kind="creator" id={creator.id} onDeleted={onDeleted} />
    </div>
  )
}

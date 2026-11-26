import { useState } from 'react'
import { ApiError, del, get, patch, post } from '../api'
import { EmptyState, Sheet, Spinner, formatDate } from '../components/common'
import { Close, Search } from '../components/Icons'
import { SearchPicker } from '../components/SearchPicker'
import { useDebounced } from '../state'
import { DangerDelete, ErrorBox, MergePreview, errText, useAction, useLoad } from './shared'

interface AdminTag { id: number; name: string; video_count: number; aliases: string[]; created_at?: string }
interface Duplicate {
  tag_a: { id: number; name: string; video_count: number }
  tag_b: { id: number; name: string; video_count: number }
  reason: string
}

type Modal =
  | { kind: 'detail'; id: number }
  | { kind: 'pick'; fromIds: number[] } // pick target for "Merge into..."
  | { kind: 'pickFrom'; intoId: number } // "Merge another tag into this"
  | { kind: 'preview'; fromIds: number[]; intoId: number; allowSwap?: boolean; back?: number }
  | null

export default function Tags() {
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim(), 250)
  const list = useLoad(() => get<{ items: AdminTag[] }>('/api/admin/tags', { q: dq }), [dq])
  const dups = useLoad(() => get<{ items: Duplicate[] }>('/api/admin/tags/duplicates'), [])
  const [selected, setSelected] = useState<number[]>([])
  const [modal, setModal] = useState<Modal>(null)
  const [showDups, setShowDups] = useState(true)
  const dupAct = useAction()
  const items = list.data?.items || []
  const nameOf = (id: number) => items.find((t) => t.id === id)?.name || `#${id}`
  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const close = () => setModal(null)
  const refresh = () => { void list.reload(true); void dups.reload(true) }

  const dismiss = async (d: Duplicate) => {
    const r = await dupAct.run(() => post('/api/admin/tags/duplicates/dismiss', { tag_a: d.tag_a.id, tag_b: d.tag_b.id }), 'Dismissed')
    if (r !== undefined) dups.setData((x) => (x ? { items: x.items.filter((i) => i !== d) } : x))
  }

  const dupItems = dups.data?.items || []

  return (
    <div>
      <div className="admin-head-sub">
        <div className="searchbox">
          <Search />
          <input type="search" value={q} placeholder="Search tags" aria-label="Search tags" onChange={(e) => setQ(e.target.value)} />
        </div>
        {selected.length ? (
          <>
            <span className="muted">{selected.length} selected</span>
            <button className="btn small primary" onClick={() => setModal({ kind: 'pick', fromIds: selected })}>Merge into...</button>
            <button className="btn small" onClick={() => setSelected([])}>Clear</button>
          </>
        ) : null}
      </div>

      <div className="card">
        <div className="section-title" style={{ marginTop: 0 }}>
          <span>Possible duplicates {dups.data ? `(${dupItems.length})` : ''}</span>
          <button className="btn small" onClick={() => setShowDups((s) => !s)}>{showDups ? 'Hide' : 'Show'}</button>
        </div>
        {showDups ? (
          <>
            <ErrorBox error={dups.error || dupAct.error} />
            {dups.loading && !dups.data ? <Spinner /> : !dupItems.length ? <div className="muted">No suggestions</div> : (
              <div className="table-wrap">
                <table className="table">
                  <tbody>
                    {dupItems.map((d) => (
                      <tr key={`${d.tag_a.id}-${d.tag_b.id}`}>
                        <td>
                          <b>{d.tag_a.name}</b> <span className="muted">({d.tag_a.video_count})</span>
                          {' / '}
                          <b>{d.tag_b.name}</b> <span className="muted">({d.tag_b.video_count})</span>
                          <span className="sub">{d.reason}</span>
                        </td>
                        <td className="nowrap num">
                          <button className="btn small primary" onClick={() => {
                            // default: keep the tag with more videos
                            const keepB = d.tag_b.video_count >= d.tag_a.video_count
                            setModal({ kind: 'preview', fromIds: [keepB ? d.tag_a.id : d.tag_b.id], intoId: keepB ? d.tag_b.id : d.tag_a.id, allowSwap: true })
                          }}>Merge</button>{' '}
                          <button className="btn small" disabled={dupAct.busy} onClick={() => void dismiss(d)}>Not a duplicate</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}
      </div>

      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : !items.length ? <EmptyState>No tags</EmptyState> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th></th><th>Tag</th><th className="num">Videos</th><th>Created</th></tr></thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => setModal({ kind: 'detail', id: t.id })}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${t.name}`} checked={selected.includes(t.id)} onChange={() => toggle(t.id)} />
                  </td>
                  <td className="title">#{t.name}{t.aliases.length ? <span className="sub">aliases: {t.aliases.join(', ')}</span> : null}</td>
                  <td className="num">{t.video_count}</td>
                  <td className="nowrap">{formatDate(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Sheet open={modal !== null} onClose={close} label="Tag">
        {modal?.kind === 'detail' ? (
          <TagDetail key={modal.id} id={modal.id} onChanged={refresh} onDeleted={() => { close(); refresh() }}
            onMergeInto={(intoId) => setModal({ kind: 'preview', fromIds: [modal.id], intoId, back: modal.id })}
            onMergeOther={() => setModal({ kind: 'pickFrom', intoId: modal.id })} />
        ) : modal?.kind === 'pick' ? (
          <div>
            <h3>Merge {modal.fromIds.map(nameOf).join(', ')} into...</h3>
            <SearchPicker types={['tag']} placeholder="Find target tag" exclude={(r) => modal.fromIds.includes(r.id)}
              onPick={(r) => setModal({ kind: 'preview', fromIds: modal.fromIds, intoId: r.id })} />
            <div className="btnrow"><button className="btn" onClick={close}>Cancel</button></div>
          </div>
        ) : modal?.kind === 'pickFrom' ? (
          <div>
            <h3>Merge another tag into {nameOf(modal.intoId)}</h3>
            <SearchPicker types={['tag']} placeholder="Find tag to merge in" exclude={(r) => r.id === modal.intoId}
              onPick={(r) => setModal({ kind: 'preview', fromIds: [r.id], intoId: modal.intoId, back: modal.intoId })} />
            <div className="btnrow"><button className="btn" onClick={() => setModal({ kind: 'detail', id: modal.intoId })}>Back</button></div>
          </div>
        ) : modal?.kind === 'preview' ? (
          <MergePreview key={`${modal.fromIds.join(',')}>${modal.intoId}`} kind="tag" fromIds={modal.fromIds} intoId={modal.intoId}
            allowSwap={modal.allowSwap}
            onCancel={() => (modal.back !== undefined ? setModal({ kind: 'detail', id: modal.back }) : close())}
            onDone={() => { setSelected([]); refresh(); close() }} />
        ) : null}
      </Sheet>
    </div>
  )
}

interface TagInfo { id: number; name: string; video_count: number; aliases: string[] }

function TagDetail({ id, onChanged, onDeleted, onMergeInto, onMergeOther }: {
  id: number
  onChanged: () => void
  onDeleted: () => void
  onMergeInto: (intoId: number) => void
  onMergeOther: () => void
}) {
  const { data: tag, setData, error, loading, reload } = useLoad(() => get<TagInfo>(`/api/admin/tags/${id}`), [id])
  const [name, setName] = useState<string | null>(null)
  const [conflict, setConflict] = useState<{ message: string; into: { id: number; name: string } } | null>(null)
  const [alias, setAlias] = useState('')
  const [removing, setRemoving] = useState<string | null>(null)
  const [split, setSplit] = useState(false)
  const act = useAction()

  if (loading && !tag) return <Spinner />
  if (!tag) return <ErrorBox error={error || 'Not found'} />
  const curName = name ?? tag.name

  const rename = async () => {
    setConflict(null)
    act.setError('')
    try {
      const r = await patch<{ id: number; name: string }>(`/api/admin/tags/${id}`, { name: curName })
      setData({ ...tag, name: r.name })
      setName(null)
      void reload(true)
      onChanged()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const d = e.detail as { message?: string; merge_into?: { id: number; name: string } }
        if (d?.merge_into) { setConflict({ message: d.message || 'Name exists', into: d.merge_into }); return }
      }
      act.setError(errText(e))
    }
  }
  const addAlias = async () => {
    const a = alias.trim()
    if (!a) return
    const r = await act.run(() => post(`/api/admin/tags/${id}/aliases`, { alias: a }), 'Alias added')
    if (r !== undefined) { setAlias(''); void reload(true); onChanged() }
  }
  const removeAlias = async (a: string) => {
    const r = await act.run(() => del<{ moved?: number }>(`/api/admin/tags/aliases/${encodeURIComponent(a)}`, { split: split ? 'true' : undefined }),
      split ? 'Alias removed and videos split' : 'Alias removed')
    if (r !== undefined) { setRemoving(null); setSplit(false); void reload(true); onChanged() }
  }

  return (
    <div>
      <div className="admin-detail-head">
        <h2>#{tag.name}</h2>
        <span className="muted">{tag.video_count} video{tag.video_count === 1 ? '' : 's'}</span>
      </div>
      <label className="field">Name
        <div className="rowline" style={{ marginTop: 4 }}>
          <input type="text" value={curName} onChange={(e) => { setName(e.target.value); setConflict(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') void rename() }} />
          <button className="btn small primary" disabled={act.busy || curName.trim() === tag.name} onClick={() => void rename()}>Rename</button>
        </div>
      </label>
      {conflict ? (
        <div className="notice">
          {conflict.message}. Renaming onto an existing tag is a merge.
          <div className="btnrow">
            <button className="btn small primary" onClick={() => onMergeInto(conflict.into.id)}>Preview merge into {conflict.into.name}</button>
          </div>
        </div>
      ) : null}
      <ErrorBox error={act.error} />

      <div className="section-title">Aliases</div>
      {tag.aliases.length ? (
        <div className="chips edit">
          {tag.aliases.map((a) => (
            <span className="chip" key={a}>{a}
              <button aria-label={`Remove alias ${a}`} onClick={() => { setRemoving(a); setSplit(false) }}><Close size={12} /></button>
            </span>
          ))}
        </div>
      ) : <div className="muted">No aliases</div>}
      {removing ? (
        <div className="card" style={{ background: 'var(--panel2)' }}>
          Remove alias <b>{removing}</b>? Future imports tagged <code>{removing}</code> will create a separate tag.
          <label className="check">
            <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} />
            Also split existing videos (videos originally tagged {removing} move to a recreated {removing} tag)
          </label>
          <div className="btnrow">
            <button className="btn" onClick={() => setRemoving(null)}>Cancel</button>
            <button className="btn danger" disabled={act.busy} onClick={() => void removeAlias(removing)}>Remove alias</button>
          </div>
        </div>
      ) : null}
      <div className="rowline" style={{ marginTop: 8 }}>
        <input type="text" value={alias} placeholder="Add alias" aria-label="Add alias" onChange={(e) => setAlias(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void addAlias() }} />
        <button className="btn small" disabled={act.busy || !alias.trim()} onClick={() => void addAlias()}>Add</button>
      </div>

      <div className="btnrow" style={{ marginTop: 18 }}>
        <button className="btn" onClick={onMergeOther}>Merge another tag into this</button>
      </div>
      <DangerDelete kind="tag" id={tag.id} onDeleted={onDeleted} />
    </div>
  )
}

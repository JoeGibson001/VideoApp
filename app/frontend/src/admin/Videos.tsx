import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { del, get, patch, post } from '../api'
import { Avatar, EmptyState, Sheet, Spinner, formatBytes, formatDate, formatDuration } from '../components/common'
import { Close, Search } from '../components/Icons'
import { SearchPicker } from '../components/SearchPicker'
import { useDebounced, useToast } from '../state'
import type { CreatorRef, Paged, SourceRef, VideoDetail } from '../types'
import { ErrorBox, JsonView, Pager, errText, useAction, useLoad } from './shared'

interface AdminVideoRow {
  id: number; title: string; creator: CreatorRef; source: SourceRef; poster_url: string | null; is_hidden: boolean
  duration_s: number | null; width: number | null; height: number | null; imported_at: string
}

type AdminVideo = VideoDetail & { tag_links?: { tag_id: number; name: string; origin_name: string | null; origin: string }[] }

const PAGE_SIZE = 50

export default function Videos() {
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim(), 250)
  const [hidden, setHidden] = useState<'' | 'true' | 'false'>('')
  const [page, setPage] = useState(1)
  const [params, setParams] = useSearchParams()
  const openId = Number(params.get('id')) || null
  const setOpenId = (id: number | null) => setParams(id ? { id: String(id) } : {}, { replace: true })
  useEffect(() => setPage(1), [dq, hidden])
  const list = useLoad(
    () => get<Paged<AdminVideoRow>>('/api/admin/videos', { q: dq, hidden: hidden || undefined, page, page_size: PAGE_SIZE }),
    [dq, hidden, page])

  return (
    <div>
      <div className="admin-head-sub">
        <div className="searchbox">
          <Search />
          <input type="search" value={q} placeholder="Search title, creator, id" aria-label="Search videos"
            onChange={(e) => setQ(e.target.value)} />
        </div>
        <select value={hidden} onChange={(e) => setHidden(e.target.value as typeof hidden)} aria-label="Visibility filter">
          <option value="">All</option>
          <option value="false">Visible</option>
          <option value="true">Hidden</option>
        </select>
      </div>
      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : list.data && !list.data.items.length ? <EmptyState>No videos</EmptyState> : list.data ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th></th><th>Title</th><th>Creator</th><th className="num">Length</th><th>Hidden</th></tr></thead>
              <tbody>
                {list.data.items.map((v) => (
                  <tr key={v.id} className="clickable" onClick={() => setOpenId(v.id)}>
                    <td>{v.poster_url ? <img className="thumb" src={v.poster_url} alt="" loading="lazy" /> : <div className="thumb" />}</td>
                    <td className="title">
                      <button className="linkish" style={{ all: 'unset', cursor: 'pointer' }} onClick={() => setOpenId(v.id)}>
                        {v.title || <span className="muted">(untitled)</span>}
                      </button>
                      <span className="sub">#{v.id} - {v.source?.name} - {formatDate(v.imported_at)}</span>
                    </td>
                    <td>{v.creator?.name}<span className="sub">{v.creator?.handle}</span></td>
                    <td className="num">{formatDuration(v.duration_s)}</td>
                    <td>{v.is_hidden ? <span className="status paused">hidden</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      ) : null}
      <Sheet open={openId !== null} onClose={() => setOpenId(null)} label="Edit video">
        {openId !== null ? (
          <VideoEditor key={openId} id={openId} onClose={() => setOpenId(null)}
            onChanged={() => void list.reload(true)} />
        ) : null}
      </Sheet>
    </div>
  )
}

function VideoEditor({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const { data: v, setData, error, loading } = useLoad(() => get<AdminVideo>(`/api/admin/videos/${id}`), [id])
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [creator, setCreator] = useState<{ id: number; name: string; handle?: string } | null>(null)
  const [source, setSource] = useState<{ id: number; name: string } | null>(null)
  const [tags, setTags] = useState<string[]>([])
  const [picking, setPicking] = useState<'creator' | 'source' | null>(null)
  const [reread, setReread] = useState<unknown>(null)
  const act = useAction()

  useEffect(() => {
    if (!v) return
    setTitle(v.title || '')
    setDescription(v.description || '')
    setCreator(v.creator ? { id: v.creator.id, name: v.creator.name, handle: v.creator.handle } : null)
    setSource(v.source ? { id: v.source.id, name: v.source.name } : null)
    setTags((v.tags || []).map((t) => t.name))
  }, [v])

  if (loading && !v) return <Spinner />
  if (!v) return <ErrorBox error={error || 'Not found'} />

  const save = async () => {
    const body: Record<string, unknown> = { title, description, tags }
    if (creator && creator.id !== v.creator?.id) body.creator_id = creator.id
    if (source && source.id !== v.source?.id) body.source_id = source.id
    const r = await act.run(() => patch<AdminVideo>(`/api/admin/videos/${id}`, body), 'Saved')
    if (r) { setData(r); onChanged() }
  }
  const setHidden = async (h: boolean) => {
    const r = await act.run(() => patch<AdminVideo>(`/api/admin/videos/${id}`, { is_hidden: h }), h ? 'Hidden' : 'Visible')
    if (r) { setData(r); onChanged() }
  }
  const remove = async () => {
    if (!window.confirm(`Delete "${v.title}"? This removes the file and all ratings for it. It cannot be undone.`)) return
    const r = await act.run(() => del(`/api/admin/videos/${id}`), 'Deleted')
    if (r !== undefined) { onChanged(); onClose() }
  }
  const doReread = async () => {
    const r = await act.run(() => post(`/api/admin/videos/${id}/reread`))
    if (r !== undefined) {
      setReread(r)
      toast('Metadata re-read')
      try { setData(await get<AdminVideo>(`/api/admin/videos/${id}`)) } catch { /* keep */ }
      onChanged()
    }
  }

  return (
    <div>
      <h3>Edit video #{v.id}</h3>
      {v.is_hidden ? <div className="notice">This video is hidden from feeds.</div> : null}
      <label className="field">Title<input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field">Description<textarea value={description} onChange={(e) => setDescription(e.target.value)} /></label>

      <div className="section-title">Creator</div>
      {picking === 'creator' ? (
        <div className="card">
          <SearchPicker types={['creator']} placeholder="Find creator" onPick={(r) => {
            setCreator({ id: r.id, name: r.name, handle: r.handle }); setPicking(null)
          }} />
          <button className="btn small" onClick={() => setPicking(null)}>Cancel</button>
        </div>
      ) : (
        <div className="rowline">
          <Avatar name={creator?.name || '?'} url={creator?.id === v.creator?.id ? v.creator?.avatar_url : null} size={32} />
          <span style={{ flex: 1 }}>{creator?.name} <span className="muted">{creator?.handle}</span></span>
          <button className="btn small" onClick={() => setPicking('creator')}>Change</button>
        </div>
      )}

      <div className="section-title">Source</div>
      {picking === 'source' ? (
        <div className="card">
          <SearchPicker types={['source']} placeholder="Find source" onPick={(r) => {
            setSource({ id: r.id, name: r.name }); setPicking(null)
          }} />
          <button className="btn small" onClick={() => setPicking(null)}>Cancel</button>
        </div>
      ) : (
        <div className="rowline">
          <span style={{ flex: 1 }}>{source?.name}</span>
          <button className="btn small" onClick={() => setPicking('source')}>Change</button>
        </div>
      )}

      <div className="section-title">Tags</div>
      <TagEditor tags={tags} onChange={setTags} origins={v.tag_links} />

      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy} onClick={() => void save()}>Save</button>
        <button className="btn" disabled={act.busy} onClick={() => void setHidden(!v.is_hidden)}>{v.is_hidden ? 'Unhide' : 'Hide'}</button>
      </div>
      <div className="btnrow">
        <button className="btn" disabled={act.busy} onClick={() => void doReread()}>Re-read metadata</button>
        <button className="btn danger" disabled={act.busy} onClick={() => void remove()}>Delete</button>
      </div>
      {reread ? <div className="card"><JsonView value={reread} /></div> : null}

      <div className="section-title">File</div>
      <div className="kv">
        <span>Source id</span><span>{v.source_video_id}</span>
        <span>Source URL</span><span>{v.source_url ? <a href={v.source_url} target="_blank" rel="noreferrer">{v.source_url}</a> : '-'}</span>
        <span>File</span><span>{v.file_path}</span>
        <span>Size</span><span>{formatBytes(v.size_bytes)}</span>
        <span>Dimensions</span><span>{v.width && v.height ? `${v.width}x${v.height}` : '-'}</span>
        <span>Duration</span><span>{formatDuration(v.duration_s)}</span>
        <span>Uploaded</span><span>{formatDate(v.upload_date)}</span>
        <span>Imported</span><span>{formatDate(v.imported_at)}</span>
      </div>
      <div className="btnrow"><button className="btn" onClick={onClose}>Close</button></div>
    </div>
  )
}

interface ResolveResult { name: string; tag: { id: number; name: string } | null; via_alias: boolean }

/** Tag chips with remove, free-text add (alias aware) and a fuzzy picker (DESIGN.md 9.1). */
export function TagEditor({ tags, onChange, origins }: {
  tags: string[]
  onChange: (t: string[]) => void
  origins?: { name: string; origin_name: string | null }[]
}) {
  const toast = useToast()
  const [text, setText] = useState('')
  const dtext = useDebounced(text.trim(), 250)
  const [hint, setHint] = useState<ResolveResult | null>(null)
  const [browse, setBrowse] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!dtext) { setHint(null); return }
    let live = true
    get<ResolveResult>('/api/admin/tags/resolve', { name: dtext })
      .then((r) => { if (live) setHint(r) })
      .catch(() => { if (live) setHint(null) })
    return () => { live = false }
  }, [dtext])

  const has = (n: string) => tags.some((t) => t.toLowerCase() === n.toLowerCase())
  const addName = (n: string) => {
    if (!n) return
    if (has(n)) { toast(`Already tagged ${n}`); return }
    onChange([...tags, n])
  }
  const addTyped = async () => {
    const raw = text.trim()
    if (!raw) return
    setErr('')
    try {
      const r = await get<ResolveResult>('/api/admin/tags/resolve', { name: raw })
      if (!r.name) { setErr('Empty tag name'); return }
      if (r.tag && r.via_alias) toast(`${r.name} is merged into ${r.tag.name}`)
      addName(r.tag ? r.tag.name : r.name)
      setText('')
      setHint(null)
    } catch (e) {
      setErr(errText(e))
    }
  }
  const originOf = (n: string) => origins?.find((o) => o.name === n && o.origin_name && o.origin_name !== n)?.origin_name

  return (
    <div>
      <div className="chips edit">
        {tags.length ? tags.map((t) => (
          <span className="chip" key={t} title={originOf(t) ? `Originally "${originOf(t)}"` : undefined}>
            #{t}{originOf(t) ? <span className="muted"> ({originOf(t)})</span> : null}
            <button aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter((x) => x !== t))}><Close size={12} /></button>
          </span>
        )) : <span className="muted">No tags</span>}
      </div>
      <div className="rowline" style={{ marginTop: 8 }}>
        <input type="text" value={text} placeholder="Add tag" aria-label="Add tag"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void addTyped() } }} />
        <button className="btn small" disabled={!text.trim()} onClick={() => void addTyped()}>Add</button>
        <button className="btn small" onClick={() => setBrowse((b) => !b)}>{browse ? 'Hide' : 'Browse'}</button>
      </div>
      {hint && hint.name && dtext === text.trim() ? (
        hint.tag && hint.via_alias
          ? <div className="notice"><code>{hint.name}</code> is merged into <code>{hint.tag.name}</code>. Adding it will use <b>{hint.tag.name}</b>.</div>
          : !hint.tag ? <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>New tag <b>{hint.name}</b> will be created.</div> : null
      ) : null}
      <ErrorBox error={err} />
      {browse ? (
        <div className="card">
          <SearchPicker types={['tag']} placeholder="Find tag" autoFocus={false}
            exclude={(r) => has(r.name)} onPick={(r) => addName(r.name)} />
        </div>
      ) : null}
    </div>
  )
}

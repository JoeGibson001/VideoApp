import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, get, patch, put } from '../api'
import { Avatar, EmptyState, Spinner } from '../components/common'
import { ChevronLeft, External, Search } from '../components/Icons'
import { WatchIndicator, WatchSection } from '../components/WatchSection'
import { useDebounced, useToast } from '../state'
import type { SourceRef, WatchStatus } from '../types'
import { DangerDelete, ErrorBox, FileButton, Pager, uploadForm, useAction, useLoad } from './shared'

interface AdminSource extends SourceRef {
  video_count: number
  creator_count: number
  creator_url_template: string | null
  tag_url_template: string | null
  example_creator_url: string | null
  example_tag_url: string | null
}

export default function Sources() {
  const [params, setParams] = useSearchParams()
  const openId = Number(params.get('id')) || null
  const list = useLoad(() => get<{ items: AdminSource[] }>('/api/admin/sources'), [])
  const items = list.data?.items || []
  const open = (id: number | null) => setParams(id ? { id: String(id) } : {}, { replace: false })

  if (openId) {
    return <SourceDetail id={openId} onBack={() => { open(null); void list.reload(true) }} />
  }
  return (
    <div>
      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : !items.length ? <EmptyState>No sources yet</EmptyState> : (
        items.map((s) => (
          <button key={s.id} className="colcard" onClick={() => open(s.id)}>
            <Avatar name={s.name} url={s.icon_url} size={40} square />
            <span className="t">
              <b>{s.name}</b>
              <small>
                {s.domain} - {s.video_count} video{s.video_count === 1 ? '' : 's'}, {s.creator_count} creator{s.creator_count === 1 ? '' : 's'}
                {s.creator_url_template || s.tag_url_template ? ' - URL templates set' : ''}
              </small>
            </span>
          </button>
        ))
      )}
    </div>
  )
}

const CREATOR_HELP = 'Placeholders: {handle} (as stored, e.g. @bob), {handle_bare} (without @), {name}.'
const TAG_HELP = 'Placeholders: {tag} (spaces as %20), {tag_plus} (spaces as +), {tag_dash} (spaces as -).'

function SourceDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const src = useLoad(() => get<AdminSource>(`/api/admin/sources/${id}`), [id])
  const s = src.data
  return (
    <div>
      <div className="btnrow" style={{ marginTop: 0 }}>
        <button className="btn small" style={{ flex: 'none' }} onClick={onBack}><ChevronLeft size={16} /> All sources</button>
      </div>
      <ErrorBox error={src.error} />
      {!s ? (src.error ? null : <Spinner />) : (
        <>
          <GeneralCard source={s} onSaved={(n) => src.setData(n)} />
          <TemplatesCard source={s} onSaved={(n) => src.setData(n)} />
          <SourceTags source={s} />
          <DangerDelete kind="source" id={s.id} onDeleted={onBack} />
        </>
      )}
    </div>
  )
}

function GeneralCard({ source, onSaved }: { source: AdminSource; onSaved: (s: AdminSource) => void }) {
  const [name, setName] = useState(source.name)
  const act = useAction()
  const save = async () => {
    const r = await act.run(() => patch<AdminSource>(`/api/admin/sources/${source.id}`, { name }), 'Saved')
    if (r) onSaved(r)
  }
  const upload = async (f: File) => {
    const r = await act.run(() => api<SourceRef>(`/api/admin/sources/${source.id}/icon`, { method: 'POST', form: uploadForm(f) }), 'Icon updated')
    if (r) onSaved({ ...source, ...r })
  }
  return (
    <div className="card">
      <div className="admin-detail-head">
        <Avatar name={source.name} url={source.icon_url} size={48} square />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ margin: 0 }}>{source.name}</h2>
          <div className="muted" style={{ fontSize: 12 }}>
            {source.domain} - {source.video_count} video{source.video_count === 1 ? '' : 's'}, {source.creator_count} creator{source.creator_count === 1 ? '' : 's'}
          </div>
        </div>
        <FileButton label="Icon..." accept="image/png,image/jpeg,image/webp,image/svg+xml,image/x-icon,.ico" disabled={act.busy}
          onFile={(f) => void upload(f)} />
      </div>
      <div className="rowline">
        <input type="text" value={name} aria-label={`Display name for ${source.domain}`} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save() }} />
        <button className="btn small primary" disabled={act.busy || !name.trim() || name === source.name} onClick={() => void save()}>Rename</button>
      </div>
      <ErrorBox error={act.error} />
    </div>
  )
}

function TemplatesCard({ source, onSaved }: { source: AdminSource; onSaved: (s: AdminSource) => void }) {
  const [creatorTpl, setCreatorTpl] = useState(source.creator_url_template || '')
  const [tagTpl, setTagTpl] = useState(source.tag_url_template || '')
  const act = useAction()
  const toast = useToast()
  const creatorChanged = creatorTpl.trim() !== (source.creator_url_template || '')
  const dirty = creatorChanged || tagTpl.trim() !== (source.tag_url_template || '')
  const n = source.creator_count

  const save = async () => {
    if (creatorChanged && n > 0) {
      const msg = creatorTpl.trim()
        ? `This sets the profile URL of all ${n} creator${n === 1 ? '' : 's'} on ${source.name} from the new template, replacing their current URLs. Continue?`
        : `Clear the template? Profile URLs it created on ${source.name} will be removed; URLs edited by hand are kept.`
      if (!window.confirm(msg)) return
    }
    const r = await act.run(() => patch<AdminSource & { creators_updated: number }>(`/api/admin/sources/${source.id}`,
      { creator_url_template: creatorTpl, tag_url_template: tagTpl }))
    if (r) {
      onSaved(r)
      toast(r.creators_updated ? `Saved, ${r.creators_updated} creator URL${r.creators_updated === 1 ? '' : 's'} updated` : 'URL templates saved')
    }
  }

  return (
    <div className="card">
      <div className="section-title" style={{ marginTop: 0 }}>URL templates</div>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 8px' }}>
        For sites where yt-dlp does not give a URL. Paths are added to https://{source.domain}; a full https:// URL also works.
      </p>
      <label className="field">Creator profile URL
        <input type="text" value={creatorTpl} placeholder="/users/{handle}" onChange={(e) => setCreatorTpl(e.target.value)} />
        <span style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
          {CREATOR_HELP} Saving a change applies it to all {n} existing creator{n === 1 ? '' : 's'} of this source, and new creators get it on import.
        </span>
      </label>
      {source.example_creator_url && !dirty ? <div className="muted" style={{ fontSize: 12 }}>Example: {source.example_creator_url}</div> : null}
      <label className="field">Tag page URL
        <input type="text" value={tagTpl} placeholder="/browse?tags={tag_plus}" onChange={(e) => setTagTpl(e.target.value)} />
        <span style={{ display: 'block', fontSize: 12, marginTop: 4 }}>{TAG_HELP}</span>
      </label>
      {source.example_tag_url && !dirty ? <div className="muted" style={{ fontSize: 12 }}>Example: {source.example_tag_url}</div> : null}
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy || !dirty} onClick={() => void save()}>Save templates</button>
      </div>
      <ErrorBox error={act.error} />
    </div>
  )
}

interface SourceTagRow {
  id: number; name: string; video_count: number; custom_url: string | null; url: string | null; watch: WatchStatus
}

function SourceTags({ source }: { source: AdminSource }) {
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim(), 200)
  const [sort, setSort] = useState<'videos' | 'name'>('videos')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<number | null>(null)
  const pageSize = 100
  const list = useLoad(() => get<{ items: SourceTagRow[]; total: number }>(`/api/admin/sources/${source.id}/tags`,
    { q: dq, sort, page, page_size: pageSize }), [source.id, dq, sort, page, source.tag_url_template])
  const rows = list.data?.items || []
  const update = (row: SourceTagRow) =>
    list.setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === row.id ? row : x)) } : d))

  return (
    <div className="card">
      <div className="section-title" style={{ marginTop: 0 }}>
        <span>Tags used on {source.name}{list.data ? ` (${list.data.total})` : ''}</span>
      </div>
      {!source.tag_url_template ? (
        <div className="notice" style={{ fontSize: 13 }}>Set a tag page URL template above, or give individual tags a URL, to watch them.</div>
      ) : null}
      <div className="toolbar">
        <div className="searchbox" style={{ flex: 1, margin: 0 }}>
          <Search />
          <input type="search" value={q} placeholder="Filter tags" aria-label="Filter tags" onChange={(e) => { setQ(e.target.value); setPage(1) }} />
        </div>
        <select value={sort} aria-label="Sort tags" style={{ width: 'auto', flex: 'none' }}
          onChange={(e) => { setSort(e.target.value as 'videos' | 'name'); setPage(1) }}>
          <option value="videos">Most videos</option>
          <option value="name">Name</option>
        </select>
      </div>
      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : !rows.length ? <EmptyState>No tags</EmptyState> : rows.map((t) => (
        <SourceTagItem key={t.id} sourceId={source.id} sourceName={source.name} row={t} open={open === t.id}
          onToggle={() => setOpen((o) => (o === t.id ? null : t.id))} onChanged={update} />
      ))}
      {list.data && list.data.total > pageSize ? (
        <Pager page={page} pageSize={pageSize} total={list.data.total} onPage={setPage} />
      ) : null}
    </div>
  )
}

function SourceTagItem({ sourceId, sourceName, row, open, onToggle, onChanged }: {
  sourceId: number; sourceName: string; row: SourceTagRow; open: boolean; onToggle: () => void; onChanged: (r: SourceTagRow) => void
}) {
  const [url, setUrl] = useState(row.custom_url || '')
  const act = useAction()
  const saveUrl = async (value: string) => {
    const r = await act.run(() => put<{ custom_url: string | null; url: string | null }>(
      `/api/admin/sources/${sourceId}/tags/${row.id}`, { url: value }), value ? 'Tag URL saved' : 'Tag URL reset')
    if (r) { onChanged({ ...row, custom_url: r.custom_url, url: r.url }); setUrl(r.custom_url || '') }
  }
  return (
    <div style={{ borderBottom: '1px solid var(--line)' }}>
      <button className="itemrow-main" style={{ width: '100%' }} onClick={onToggle} aria-expanded={open}>
        <span className="rowicon" aria-hidden="true">#</span>
        <span className="itemrow-text">
          <span className="itemrow-name">{row.name}</span>
          <small>{row.video_count} video{row.video_count === 1 ? '' : 's'}{row.custom_url ? ' - custom URL' : ''}{row.url ? '' : ' - no URL'}</small>
        </span>
        {row.watch.state !== 'none' ? <WatchIndicator state={row.watch.state} /> : null}
      </button>
      {open ? (
        <div style={{ padding: '0 2px 12px' }}>
          <div className="kv" style={{ marginBottom: 8 }}>
            <span>Tag page</span>
            <span style={{ wordBreak: 'break-all' }}>
              {row.url ? <a href={row.url} target="_blank" rel="noreferrer noopener">{row.url} <External /></a> : 'No URL (set a template or a custom URL)'}
            </span>
          </div>
          <label className="field">Custom URL for this tag on {sourceName} (overrides the template)
            <div className="rowline" style={{ marginTop: 4 }}>
              <input type="url" value={url} placeholder={row.url || 'https://...'} onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void saveUrl(url) }} />
              <button className="btn small primary" disabled={act.busy || url === (row.custom_url || '')} onClick={() => void saveUrl(url)}>Save</button>
              {row.custom_url ? <button className="btn small" disabled={act.busy} onClick={() => void saveUrl('')}>Reset</button> : null}
            </div>
          </label>
          <ErrorBox error={act.error} />
          <div className="section-title">Watch</div>
          {row.url ? (
            <WatchSection endpoint={`/api/admin/sources/${sourceId}/tags/${row.id}/watch`} defaultTags={[row.name]}
              description={`${row.url} is checked once a day for new videos. Imports are tagged #${row.name} by default.`} />
          ) : <div className="muted" style={{ fontSize: 13 }}>Needs a tag page URL before it can be watched.</div>}
        </div>
      ) : null}
    </div>
  )
}

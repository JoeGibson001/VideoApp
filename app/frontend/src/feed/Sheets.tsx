import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { del, errorMessage, get, patch, post } from '../api'
import {
  aspectLabel, Avatar, formatBytes, formatDate, formatDuration, ScoreBadge, scoreClass, Sheet, signed, Spinner,
} from '../components/common'
import { External } from '../components/Icons'
import { WatchSection } from '../components/WatchSection'
import { useAuth, useToast } from '../state'
import type { CollectionSummary, LibraryRow, SourceRef, VideoDetail, VideoSummary } from '../types'

export function DetailsSheet({ open, video, rating, onClose, onClear, onSave, onCreator, onTag }: {
  open: boolean
  video: VideoSummary | undefined
  rating: 1 | -1 | null
  onClose: () => void
  onClear: () => void
  onSave: () => void
  onCreator: (creatorId: number) => void
  onTag: (tagId: number) => void
}) {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [d, setD] = useState<VideoDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !video) return
    setD(null)
    setErr(null)
    get<VideoDetail>(`/api/videos/${video.id}`).then(setD).catch((e) => setErr(errorMessage(e)))
  }, [open, video, rating])

  const hide = async () => {
    if (!video || !confirm('Hide this video from all feeds?')) return
    try {
      await patch(`/api/admin/videos/${video.id}`, { is_hidden: true })
      toast('Video hidden')
      onClose()
    } catch (e) { toast(errorMessage(e)) }
  }

  const v = d
  return (
    <Sheet open={open} onClose={onClose} label="Video details">
      {err ? <div className="error">{err}</div> : null}
      {!v ? (err ? null : <Spinner />) : (
        <div data-testid="details-sheet">
          <h3>{v.title}</h3>
          {v.description ? <p className="desc">{v.description}</p> : null}

          <h4>Tags{user ? ' (your score)' : ''}</h4>
          <div className="tagrow">
            {v.tags.map((t) => (
              <button key={t.id} className="chip" onClick={() => { onClose(); onTag(t.id) }}>
                #{t.name}{user ? <b className={scoreClass(t.score)}>{signed(t.score)}</b> : null}
              </button>
            ))}
          </div>

          <h4>Creator and source</h4>
          <button className="creator-line" style={{ margin: '0 0 10px' }} onClick={() => onCreator(v.creator.id)}>
            <Avatar name={v.creator.name} url={v.creator.avatar_url} size={36} />
            <span>
              <span className="cname">{v.creator.name}</span>{' '}
              {user ? <ScoreBadge score={v.creator_score} /> : null}
              <span className="csrc" style={{ display: 'block' }}>{v.creator.handle}</span>
            </span>
          </button>
          <div className="kv">
            {v.creator.profile_url ? <><span>Profile</span><span><a href={v.creator.profile_url} target="_blank" rel="noreferrer noopener">Open profile <External /></a></span></> : null}
            <span>Source</span><span><SourceLabel s={v.source} /> {user ? <ScoreBadge score={v.source_score} /> : null}</span>
            {v.source_url ? <><span>Original</span><span><a href={v.source_url} target="_blank" rel="noreferrer noopener">Open on {v.source.name} <External /></a></span></> : null}
          </div>

          <h4>File</h4>
          <div className="kv">
            <span>Filename</span><span>{v.filename || '-'}</span>
            {v.original_filename && v.original_filename !== v.filename ? <><span>Original name</span><span>{v.original_filename}</span></> : null}
            <span>Size</span><span>{formatBytes(v.size_bytes)}</span>
            <span>Dimensions</span><span>{v.width && v.height ? `${v.width} x ${v.height}` : '-'}</span>
            <span>Aspect ratio</span><span>{aspectLabel(v.width, v.height)}</span>
            <span>Duration</span><span>{formatDuration(v.duration_s)}</span>
            <span>Frame rate</span><span>{v.fps ? `${Math.round(v.fps * 100) / 100} fps` : '-'}</span>
            <span>Video codec</span><span>{v.video_codec || '-'}</span>
            <span>Audio codec</span><span>{v.audio_codec || 'none'}</span>
            <span>Has sound</span><span>{v.has_audio ? 'Yes' : 'No'}</span>
            <span>Uploaded</span><span>{formatDate(v.upload_date)}</span>
            <span>Imported</span><span>{formatDate(v.imported_at)}</span>
          </div>

          <h4>You</h4>
          <div className="kv">
            <span>Your rating</span>
            <span className={rating ? scoreClass(rating) : 'zero'} data-testid="details-rating">
              {rating ? (rating > 0 ? 'Liked' : 'Disliked') : 'Not rated'}
            </span>
            {user ? <><span>In collections</span><span>{v.collections.map((c) => c.name).join(', ') || 'None'}</span></> : null}
          </div>
          <div className="btnrow">
            <button className="btn" onClick={() => { onClear(); onClose() }} disabled={!rating}>Clear rating</button>
            <button className="btn" onClick={onSave}>Add to collection</button>
          </div>
          {user?.is_admin ? (
            <div className="btnrow">
              <button className="btn" onClick={() => { onClose(); navigate(`/admin/videos?id=${v.id}`) }}>Edit metadata</button>
              <button className="btn danger" onClick={() => void hide()}>Hide video</button>
            </div>
          ) : null}
        </div>
      )}
    </Sheet>
  )
}

function SourceLabel({ s }: { s: SourceRef }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {s.icon_url ? <img src={s.icon_url} alt="" width={16} height={16} style={{ borderRadius: 4 }} /> : null}
      {s.name} ({s.domain})
    </span>
  )
}

export function SaveSheet({ open, video, liked, favorite, onToggleFavorite, onClose }: {
  open: boolean; video: VideoSummary | undefined; liked: boolean; favorite: boolean
  onToggleFavorite: () => void; onClose: () => void
}) {
  const toast = useToast()
  const [cols, setCols] = useState<CollectionSummary[] | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (!open || !video) return
    setCols(null)
    get<{ items: CollectionSummary[] }>('/api/collections', { video_id: video.id })
      .then((r) => setCols(r.items)).catch((e) => toast(errorMessage(e)))
  }, [open, video, toast])

  const toggle = async (c: CollectionSummary, on: boolean) => {
    if (!video) return
    if (c.system_type === 'favorites') { onToggleFavorite(); return }
    setCols((cs) => cs?.map((x) => (x.id === c.id ? { ...x, contains_video: on } : x)) || null)
    try {
      if (on) await post(`/api/collections/${c.id}/videos`, { video_id: video.id })
      else await del(`/api/collections/${c.id}/videos/${video.id}`)
      toast(on ? `Added to ${c.name}` : `Removed from ${c.name}`)
    } catch (e) {
      setCols((cs) => cs?.map((x) => (x.id === c.id ? { ...x, contains_video: !on } : x)) || null)
      toast(errorMessage(e))
    }
  }

  const create = async () => {
    const name = newName.trim()
    if (!name || !video) return
    try {
      const c = await post<CollectionSummary>('/api/collections', { name })
      await post(`/api/collections/${c.id}/videos`, { video_id: video.id })
      setCols((cs) => [...(cs || []), { ...c, contains_video: true, video_count: 1 }])
      setNewName('')
      setCreating(false)
      toast(`Added to ${name}`)
    } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <Sheet open={open} onClose={onClose} label="Save to collection">
      <h3>Save to collection</h3>
      {!cols ? <Spinner /> : cols.map((c) => {
        const checked = c.system_type === 'favorites' ? favorite : c.system_type === 'liked' ? liked : !!c.contains_video
        const sub = c.system_type === 'favorites' ? 'Your manual favorites'
          : c.system_type === 'liked' ? 'Automatic, follows your likes' : `${c.video_count} video${c.video_count === 1 ? '' : 's'}`
        return (
          <label key={c.id} className="colrow">
            <span>{c.name}<small>{sub}</small></span>
            <input type="checkbox" checked={checked} disabled={c.system_type === 'liked'}
              onChange={(e) => void toggle(c, e.target.checked)} aria-label={c.name} />
          </label>
        )
      })}
      {creating ? (
        <form className="btnrow" onSubmit={(e) => { e.preventDefault(); void create() }}>
          <input type="text" autoFocus placeholder="Collection name" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button className="btn primary" type="submit" style={{ flex: 'none' }}>Create</button>
        </form>
      ) : (
        <div className="btnrow"><button className="btn" onClick={() => setCreating(true)}>+ New collection</button></div>
      )}
    </Sheet>
  )
}

export function CreatorSheet({ open, creatorId, onClose, onPlay }: {
  open: boolean; creatorId: number | null; onClose: () => void; onPlay: (id: number) => void
}) {
  const { user } = useAuth()
  const [c, setC] = useState<(LibraryRow & { profile_url: string | null; source: SourceRef }) | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!open || !creatorId) return
    setC(null)
    setErr(null)
    get<LibraryRow & { profile_url: string | null; source: SourceRef }>(`/api/creators/${creatorId}`)
      .then(setC).catch((e) => setErr(errorMessage(e)))
  }, [open, creatorId])
  return (
    <Sheet open={open} onClose={onClose} label="Creator">
      {err ? <div className="error">{err}</div> : null}
      {!c ? (err ? null : <Spinner />) : (
        <div data-testid="creator-sheet">
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <Avatar name={c.name} url={c.avatar_url} size={56} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 style={{ margin: 0 }}>{c.name}</h3>
              <div className="muted">{c.handle} &middot; {c.source.name}</div>
            </div>
            {user ? <ScoreBadge score={c.score} /> : null}
          </div>
          <div className="kv" style={{ marginTop: 12 }}>
            <span>Videos</span><span>{c.video_count}</span>
            {user ? <><span>Likes / dislikes</span><span>{c.likes} / {c.dislikes}</span></> : null}
            {c.profile_url ? <><span>Profile</span><span><a href={c.profile_url} target="_blank" rel="noreferrer noopener">Open profile <External /></a></span></> : null}
          </div>
          <div className="btnrow"><button className="btn primary" onClick={() => onPlay(c.id)}>Play creator feed</button></div>
          {user?.is_admin ? (<><h4>Watch</h4><WatchSection creatorId={c.id} /></>) : null}
        </div>
      )}
    </Sheet>
  )
}

export function LoginSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onClose={onClose} label="Log in">
      <h3>Log in to save</h3>
      <p className="muted">Favorites, collections and saved likes need an account.</p>
      <div className="btnrow">
        <Link className="btn primary" to="/login" onClick={onClose}>Log in</Link>
        <button className="btn" onClick={onClose}>Not now</button>
      </div>
    </Sheet>
  )
}

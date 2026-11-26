import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { del, errorMessage, get, patch, put } from '../api'
import { EmptyState, PageHeader, Spinner } from '../components/common'
import { ChevronLeft, Close } from '../components/Icons'
import { useToast } from '../state'
import type { CollectionSummary, VideoSummary } from '../types'

type Detail = CollectionSummary & { videos: VideoSummary[] }

export default function CollectionPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [col, setCol] = useState<Detail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [dragId, setDragId] = useState<number | null>(null)
  const [overId, setOverId] = useState<number | null>(null)
  const touchDrag = useRef<{ id: number } | null>(null)

  const load = useCallback(() => {
    get<Detail>(`/api/collections/${id}`).then((c) => { setCol(c); setName(c.name) }).catch((e) => setErr(errorMessage(e)))
  }, [id])
  useEffect(load, [load])

  const isLiked = col?.system_type === 'liked'
  const canReorder = col && !isLiked

  const remove = async (v: VideoSummary) => {
    if (isLiked && !confirm('Removing from Liked clears your like on this video. Continue?')) return
    try {
      await del(`/api/collections/${id}/videos/${v.id}`)
      setCol((c) => (c ? { ...c, videos: c.videos.filter((x) => x.id !== v.id), video_count: c.video_count - 1 } : c))
      toast(isLiked ? 'Rating cleared' : 'Removed')
    } catch (e) { toast(errorMessage(e)) }
  }

  const move = async (from: number, to: number) => {
    if (!col || from === to) return
    const vids = [...col.videos]
    const fi = vids.findIndex((v) => v.id === from)
    const ti = vids.findIndex((v) => v.id === to)
    if (fi < 0 || ti < 0) return
    const [item] = vids.splice(fi, 1)
    vids.splice(ti, 0, item)
    setCol({ ...col, videos: vids })
    try { await put(`/api/collections/${id}/order`, { video_ids: vids.map((v) => v.id) }) } catch (e) { toast(errorMessage(e)); load() }
  }

  const rename = async () => {
    try { await patch(`/api/collections/${id}`, { name }); setEditing(false); load() } catch (e) { toast(errorMessage(e)) }
  }
  const deleteCol = async () => {
    if (!confirm('Delete this collection? The videos themselves are not deleted.')) return
    try { await del(`/api/collections/${id}`); navigate('/collections') } catch (e) { toast(errorMessage(e)) }
  }

  // Touch reordering: long-press-free drag using pointer events on the grip area of each tile.
  const onPointerMove = (e: RPointerEvent) => {
    if (!touchDrag.current) return
    const el = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-tile-id]')
    if (el) setOverId(Number(el.dataset.tileId))
  }
  const onPointerUp = () => {
    if (touchDrag.current && overId !== null) void move(touchDrag.current.id, overId)
    touchDrag.current = null
    setDragId(null)
    setOverId(null)
  }

  return (
    <div className="page" onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
      <PageHeader title={col?.name || 'Collection'}
        left={<button className="iconbtn plain" aria-label="Back" onClick={() => navigate('/collections')}><ChevronLeft /></button>} />
      <div className="page-inner">
        {err ? <div className="error">{err}</div> : null}
        {!col ? (err ? null : <Spinner />) : (
          <>
            {isLiked ? <p className="muted" style={{ fontSize: 13 }}>Liked automatically. Most recently liked first. Swipe right on a video to add it here.</p> : null}
            {editing ? (
              <form className="btnrow" onSubmit={(e) => { e.preventDefault(); void rename() }}>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)} aria-label="Collection name" />
                <button className="btn primary" type="submit" style={{ flex: 'none' }}>Save</button>
              </form>
            ) : null}
            <div className="btnrow">
              <button className="btn primary" disabled={!col.videos.length} onClick={() => navigate(`/feed/collection/${col.id}`)}>Play</button>
              {!col.system_type ? <button className="btn" onClick={() => setEditing((x) => !x)}>Rename</button> : null}
              {!col.system_type ? <button className="btn danger" onClick={() => void deleteCol()}>Delete</button> : null}
            </div>
            {canReorder && col.videos.length > 1 ? <p className="muted" style={{ fontSize: 12 }}>Drag tiles to reorder.</p> : null}
            {!col.videos.length ? <EmptyState>No videos yet</EmptyState> : (
              <div className="grid">
                {col.videos.map((v) => (
                  <div key={v.id} data-tile-id={v.id}
                    className={'tile' + (dragId === v.id ? ' dragging' : '') + (overId === v.id && dragId !== v.id ? ' dragover' : '')}
                    draggable={!!canReorder}
                    onDragStart={() => setDragId(v.id)}
                    onDragOver={(e) => { if (canReorder) { e.preventDefault(); setOverId(v.id) } }}
                    onDrop={(e) => { e.preventDefault(); if (dragId !== null) void move(dragId, v.id); setDragId(null); setOverId(null) }}
                    onDragEnd={() => { setDragId(null); setOverId(null) }}
                    onPointerDown={(e) => {
                      if (canReorder && e.pointerType !== 'mouse' && (e.target as HTMLElement).closest('.tile-grip')) {
                        touchDrag.current = { id: v.id }
                        setDragId(v.id)
                      }
                    }}>
                    <button style={{ all: 'unset', display: 'block', width: '100%', height: '100%', cursor: 'pointer' }}
                      onClick={() => navigate(`/feed/collection/${col.id}`)} aria-label={`Play ${v.title}`}>
                      {v.poster_url ? <img src={v.poster_url} alt="" loading="lazy" draggable={false} /> : null}
                      <span className="tile-title">{v.title}</span>
                    </button>
                    {canReorder ? <span className="tile-grip" style={{ position: 'absolute', left: 0, top: 0, width: 44, height: 44, touchAction: 'none' }} aria-hidden="true" /> : null}
                    <button className="iconbtn tile-x" aria-label={isLiked ? `Unlike ${v.title}` : `Remove ${v.title}`} onClick={() => void remove(v)}><Close size={14} /></button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

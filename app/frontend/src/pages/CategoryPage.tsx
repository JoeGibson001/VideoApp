import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { del, errorMessage, get, patch, post } from '../api'
import { EmptyState, PageHeader, ScoreBadge, Sheet, Spinner } from '../components/common'
import { ChevronLeft, Close } from '../components/Icons'
import { SearchPicker } from '../components/SearchPicker'
import { useToast } from '../state'
import type { CategoryDetail, LibraryRow } from '../types'

export default function CategoryPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [cat, setCat] = useState<CategoryDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')

  const load = useCallback(() => {
    get<CategoryDetail>(`/api/categories/${id}`).then((c) => { setCat(c); setName(c.name); setDesc(c.description) })
      .catch((e) => setErr(errorMessage(e)))
  }, [id])
  useEffect(load, [load])

  const add = async (row: LibraryRow) => {
    try {
      await post(`/api/categories/${id}/items`, { item_type: row.type, item_id: row.id })
      toast(`Added ${row.name}`)
      load()
    } catch (e) { toast(errorMessage(e)) }
  }
  const remove = async (itemId: number) => {
    try { await del(`/api/categories/${id}/items/${itemId}`); load() } catch (e) { toast(errorMessage(e)) }
  }
  const save = async () => {
    try { await patch(`/api/categories/${id}`, { name, description: desc }); setEditing(false); load() } catch (e) { toast(errorMessage(e)) }
  }
  const remove_ = async () => {
    if (!confirm('Delete this category? Its tags, creators and sources are not affected.')) return
    try { await del(`/api/categories/${id}`); navigate('/library?tab=categories') } catch (e) { toast(errorMessage(e)) }
  }

  const member = new Set(cat ? (['tag', 'creator', 'source'] as const).flatMap((k) => cat.items[k].map((r) => `${k}-${r.id}`)) : [])

  return (
    <div className="page">
      <PageHeader title={cat?.name || 'Category'}
        left={<button className="iconbtn plain" aria-label="Back" onClick={() => navigate('/library?tab=categories')}><ChevronLeft /></button>}
        right={cat ? <ScoreBadge score={cat.score} /> : null} />
      <div className="page-inner">
        {err ? <div className="error">{err}</div> : null}
        {!cat ? (err ? null : <Spinner />) : (
          <>
            {editing ? (
              <div className="card">
                <label className="field">Name<input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label>
                <label className="field">Description<textarea value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
                <div className="btnrow"><button className="btn primary" onClick={() => void save()}>Save</button><button className="btn" onClick={() => setEditing(false)}>Cancel</button></div>
              </div>
            ) : cat.description ? <p className="muted">{cat.description}</p> : null}
            <div className="btnrow">
              <button className="btn primary" onClick={() => navigate(`/feed/category/${cat.id}`)} disabled={!cat.item_count}>Play feed</button>
              <button className="btn" onClick={() => setPicking(true)}>Add items</button>
            </div>
            {(['tag', 'creator', 'source'] as const).map((k) => (
              <section key={k}>
                <div className="section-title">{k === 'tag' ? 'Tags' : k === 'creator' ? 'Creators' : 'Sources'}</div>
                {cat.items[k].length ? (
                  <div className="tagrow">
                    {cat.items[k].map((r) => (
                      <span key={r.id} className="chip solid">
                        <button style={{ background: 'none', border: 0, padding: 0 }} onClick={() => navigate(`/feed/${k}/${r.id}`)}>
                          {k === 'tag' ? '#' : ''}{r.name}
                        </button>
                        <ScoreBadge score={r.score} />
                        <button className="x" style={{ background: 'none', border: 0, padding: 0, display: 'inline-flex' }}
                          aria-label={`Remove ${r.name}`} onClick={() => void remove(r.category_item_id)}><Close size={14} /></button>
                      </span>
                    ))}
                  </div>
                ) : <p className="muted" style={{ fontSize: 13 }}>None</p>}
              </section>
            ))}
            {!cat.item_count ? <EmptyState>Add tags, creators or sources to build this feed.</EmptyState> : null}
            <div className="btnrow" style={{ marginTop: 24 }}>
              <button className="btn" onClick={() => setEditing(true)}>Rename</button>
              <button className="btn danger" onClick={() => void remove_()}>Delete category</button>
            </div>
          </>
        )}
      </div>
      <Sheet open={picking} onClose={() => setPicking(false)} label="Add items">
        <h3>Add to {cat?.name}</h3>
        <SearchPicker types={['tag', 'creator', 'source']} placeholder="Search tags, creators, sources"
          exclude={(r) => member.has(`${r.type}-${r.id}`)} onPick={(r) => void add(r)} />
      </Sheet>
    </div>
  )
}

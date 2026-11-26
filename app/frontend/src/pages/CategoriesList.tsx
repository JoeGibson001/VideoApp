import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { errorMessage, get, post } from '../api'
import { EmptyState, ScoreBadge, Spinner } from '../components/common'
import { useAuth, useToast } from '../state'
import type { CategorySummary } from '../types'

export function CategoriesList() {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [cats, setCats] = useState<CategorySummary[] | null>(null)
  const [name, setName] = useState('')

  useEffect(() => {
    if (!user) return
    get<{ items: CategorySummary[] }>('/api/categories').then((r) => setCats(r.items)).catch((e) => toast(errorMessage(e)))
  }, [user, toast])

  if (!user) return <EmptyState><p>Categories group tags, creators and sources into your own feeds.</p><Link className="btn primary" to="/login">Log in to use categories</Link></EmptyState>

  const create = async () => {
    if (!name.trim()) return
    try {
      const c = await post<CategorySummary>('/api/categories', { name: name.trim() })
      navigate(`/library/category/${c.id}`)
    } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <>
      <form className="btnrow" onSubmit={(e) => { e.preventDefault(); void create() }}>
        <input type="text" placeholder="New category name" aria-label="New category name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn primary" type="submit" style={{ flex: 'none' }}>Create</button>
      </form>
      {!cats ? <Spinner /> : null}
      {cats && !cats.length ? <EmptyState>No categories yet</EmptyState> : null}
      {cats?.map((c) => (
        <button key={c.id} className="colcard" onClick={() => navigate(`/library/category/${c.id}`)}>
          <span className="cover" aria-hidden="true">{'◈'}</span>
          <span className="t"><b>{c.name}</b><small>{c.item_count} item{c.item_count === 1 ? '' : 's'}{c.description ? ` · ${c.description}` : ''}</small></span>
          <ScoreBadge score={c.score} />
        </button>
      ))}
    </>
  )
}

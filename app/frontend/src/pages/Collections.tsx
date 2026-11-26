import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { errorMessage, get, post } from '../api'
import { EmptyState, PageHeader, Spinner } from '../components/common'
import { CollectionsIcon, Search } from '../components/Icons'
import { useAuth, useToast } from '../state'
import type { CollectionSummary } from '../types'

export default function Collections() {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [cols, setCols] = useState<CollectionSummary[] | null>(null)
  const [name, setName] = useState('')

  useEffect(() => {
    if (!user) return
    get<{ items: CollectionSummary[] }>('/api/collections').then((r) => setCols(r.items)).catch((e) => toast(errorMessage(e)))
  }, [user, toast])

  const create = async () => {
    if (!name.trim()) return
    try {
      const c = await post<CollectionSummary>('/api/collections', { name: name.trim() })
      setCols((cs) => [...(cs || []), c])
      setName('')
    } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <div className="page">
      <PageHeader title="Collections" right={<Link to="/search" className="iconbtn plain" aria-label="Search"><Search /></Link>} />
      <div className="page-inner">
        {!user ? (
          <EmptyState>
            <p>Favorites, your Liked videos and your own playlists live here.</p>
            <Link className="btn primary" to="/login">Log in to use collections</Link>
          </EmptyState>
        ) : (
          <>
            <form className="btnrow" onSubmit={(e) => { e.preventDefault(); void create() }}>
              <input type="text" placeholder="New collection name" aria-label="New collection name" value={name} onChange={(e) => setName(e.target.value)} />
              <button className="btn primary" type="submit" style={{ flex: 'none' }}>Create</button>
            </form>
            {!cols ? <Spinner /> : cols.map((c) => (
              <button key={c.id} className="colcard" onClick={() => navigate(`/collections/${c.id}`)} data-testid={`collection-${c.system_type || c.id}`}>
                {c.cover_url ? <img className="cover" src={c.cover_url} alt="" /> : <span className="cover"><CollectionsIcon /></span>}
                <span className="t">
                  <b>{c.name}</b>
                  {c.system_type === 'liked' ? <span className="tagbadge">Liked automatically</span> : null}
                  {c.system_type === 'favorites' ? <span className="tagbadge">Favorites</span> : null}
                  <small>{c.video_count} video{c.video_count === 1 ? '' : 's'}</small>
                </span>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  )
}

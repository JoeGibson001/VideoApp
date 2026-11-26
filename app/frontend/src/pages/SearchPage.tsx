import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { del, get, post } from '../api'
import { EmptyState, ItemRow, PageHeader, Spinner } from '../components/common'
import { ChevronLeft, Close, Search } from '../components/Icons'
import { useAuth, useDebounced } from '../state'
import type { LibraryRow, SearchResponse } from '../types'

type Group = 'tag' | 'creator' | 'source' | 'collection' | 'category'
const GROUPS: { key: Group; label: string }[] = [
  { key: 'tag', label: 'Tags' }, { key: 'creator', label: 'Creators' }, { key: 'source', label: 'Sources' },
  { key: 'collection', label: 'Collections' }, { key: 'category', label: 'Categories' },
]

export default function SearchPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 150)
  const [filter, setFilter] = useState<Group | 'all'>('all')
  const [res, setRes] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [recent, setRecent] = useState<string[]>([])
  const abort = useRef<AbortController>()
  const groups = GROUPS.filter((g) => user || ['tag', 'creator', 'source'].includes(g.key))

  useEffect(() => {
    if (user) get<{ items: string[] }>('/api/search/recent').then((r) => setRecent(r.items)).catch(() => {})
  }, [user])

  useEffect(() => {
    abort.current?.abort()
    if (!dq.trim()) { setRes(null); setLoading(false); return }
    const ctrl = new AbortController()
    abort.current = ctrl
    setLoading(true)
    const types = filter === 'all' ? groups.map((g) => g.key).join(',') : filter
    get<SearchResponse>('/api/search', { q: dq, types, limit: filter === 'all' ? 5 : 100 }, ctrl.signal)
      .then(setRes).catch(() => {}).finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    return () => ctrl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq, filter, user])

  const open = (row: LibraryRow) => {
    if (user && q.trim()) void post('/api/search/recent', { q: q.trim() }).catch(() => {})
    if (row.type === 'collection') navigate(`/collections/${row.id}`)
    else if (row.type === 'category') navigate(`/library/category/${row.id}`)
    else navigate(`/feed/${row.type}/${row.id}`)
  }

  const visibleGroups = filter === 'all' ? groups : groups.filter((g) => g.key === filter)
  const anyResults = res && visibleGroups.some((g) => res.groups[g.key]?.items.length)

  return (
    <div className="page">
      <PageHeader title="Search" left={<button className="iconbtn plain" aria-label="Back" onClick={() => navigate(-1)}><ChevronLeft /></button>} />
      <div className="page-inner">
        <div className="searchbox">
          <Search />
          <input type="search" autoFocus value={q} placeholder="Tags, creators, sources..." aria-label="Search"
            onChange={(e) => setQ(e.target.value)} data-testid="search-input" />
        </div>
        <div className="chiprow" role="tablist">
          <button className={'chip solid' + (filter === 'all' ? ' on' : '')} onClick={() => setFilter('all')}>All</button>
          {groups.map((g) => (
            <button key={g.key} className={'chip solid' + (filter === g.key ? ' on' : '')} onClick={() => setFilter(g.key)}>{g.label}</button>
          ))}
        </div>

        {!q.trim() && user && recent.length ? (
          <>
            <div className="section-title">Recent searches
              <button className="btn small" onClick={() => { void del('/api/search/recent'); setRecent([]) }}>Clear</button>
            </div>
            {recent.map((r) => (
              <button key={r} className="colcard" style={{ minHeight: 48 }} onClick={() => setQ(r)}>
                <span className="t">{r}</span><Close size={14} />
              </button>
            ))}
          </>
        ) : null}

        {loading && !res ? <Spinner /> : null}
        {res ? visibleGroups.map((g) => {
          const grp = res.groups[g.key]
          if (!grp || !grp.items.length) return null
          return (
            <section key={g.key} data-testid={`search-group-${g.key}`}>
              <div className="section-title">{g.label} ({grp.total})
                {filter === 'all' && grp.total > grp.items.length
                  ? <button className="btn small" onClick={() => setFilter(g.key)}>Show all</button> : null}
              </div>
              {grp.items.map((r) => <ItemRow key={`${r.type}-${r.id}`} row={r} showScore={!!user} onClick={() => open(r)} />)}
            </section>
          )
        }) : null}
        {res && !anyResults && !loading ? <EmptyState>No results for "{dq}"</EmptyState> : null}
      </div>
    </div>
  )
}

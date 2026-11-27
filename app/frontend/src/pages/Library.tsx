import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { errorMessage, get, post } from '../api'
import { EmptyState, ItemRow, PageHeader, Sheet, Spinner } from '../components/common'
import { More, Search } from '../components/Icons'
import { useAuth, useDebounced, useToast } from '../state'
import type { CategorySummary, ItemType, LibraryRow, Paged } from '../types'
import { LIBRARY_SORTS } from '../sorts'
import { CategoriesList } from './CategoriesList'

const TABS = [
  { key: 'tags', label: 'Tags', type: 'tag' as ItemType },
  { key: 'creators', label: 'Creators', type: 'creator' as ItemType },
  { key: 'sources', label: 'Sources', type: 'source' as ItemType },
  { key: 'categories', label: 'Moods', type: null },
]

const SORTS = LIBRARY_SORTS

export default function Library() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((t) => t.key === params.get('tab')) || TABS[0]
  return (
    <div className="page">
      <PageHeader title="Library" right={<Link to="/search" className="iconbtn plain" aria-label="Search"><Search /></Link>} />
      <div className="page-inner">
        <div className="segmented" role="tablist">
          {TABS.map((t) => (
            <button key={t.key} role="tab" aria-selected={t.key === tab.key} className={t.key === tab.key ? 'on' : ''}
              onClick={() => setParams({ tab: t.key }, { replace: true })}>{t.label}</button>
          ))}
        </div>
        {tab.type ? <ItemList key={tab.type} type={tab.type} /> : <CategoriesList />}
      </div>
    </div>
  )
}

function ItemList({ type }: { type: ItemType }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 150)
  const [sort, setSort] = useState(user ? 'score_desc' : 'name_asc')
  const [watchedOnly, setWatchedOnly] = useState(false)
  const [rows, setRows] = useState<LibraryRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [catFor, setCatFor] = useState<LibraryRow | null>(null)
  const abort = useRef<AbortController>()

  const load = useCallback(async (p: number) => {
    abort.current?.abort()
    const ctrl = new AbortController()
    abort.current = ctrl
    setLoading(true)
    try {
      const r = await get<Paged<LibraryRow>>(`/api/${type}s`, {
        sort, q: dq || undefined, page: p, page_size: 50, watched_only: watchedOnly || undefined,
      }, ctrl.signal)
      setRows((prev) => (p === 1 ? r.items : [...prev, ...r.items]))
      setTotal(r.total)
      setPage(p)
      setErr(null)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setErr(errorMessage(e))
    } finally {
      if (!ctrl.signal.aborted) setLoading(false)
    }
  }, [type, sort, dq, watchedOnly])

  useEffect(() => { void load(1) }, [load])

  const label = type === 'tag' ? 'tags' : type === 'creator' ? 'creators' : 'sources'
  return (
    <>
      <div className="searchbox">
        <Search />
        <input type="search" placeholder={`Search ${label}`} aria-label={`Search ${label}`} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="toolbar">
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort" disabled={!!dq}>
          {SORTS.filter(([k]) => user || !k.startsWith('score') && k !== 'recent').map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {type === 'creator' && user?.is_admin ? (
          <label className="check" style={{ flex: 'none' }}>
            <input type="checkbox" checked={watchedOnly} onChange={(e) => setWatchedOnly(e.target.checked)} /> Watched only
          </label>
        ) : null}
      </div>
      {err ? <div className="error">{err}</div> : null}
      {rows.map((r) => (
        <ItemRow key={r.id} row={r} showScore={!!user} onClick={() => navigate(`/feed/${type}/${r.id}`)}
          actions={user ? (
            <button className="iconbtn plain" aria-label={`More actions for ${r.name}`} onClick={() => setCatFor(r)}><More /></button>
          ) : null} />
      ))}
      {loading ? <Spinner /> : null}
      {!loading && !rows.length && !err ? <EmptyState>{dq ? 'No matches' : `No ${label} yet`}</EmptyState> : null}
      {!loading && rows.length < total ? (
        <div className="btnrow"><button className="btn" onClick={() => void load(page + 1)}>Load more ({total - rows.length})</button></div>
      ) : null}
      <AddToCategorySheet item={catFor} onClose={() => setCatFor(null)} />
    </>
  )
}

export function AddToCategorySheet({ item, onClose }: { item: LibraryRow | null; onClose: () => void }) {
  const toast = useToast()
  const [cats, setCats] = useState<CategorySummary[] | null>(null)
  const [name, setName] = useState('')
  useEffect(() => {
    if (!item) return
    setCats(null)
    get<{ items: CategorySummary[] }>('/api/categories').then((r) => setCats(r.items)).catch((e) => toast(errorMessage(e)))
  }, [item, toast])
  const add = async (catId: number, catName: string) => {
    if (!item) return
    try {
      await post(`/api/categories/${catId}/items`, { item_type: item.type, item_id: item.id })
      toast(`Added to ${catName}`)
      onClose()
    } catch (e) { toast(errorMessage(e)) }
  }
  const create = async () => {
    if (!name.trim()) return
    try {
      const c = await post<CategorySummary>('/api/categories', { name: name.trim() })
      setName('')
      await add(c.id, c.name)
    } catch (e) { toast(errorMessage(e)) }
  }
  return (
    <Sheet open={!!item} onClose={onClose} label="Add to mood">
      <h3>Add {item?.type === 'tag' ? '#' : ''}{item?.name} to a mood</h3>
      {!cats ? <Spinner /> : cats.map((c) => (
        <button key={c.id} className="colcard" onClick={() => void add(c.id, c.name)}>
          <span className="t">{c.name}<small>{c.item_count} items</small></span>
        </button>
      ))}
      {cats && !cats.length ? <p className="muted">No moods yet. Create one:</p> : null}
      <form className="btnrow" onSubmit={(e) => { e.preventDefault(); void create() }}>
        <input type="text" placeholder="New mood name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn primary" type="submit" style={{ flex: 'none' }}>Create</button>
      </form>
    </Sheet>
  )
}

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { get } from '../api'
import { useDebounced } from '../state'
import type { LibraryRow, SearchResponse } from '../types'
import { ItemRow, Spinner } from './common'
import { Search } from './Icons'

/**
 * Fuzzy picker backed by /api/search (DESIGN.md 6.4). Used by the category item picker,
 * tag editor and merge dialogs. Results update on every keystroke (debounced, in-flight cancelled).
 */
export function SearchPicker({ types, onPick, placeholder = 'Search', exclude, autoFocus = true, renderActions }: {
  types: ('tag' | 'creator' | 'source' | 'collection' | 'category')[]
  onPick: (row: LibraryRow) => void
  placeholder?: string
  exclude?: (row: LibraryRow) => boolean
  autoFocus?: boolean
  renderActions?: (row: LibraryRow) => ReactNode
}) {
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 150)
  const [rows, setRows] = useState<LibraryRow[]>([])
  const [loading, setLoading] = useState(false)
  const abort = useRef<AbortController>()

  useEffect(() => {
    abort.current?.abort()
    if (!dq.trim()) { setRows([]); return }
    const ctrl = new AbortController()
    abort.current = ctrl
    setLoading(true)
    get<SearchResponse>('/api/search', { q: dq, types: types.join(','), limit: 30 }, ctrl.signal)
      .then((r) => {
        const all: LibraryRow[] = []
        for (const t of types) all.push(...(r.groups[t]?.items || []))
        all.sort((a, b) => (a.match_rank ?? 9) - (b.match_rank ?? 9))
        setRows(exclude ? all.filter((x) => !exclude(x)) : all)
      })
      .catch(() => {})
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    return () => ctrl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq, types.join(',')])

  return (
    <div>
      <div className="searchbox">
        <Search />
        <input type="search" value={q} autoFocus={autoFocus} placeholder={placeholder} aria-label={placeholder}
          onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="picker-results">
        {loading && !rows.length ? <Spinner /> : null}
        {rows.map((r) => (
          <ItemRow key={`${r.type}-${r.id}`} row={r} onClick={() => onPick(r)} actions={renderActions?.(r)} />
        ))}
        {dq && !loading && !rows.length ? <div className="empty">No matches</div> : null}
      </div>
    </div>
  )
}

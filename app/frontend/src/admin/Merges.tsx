import { get, post } from '../api'
import { EmptyState, Spinner, formatDateTime } from '../components/common'
import { ErrorBox, useAction, useLoad } from './shared'

interface MergeEntry {
  id: number
  entity_type: 'tag' | 'creator'
  from_ids: number[]
  from_names: string[]
  into_id: number
  into_name: string | null
  merged_at: string
  undone_at: string | null
}

export default function Merges() {
  const list = useLoad(() => get<{ items: MergeEntry[] }>('/api/admin/merges'), [])
  const act = useAction()
  const items = list.data?.items || []
  const undo = async (m: MergeEntry) => {
    const what = `${(m.from_names || []).join(', ')} -> ${m.into_name ?? `#${m.into_id}`}`
    if (!window.confirm(`Undo ${m.entity_type} merge ${what}? Stats are recomputed.`)) return
    const r = await act.run(() => post(`/api/admin/merges/${m.id}/undo`), 'Merge undone')
    if (r !== undefined) void list.reload(true)
  }
  return (
    <div>
      <p className="muted" style={{ fontSize: 13 }}>
        Undo restores the merged {`"from"`} items and moves their videos back, including videos imported since. It is blocked
        if the surviving item has since been merged or deleted.
      </p>
      <ErrorBox error={list.error || act.error} />
      {list.loading && !list.data ? <Spinner /> : !items.length ? <EmptyState>No merges yet</EmptyState> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>When</th><th>Type</th><th>Merged</th><th>Into</th><th></th></tr></thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id}>
                  <td className="nowrap">{formatDateTime(m.merged_at)}</td>
                  <td>{m.entity_type}</td>
                  <td>{(m.from_names || []).join(', ') || (m.from_ids || []).map((i) => `#${i}`).join(', ')}</td>
                  <td>{m.into_name ?? <span className="muted">#{m.into_id} (gone)</span>}</td>
                  <td className="num nowrap">
                    {m.undone_at
                      ? <span className="status skipped" title={formatDateTime(m.undone_at)}>undone</span>
                      : <button className="btn small" disabled={act.busy} onClick={() => void undo(m)}>Undo</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

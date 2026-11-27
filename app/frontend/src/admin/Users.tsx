import { useEffect, useState } from 'react'
import { del, get, patch, post, put } from '../api'
import { Avatar, EmptyState, Sheet, Spinner } from '../components/common'
import { useAuth, useToast } from '../state'
import { ErrorBox, useAction, useLoad } from './shared'

interface SourceOption { id: number; name: string; domain: string; video_count: number; icon_url: string | null }
type SourceMode = 'all' | 'only' | 'except'
interface Access { source_mode: SourceMode; source_ids: number[] }
interface AdminUser extends Access {
  id: number; username: string; is_admin: boolean; is_active: boolean; created_at: string | null; ratings: number; is_me: boolean
}

function accessLabel(a: Access, total: number): string {
  const n = a.source_ids.length
  if (a.source_mode === 'all') return 'All sources (including new ones)'
  if (a.source_mode === 'except') return n ? `All sources except ${n} (new ones included)` : 'All sources (including new ones)'
  if (!n) return 'No sources'
  return `${n} of ${total} source${total === 1 ? '' : 's'}`
}

const MODE_TEXT: Record<SourceMode, { label: string; list?: string; hint?: string }> = {
  all: { label: 'All sources, including ones added later' },
  except: {
    label: 'All sources, including ones added later, except the ones ticked below',
    list: 'Hide these sources', hint: 'New sources are shown automatically.',
  },
  only: {
    label: 'Only the sources ticked below',
    list: 'Show these sources', hint: 'New sources stay hidden until they are ticked here.',
  },
}

/** All sources / all except a list / only a list. */
function SourceAccess({ value, onChange, sources, name }: {
  value: Access; onChange: (a: Access) => void; sources: SourceOption[]; name: string
}) {
  const toggle = (id: number, on: boolean) => onChange({
    ...value, source_ids: on ? [...value.source_ids, id] : value.source_ids.filter((x) => x !== id),
  })
  // The list means "show" for one mode and "hide" for the other, so switching between them starts afresh.
  const setMode = (m: SourceMode) => onChange({ source_mode: m, source_ids: m === value.source_mode ? value.source_ids : [] })
  const text = MODE_TEXT[value.source_mode]
  return (
    <div>
      {(['all', 'except', 'only'] as SourceMode[]).map((m) => (
        <label key={m} className="check">
          <input type="radio" name={`${name}-mode`} checked={value.source_mode === m} onChange={() => setMode(m)} />
          {MODE_TEXT[m].label}
        </label>
      ))}
      {value.source_mode !== 'all' ? (
        <div style={{ paddingLeft: 30 }}>
          <div className="muted" style={{ fontSize: 12, fontWeight: 600 }}>{text.list}</div>
          {!sources.length ? <div className="muted" style={{ fontSize: 13 }}>No sources yet</div> : sources.map((s) => (
            <label key={s.id} className="check">
              <input type="checkbox" checked={value.source_ids.includes(s.id)} onChange={(e) => toggle(s.id, e.target.checked)}
                aria-label={`${value.source_mode === 'except' ? 'Hide' : 'Show'} ${s.name}`} />
              <Avatar name={s.name} url={s.icon_url} size={22} square />
              <span style={value.source_mode === 'except' && value.source_ids.includes(s.id) ? { textDecoration: 'line-through', opacity: 0.6 } : undefined}>
                {s.name} <span className="muted" style={{ fontSize: 12 }}>{s.domain} - {s.video_count} video{s.video_count === 1 ? '' : 's'}</span>
              </span>
            </label>
          ))}
          {sources.length ? (
            <div className="btnrow" style={{ marginTop: 4 }}>
              <button className="btn small" type="button" style={{ flex: 'none' }} onClick={() => onChange({ ...value, source_ids: sources.map((s) => s.id) })}>Tick all</button>
              <button className="btn small" type="button" style={{ flex: 'none' }} onClick={() => onChange({ ...value, source_ids: [] })}>Untick all</button>
            </div>
          ) : null}
          <div className="muted" style={{ fontSize: 12 }}>{text.hint}</div>
        </div>
      ) : null}
    </div>
  )
}

export default function Users() {
  const users = useLoad(() => get<{ items: AdminUser[] }>('/api/admin/users'), [])
  const sourcesRes = useLoad(() => get<{ items: SourceOption[] }>('/api/admin/sources'), [])
  const sources = sourcesRes.data?.items || []
  const [editing, setEditing] = useState<number | null>(null)
  const list = users.data?.items || []
  const current = list.find((u) => u.id === editing)

  return (
    <div>
      <VisitorsCard sources={sources} />
      <AddUserCard sources={sources} onAdded={() => void users.reload(true)} />
      <div className="section-title">Users</div>
      <ErrorBox error={users.error} />
      {users.loading && !users.data ? <Spinner /> : !list.length ? <EmptyState>No users</EmptyState> : list.map((u) => (
        <button key={u.id} className="colcard" onClick={() => setEditing(u.id)}>
          <Avatar name={u.username} size={40} />
          <span className="t">
            <b>{u.username}</b>
            {u.is_admin ? <span className="tagbadge">admin</span> : null}
            {u.is_me ? <span className="tagbadge">you</span> : null}
            {!u.is_active ? <span className="tagbadge" style={{ color: '#fca5a5' }}>disabled</span> : null}
            <small>{accessLabel(u, sources.length)} - {u.ratings} rating{u.ratings === 1 ? '' : 's'}</small>
          </span>
        </button>
      ))}
      <Sheet open={!!current} onClose={() => setEditing(null)} label="Edit user">
        {current ? (
          <EditUser key={current.id} user={current} sources={sources}
            onSaved={() => void users.reload(true)} onDeleted={() => { setEditing(null); void users.reload(true) }} />
        ) : null}
      </Sheet>
    </div>
  )
}

function VisitorsCard({ sources }: { sources: SourceOption[] }) {
  const setting = useLoad(() => get<Access>('/api/admin/visibility/anonymous'), [])
  const [value, setValue] = useState<Access | null>(null)
  const act = useAction()
  useEffect(() => { if (setting.data) setValue(setting.data) }, [setting.data])
  const key = (a: Access) => `${a.source_mode}:${[...a.source_ids].sort((x, y) => x - y).join(',')}`
  const dirty = !!value && !!setting.data && key(value) !== key(setting.data)
  const save = async () => {
    if (!value) return
    const r = await act.run(() => put<Access>('/api/admin/visibility/anonymous', value), 'Visitor access saved')
    if (r) setting.setData(r)
  }
  return (
    <div className="card">
      <div className="section-title" style={{ marginTop: 0 }}>Visitors who are not logged in</div>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 6px' }}>
        What people can see without an account. Choose "Only the sources ticked" with nothing ticked to require a login for everything.
      </p>
      <ErrorBox error={setting.error} />
      {!value ? <Spinner /> : <SourceAccess value={value} onChange={setValue} sources={sources} name="anon" />}
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy || !dirty} onClick={() => void save()}>Save visitor access</button>
      </div>
      <ErrorBox error={act.error} />
    </div>
  )
}

function AddUserCard({ sources, onAdded }: { sources: SourceOption[]; onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [access, setAccess] = useState<Access>({ source_mode: 'all', source_ids: [] })
  const act = useAction()
  const add = async () => {
    const r = await act.run(() => post<AdminUser>('/api/admin/users', { username, password, is_admin: isAdmin, ...access }),
      `Created ${isAdmin ? 'admin' : 'user'} ${username}`)
    if (r) {
      setUsername(''); setPassword(''); setIsAdmin(false); setAccess({ source_mode: 'all', source_ids: [] }); setOpen(false)
      onAdded()
    }
  }
  if (!open) {
    return <div className="btnrow"><button className="btn primary" onClick={() => setOpen(true)}>+ Add user</button></div>
  }
  return (
    <div className="card">
      <div className="section-title" style={{ marginTop: 0 }}>New user</div>
      <label className="field">Username
        <input type="text" value={username} autoComplete="off" autoCapitalize="none" onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="field">Password (at least 8 characters)
        <input type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
        Admin (can manage content, imports, watches and users)
      </label>
      <div className="section-title">Sources this user can see</div>
      <SourceAccess value={access} onChange={setAccess} sources={sources} name="new" />
      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn" onClick={() => setOpen(false)}>Cancel</button>
        <button className="btn primary" disabled={act.busy || !username.trim() || password.length < 8} onClick={() => void add()}>Create user</button>
      </div>
    </div>
  )
}

function EditUser({ user, sources, onSaved, onDeleted }: {
  user: AdminUser; sources: SourceOption[]; onSaved: () => void; onDeleted: () => void
}) {
  const { refresh } = useAuth()
  const toast = useToast()
  const [username, setUsername] = useState(user.username)
  const [isAdmin, setIsAdmin] = useState(user.is_admin)
  const [isActive, setIsActive] = useState(user.is_active)
  const [access, setAccess] = useState<Access>({ source_mode: user.source_mode, source_ids: user.source_ids })
  const [password, setPassword] = useState('')
  const act = useAction()

  const save = async () => {
    const body: Record<string, unknown> = { username, is_admin: isAdmin, is_active: isActive, ...access }
    if (password) body.password = password
    const r = await act.run(() => patch<AdminUser>(`/api/admin/users/${user.id}`, body), 'Saved')
    if (r) {
      setPassword('')
      onSaved()
      if (user.is_me) void refresh()
    }
  }
  const remove = async () => {
    if (!window.confirm(`Delete ${user.username}? Their ratings, collections and moods are deleted too. This cannot be undone.`)) return
    const r = await act.run(() => del(`/api/admin/users/${user.id}`))
    if (r !== undefined) { toast(`Deleted ${user.username}`); onDeleted() }
  }

  return (
    <div>
      <h3>{user.username}{user.is_me ? ' (you)' : ''}</h3>
      <label className="field">Username
        <input type="text" value={username} autoCapitalize="none" onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={isAdmin} disabled={user.is_me} onChange={(e) => setIsAdmin(e.target.checked)} />
        Admin{user.is_me ? ' (you cannot remove your own admin access)' : ''}
      </label>
      <label className="check">
        <input type="checkbox" checked={isActive} disabled={user.is_me} onChange={(e) => setIsActive(e.target.checked)} />
        Account enabled{!isActive ? ' (disabled accounts cannot log in and are logged out everywhere)' : ''}
      </label>
      <label className="field">{user.is_me ? 'New password' : 'Reset password'} (leave blank to keep)
        <input type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
        {!user.is_me && password ? <span style={{ fontSize: 12 }}>Saving logs {user.username} out on every device.</span> : null}
      </label>
      <div className="section-title">Sources {user.is_me ? 'you see' : 'this user can see'}</div>
      {user.is_me ? (
        <p className="muted" style={{ fontSize: 13, margin: '0 0 6px' }}>
          Hides sources from your own feed, library and search. Admin pages always show everything.
        </p>
      ) : null}
      <SourceAccess value={access} onChange={setAccess} sources={sources} name={`u${user.id}`} />
      <ErrorBox error={act.error} />
      <div className="btnrow">
        <button className="btn primary" disabled={act.busy || !username.trim() || (!!password && password.length < 8)} onClick={() => void save()}>Save</button>
      </div>
      {!user.is_me ? (
        <div className="card danger-zone">
          <button className="btn danger" disabled={act.busy} onClick={() => void remove()}>Delete user</button>
        </div>
      ) : null}
    </div>
  )
}

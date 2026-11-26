import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { errorMessage, post } from '../api'
import { PageHeader } from '../components/common'
import { useAuth, usePlayback, useToast } from '../state'

export default function Account() {
  const { user, logout, updateSettings } = useAuth()
  const { setMuted } = usePlayback()
  const toast = useToast()
  const navigate = useNavigate()

  const set = async (key: string, value: boolean) => {
    try { await updateSettings({ [key]: value }) } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <div className="page">
      <PageHeader title="Account" />
      <div className="page-inner">
        {!user ? (
          <div className="card">
            <p>You are browsing anonymously. Likes are kept only for this session.</p>
            <Link className="btn primary" to="/login" style={{ width: '100%' }}>Log in</Link>
          </div>
        ) : (
          <>
            <div className="card">
              <div className="muted" style={{ fontSize: 13 }}>Signed in as</div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{user.username}{user.is_admin ? <span className="tagbadge">admin</span> : null}</div>
            </div>
            <div className="section-title">Playback</div>
            <div className="card">
              <label className="check">
                <input type="checkbox" checked={user.settings.mute_default !== false}
                  onChange={(e) => { void set('mute_default', e.target.checked); setMuted(e.target.checked) }} />
                Start videos muted
              </label>
              <label className="check">
                <input type="checkbox" checked={user.settings.autoplay !== false} onChange={(e) => void set('autoplay', e.target.checked)} />
                Autoplay videos
              </label>
              <p className="muted" style={{ fontSize: 12, margin: '4px 0 0' }}>Browsers may still require a tap before playing with sound.</p>
            </div>
            <div className="section-title">Password</div>
            <ChangePassword />
            {user.is_admin ? (
              <>
                <div className="section-title">Admin</div>
                <Link className="btn" to="/admin" style={{ width: '100%' }}>Open admin</Link>
              </>
            ) : null}
            <div className="btnrow" style={{ marginTop: 24 }}>
              <button className="btn danger" onClick={() => void logout().then(() => navigate('/'))}>Log out</button>
            </div>
          </>
        )}
        <p className="muted" style={{ fontSize: 12, textAlign: 'center', marginTop: 32 }}>
          Install: use your browser's "Install app" or "Add to Home Screen".
        </p>
      </div>
    </div>
  )
}

function ChangePassword() {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mismatch = !!confirm && next !== confirm
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await post('/api/auth/password', { current_password: current, new_password: next })
      toast('Password changed. Other devices were logged out.')
      setOpen(false); setCurrent(''); setNext(''); setConfirm('')
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  if (!open) return <div className="card"><button className="btn" style={{ width: '100%' }} onClick={() => setOpen(true)}>Change password</button></div>
  return (
    <form className="card" onSubmit={(e) => { e.preventDefault(); void save() }}>
      <label className="field">Current password
        <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </label>
      <label className="field">New password (at least 8 characters)
        <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </label>
      <label className="field">Repeat new password
        <input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </label>
      {mismatch ? <div className="error">The new passwords don't match</div> : null}
      {error ? <div className="error" role="alert">{error}</div> : null}
      <div className="btnrow">
        <button className="btn" type="button" onClick={() => setOpen(false)}>Cancel</button>
        <button className="btn primary" type="submit" disabled={busy || !current || next.length < 8 || next !== confirm}>Change password</button>
      </div>
    </form>
  )
}

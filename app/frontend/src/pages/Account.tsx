import { Link, useNavigate } from 'react-router-dom'
import { errorMessage } from '../api'
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

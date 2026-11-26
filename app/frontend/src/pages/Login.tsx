import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { errorMessage } from '../api'
import { useAuth } from '../state'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await login(username, password)
      const next = params.get('next')
      navigate(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replace: true })
    } catch (ex) {
      setErr(errorMessage(ex))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page" style={{ paddingBottom: 0 }}>
      <form className="login" onSubmit={submit}>
        <img src="/icons/icon-192.png" alt="" width={72} height={72} style={{ display: 'block', margin: '0 auto 12px', borderRadius: 18 }} />
        <h1>Tastebud</h1>
        {err ? <div className="error" role="alert">{err}</div> : null}
        <label className="field">Username
          <input type="text" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="field">Password
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <button className="btn primary" type="submit" disabled={busy} style={{ width: '100%', marginTop: 8 }}>{busy ? 'Logging in...' : 'Log in'}</button>
        <p style={{ textAlign: 'center', marginTop: 20 }}><Link to="/">Continue without logging in</Link></p>
      </form>
    </div>
  )
}

import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { getErrorMessage } from '../api/client'
import mark from '../assets/figma/consent-mark.png'
import { AuthHero } from '../components/AuthHero'

// R2-12: no password lives here, in source, or in the bundle - a leaked build
// artifact or a shoulder-surfed screen must not hand out working credentials.
// This dev-only convenience just fills in the *username* for the two seeded
// accounts (see backend/seed.py); the person still has to type the password.
// Gating on import.meta.env.DEV means `vite build` (which sets DEV to the
// literal `false`) dead-code-eliminates this whole block, so it never ships
// to a production bundle - verified by grepping `dist/` for the old literal.
const DEMO_USERS = [
  { value: 'admin', role: 'Admin' },
  { value: 'viewer', role: 'Viewer' },
]

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/dashboard')
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      <AuthHero />
      <div className="auth-panel">
        <div className="auth-card auth-card-form">
          <img src={mark} alt="" className="auth-card-mark" />
          <h1 className="auth-card-title">Consent Management Admin</h1>
          <div className="auth-card-sub">Admin or Consent Manager</div>
          <form onSubmit={submit}>
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group"><label htmlFor="login-username">Username</label><input id="login-username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></div>
            <div className="form-group"><label htmlFor="login-password">Password</label><input id="login-password" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            <button className="btn btn-primary auth-card-btn" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</button>
          </form>
          {import.meta.env.DEV && (
            <div className="auth-demo">
              <div className="auth-demo-label">Dev only — fills the username, not the password (see backend/seed.py)</div>
              <div className="auth-demo-row">
                {DEMO_USERS.map((u) => <button key={u.value} type="button" className="btn btn-sm" onClick={() => { setUsername(u.value); setPassword('') }}>{u.value}</button>)}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

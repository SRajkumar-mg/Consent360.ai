import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { getErrorMessage } from '../api/client'
import { Consent360Logo } from '../components/Logo'

const DEMO_USERS = [
  { label: 'admin', value: 'admin', pw: 'Admin@1234', role: 'Admin' },
  { label: 'viewer', value: 'viewer', pw: 'Viewer@1234', role: 'Viewer' },
]

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('Admin@1234')
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
    <div className="login-page">
      <div className="login-left">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 32 }}>
          <div className="logo-badge" style={{ width: 44, height: 44, fontSize: 20 }}><Consent360Logo size={20} /></div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>Consent Management Platform</div>
            <div style={{ fontSize: 12.5, color: '#9fb0d4' }}>Enterprise Consent &amp; Privacy Management</div>
          </div>
        </div>
        <h1 style={{ fontSize: 28, lineHeight: 1.3, maxWidth: 480 }}>
          Centralize consent. <span style={{ color: '#8aa2ff' }}>Prove compliance.</span>
        </h1>
        <p style={{ marginTop: 16, color: '#b9c4da', maxWidth: 460, fontSize: 14, lineHeight: 1.6 }}>
          Manage the full consent lifecycle for every customer — grant, deny, withdraw, renew,
          evaluate processing decisions and maintain a tamper-evident audit trail.
          Any business application can integrate through a secure REST API.
        </p>
        <div style={{ display: 'flex', gap: 24, marginTop: 36, fontSize: 12.5, color: '#9fb0d4' }}>
          <span>✓ Consent Lifecycle</span>
          <span>✓ Decision Engine</span>
          <span>✓ Immutable Audit</span>
          <span>✓ Consent Evidence</span>
        </div>
      </div>
      <div className="login-right">
        <div className="login-card">
          <div className="brand">
            <div className="logo-badge"><Consent360Logo size={20} /></div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Consent360</div>
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Sign in to continue</div>
            </div>
          </div>
          <div className="card" style={{ padding: 26 }}>
            <form onSubmit={submit}>
              {error && <div className="alert alert-error">{error}</div>}
              <div className="form-group">
                <label>Username</label>
                <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
              </div>
              <div className="form-group">
                <label>Password</label>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <button className="btn btn-primary btn-lg" style={{ width: '100%' }} disabled={loading}>
                {loading ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
            <div className="divider" />
            <div className="text-xs text-muted">Demo accounts</div>
            <div className="flex" style={{ flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {DEMO_USERS.map((u) => (
                <button
                  key={u.value}
                  className="btn btn-sm"
                  style={{ fontFamily: 'monospace', fontSize: 11 }}
                  title={u.role}
                  onClick={() => { setUsername(u.value); setPassword(u.pw) }}
                >
                  {u.value}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

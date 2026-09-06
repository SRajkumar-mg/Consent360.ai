import { useState, type ChangeEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { getErrorMessage } from '../api/client'
import { Consent360Logo } from '../components/Logo'

const DEMO_USERS = [
  { label: 'platform.superadmin.cms', value: 'platform.superadmin.cms', pw: 'PlatformSuper@1234', role: 'Platform Super Admin' },
  { label: 'viewer', value: 'viewer', pw: 'Viewer@1234', role: 'Viewer' },
]

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('platform.superadmin.cms')
  const [password, setPassword] = useState('PlatformSuper@1234')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [mfaRequired, setMfaRequired] = useState(false)
  const [otpCode, setOtpCode] = useState('')
  const [mfaLoading, setMfaLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/dashboard')
    } catch (err) {
      const message = getErrorMessage(err)
      if (message.toLowerCase().includes('mfa required')) {
        setMfaRequired(true)
      } else {
        setError(message)
      }
    } finally {
      setLoading(false)
    }
  }

  const submitOtp = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setMfaLoading(true)
    try {
      await login(username, password, otpCode)
      navigate('/dashboard')
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setMfaLoading(false)
    }
  }

  const handleOtpChange = (e: ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value.replace(/\D/g, '').slice(0, 6)
    setOtpCode(val)
  }

  return (
    <div className="login-page">
      <div className="login-left">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 32 }}>
          <div className="logo-badge" style={{ width: 44, height: 44, fontSize: 20 }}><Consent360Logo size={20} /></div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>Consent Management Platform</div>
            <div style={{ fontSize: 12.5, color: '#a5b4fc' }}>Enterprise Consent &amp; Privacy Management</div>
          </div>
        </div>
        <h1 style={{ fontSize: 28, lineHeight: 1.3, maxWidth: 480 }}>
          Centralize consent. <span style={{ color: '#a5b4fc' }}>Prove compliance.</span>
        </h1>
        <p style={{ marginTop: 16, color: '#b9c4da', maxWidth: 460, fontSize: 14, lineHeight: 1.6 }}>
          Manage the full consent lifecycle for every customer — grant, deny, withdraw, renew,
          evaluate processing decisions and maintain a tamper-evident audit trail.
          Any business application can integrate through a secure REST API.
        </p>
        <div style={{ display: 'flex', gap: 24, marginTop: 36, fontSize: 12.5, color: '#a5b4fc' }}>
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
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {mfaRequired ? 'Enter your MFA code' : 'Sign in to continue'}
              </div>
            </div>
          </div>
          <div className="card" style={{ padding: 26 }}>
            {!mfaRequired ? (
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
            ) : (
              <form onSubmit={submitOtp}>
                {error && <div className="alert alert-error">{error}</div>}
                <div style={{ textAlign: 'center', marginBottom: 20 }}>
                  <div style={{ fontSize: 14, color: 'var(--text-muted)' }}>
                    Multi-factor authentication is enabled. Enter the 6-digit code from your authenticator app.
                  </div>
                </div>
                <div className="form-group" style={{ textAlign: 'center' }}>
                  <label>Verification Code</label>
                  <input
                    className="input"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    maxLength={6}
                    value={otpCode}
                    onChange={handleOtpChange}
                    placeholder="000000"
                    autoFocus
                    style={{
                      textAlign: 'center',
                      fontSize: 24,
                      letterSpacing: 8,
                      fontFamily: 'monospace',
                      width: '100%',
                    }}
                  />
                </div>
                <button
                  className="btn btn-primary btn-lg"
                  style={{ width: '100%' }}
                  disabled={mfaLoading || otpCode.length !== 6}
                >
                  {mfaLoading ? 'Verifying…' : 'Verify'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  style={{ width: '100%', marginTop: 8 }}
                  onClick={() => { setMfaRequired(false); setOtpCode(''); setError('') }}
                >
                  Back to login
                </button>
              </form>
            )}
            {!mfaRequired && (
              <>
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
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

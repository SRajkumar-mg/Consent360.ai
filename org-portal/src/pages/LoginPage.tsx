import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { orgApi, getErrorMessage } from '../api'

export default function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await orgApi.login({ username, password })
      const data = res.data ?? {}
      localStorage.setItem('org_access_token', data.access_token ?? data.token ?? '')
      localStorage.setItem(
        'org_user',
        JSON.stringify(data.user ?? data.account ?? { username })
      )
      localStorage.setItem(
        'org_organization',
        JSON.stringify(data.organization ?? data.org ?? {})
      )
      navigate('/dashboard')
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2.5l7.5 3v5.2c0 4.8-3.2 9-7.5 10.8C7.7 19.7 4.5 15.5 4.5 10.7V5.5l7.5-3z" />
            <rect x="9.25" y="11" width="5.5" height="4.5" rx="1" />
            <path d="M10.5 11V9.75a1.5 1.5 0 013 0V11" />
          </svg>
        </div>
        <h1>Organization Portal</h1>
        <p className="login-subtitle">Sign in to view your organization&apos;s consent data</p>

        {error && <div className="alert-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <label className="field">
            <span>Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter your username"
              autoComplete="username"
              required
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
            />
          </label>
          <button type="submit" className="btn btn-primary btn-block" disabled={loading}>
            {loading ? 'Signing In…' : 'Sign In'}
          </button>
        </form>

        <p className="login-footer">Consent360 · Organization access is read-only</p>
      </div>
    </div>
  )
}

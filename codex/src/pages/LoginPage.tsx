import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { crmApi, getErrorMessage } from '../api'

const AGE_VERIFIED_KEY = 'codex_age_verified'

export function LoginPage() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [tosAccepted, setTosAccepted] = useState(false)
  const [ageAcknowledged, setAgeAcknowledged] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!tosAccepted) {
      setError('You must accept the Terms of Service to continue')
      return
    }
    if (!name.trim()) { setError('Please enter your name'); return }
    if (!email.trim() || !/^\S+@\S+\.\S+$/.test(email.trim())) { setError('Please enter a valid email address'); return }
    const digits = phone.replace(/\D/g, '')
    if (phone.trim() && digits.length !== 10) { setError('Please enter a valid 10-digit mobile number'); return }
    setLoading(true)
    try {
      localStorage.setItem(AGE_VERIFIED_KEY, String(ageAcknowledged))
      const res = await crmApi.login({
        name: name.trim(),
        email: email.trim(),
        phone: digits || undefined,
        source_app: 'CODEX',
      })
      navigate('/platform', {
        state: {
          showConsentModal: true,
          customerName: res.data.customer.name,
          customerId: res.data.customer.id,
          created: res.data.created,
        },
      })
    } catch (err) {
      setError(getErrorMessage(err))
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="blob blob-1" />
      <div className="blob blob-2" />
      <div className="blob blob-3" />

      <div className="login-card">
        <div className="login-logo">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
        </div>
        <h1>Welcome to Codex</h1>
        <p className="subtitle">Sign in to start coding and collaborate with developers</p>
        <form onSubmit={submit} className="form-stack">
          {error && <div className="alert alert-error">{error}</div>}
          <div className="form-group">
            <input className="input" placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="form-group">
            <input className="input" type="email" placeholder="Email address" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="form-group">
            <input className="input" type="tel" inputMode="numeric" placeholder="Mobile number (optional)" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
          <label className="age-verify-row">
            <input
              type="checkbox"
              checked={tosAccepted}
              onChange={(e) => {
                setTosAccepted(e.target.checked)
                if (e.target.checked) setError('')
              }}
              className="age-verify-checkbox"
            />
            <span className="age-verify-text">
              I agree to the <strong>Terms of Service</strong> and <strong>Privacy Policy</strong>
            </span>
          </label>
          <label className="age-verify-row">
            <input
              type="checkbox"
              checked={ageAcknowledged}
              onChange={(e) => setAgeAcknowledged(e.target.checked)}
              className="age-verify-checkbox"
            />
            <span className="age-verify-text">
              I confirm I am <strong>18 years or older</strong>
            </span>
          </label>
          <button className="btn btn-primary btn-lg" disabled={loading || !tosAccepted}>
            {loading ? 'Signing in…' : 'Start Coding'}
          </button>
        </form>
      </div>
    </div>
  )
}

import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { crmApi, getErrorMessage } from '../api'

const AGE_VERIFIED_KEY = 'jobhub_age_verified'

export function LoginPage() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [ageVerified, setAgeVerified] = useState(true)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!ageVerified) {
      setError('You must confirm you are 18 years or older to continue')
      return
    }
    if (!name.trim()) { setError('Please enter your name'); return }
    if (!email.trim() || !/^\S+@\S+\.\S+$/.test(email.trim())) { setError('Please enter a valid email address'); return }
    const digits = phone.replace(/\D/g, '')
    if (phone.trim() && digits.length !== 10) { setError('Please enter a valid 10-digit mobile number'); return }
    setLoading(true)
    try {
      localStorage.setItem(AGE_VERIFIED_KEY, 'true')
      const res = await crmApi.login({
        name: name.trim(),
        email: email.trim(),
        phone: digits || undefined,
      })
      navigate('/users', {
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
            <path d="M12 2l8 4v6c0 5.25-3.4 9.25-8 10-4.6-.75-8-4.75-8-10V6l8-4z" />
            <path d="M9 12l2 2 4-4" />
          </svg>
        </div>
        <h1>Welcome to Consent360</h1>
        <p className="subtitle">Sign in to manage your account and privacy preferences</p>
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
              checked={ageVerified}
              onChange={(e) => {
                setAgeVerified(e.target.checked)
                if (e.target.checked) setError('')
              }}
              className="age-verify-checkbox"
            />
            <span className="age-verify-text">
              I confirm I am <strong>18 years or older</strong> and agree to the Privacy Policy and Terms of Service
            </span>
          </label>
          <button className="btn btn-primary btn-lg" disabled={loading || !ageVerified}>
            {loading ? 'Signing in…' : 'Get Started'}
          </button>
        </form>
      </div>
    </div>
  )
}

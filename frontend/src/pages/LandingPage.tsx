import { Link } from 'react-router-dom'
import mark from '../assets/figma/consent-mark.png'
import { AuthHero } from '../components/AuthHero'

export function LandingPage() {
  return (
    <div className="auth-page">
      <AuthHero />
      <div className="auth-panel">
        <div className="auth-card">
          <img src={mark} alt="" className="auth-card-mark" />
          <h1 className="auth-card-title">Consent Management Admin</h1>
          <div className="auth-card-sub">Staff sign in</div>
          <p className="auth-card-desc">Sign in as an Admin or Consent Manager to operate the platform, manage purposes, rules and audit trails.</p>
          <Link to="/login" className="btn btn-primary auth-card-btn">Admin Sign in</Link>
        </div>
      </div>
    </div>
  )
}

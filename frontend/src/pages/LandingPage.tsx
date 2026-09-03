import { Link } from 'react-router-dom'
import { Consent360Logo } from '../components/Logo'

export function LandingPage() {
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
        </p>
        <div style={{ display: 'flex', gap: 24, marginTop: 36, fontSize: 12.5, color: '#a5b4fc' }}>
          <span>✓ Consent Lifecycle</span>
          <span>✓ Decision Engine</span>
          <span>✓ Immutable Audit</span>
        </div>
      </div>
      <div className="login-right">
        <div className="landing-entry login-card" style={{ maxWidth: 460 }}>
          <div className="card" style={{ padding: 26 }}>
            <div className="brand">
              <div className="logo-badge"><Consent360Logo size={20} /></div>
              <div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>Consent Management Admin</div>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Staff sign in</div>
              </div>
            </div>
            <p className="text-sm text-secondary mb" style={{ lineHeight: 1.6 }}>
              Sign in as an <b>Admin</b> or <b>Consent Manager</b> to operate the platform, manage
              purposes, rules and audit trails.
            </p>
            <Link to="/login" className="btn btn-primary btn-lg" style={{ width: '100%', textDecoration: 'none', color: '#fff' }}>
              Admin Sign in
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}

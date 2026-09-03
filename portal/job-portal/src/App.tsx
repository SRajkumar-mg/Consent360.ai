import { useState } from 'react'
import { ConsentBanner } from './ConsentBanner'
import { useConsent } from './useConsent'
import { JOBS } from './data'

export function App() {
  const consent = useConsent()
  const [search, setSearch] = useState('')
  const [showLogin, setShowLogin] = useState(false)

  const filtered = JOBS.filter(
    (j) =>
      j.title.toLowerCase().includes(search.toLowerCase()) ||
      j.company.toLowerCase().includes(search.toLowerCase()) ||
      j.location.toLowerCase().includes(search.toLowerCase()) ||
      j.tags.some((t) => t.toLowerCase().includes(search.toLowerCase())),
  )

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header className="site-header">
        <div className="site-logo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2z"/>
            <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>
          </svg>
          CareerHub
        </div>
        <nav className="site-nav">
          <a href="#jobs">Browse Jobs</a>
          <a href="#about">About</a>
          <button
            onClick={() => setShowLogin(true)}
            style={{ background: 'none', border: 'none', color: 'var(--primary)', fontWeight: 600, fontSize: 14, cursor: 'pointer' }}
          >
            Sign In
          </button>
        </nav>
      </header>

      <section className="hero">
        <h1>Find Your Next Opportunity</h1>
        <p>Discover 500+ curated positions from top companies across India</p>
        <div className="hero-search">
          <input
            placeholder="Job title, company, or skill..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button>Search</button>
        </div>
      </section>

      <div className="stats-bar">
        <div className="stat-card"><div className="number">500+</div><div className="label">Open Positions</div></div>
        <div className="stat-card"><div className="number">200+</div><div className="label">Companies</div></div>
        <div className="stat-card"><div className="number">50k+</div><div className="label">Candidates</div></div>
        <div className="stat-card"><div className="number">95%</div><div className="label">Placement Rate</div></div>
      </div>

      <section className="section" id="jobs">
        <h2 className="section-title">Featured Jobs</h2>
        <div className="jobs-grid">
          {filtered.map((job) => (
            <div key={job.id} className="job-card">
              <div className="job-card-header">
                <div>
                  <h3>{job.title}</h3>
                  <div className="company">{job.company}</div>
                </div>
                <span className="tag tag-blue">{job.type}</span>
              </div>
              <div className="meta">
                <span className="tag tag-gray">{job.location}</span>
                {job.tags.map((t) => (
                  <span key={t} className="tag tag-green">{t}</span>
                ))}
              </div>
              <div className="salary">{job.salary}</div>
              <div className="posted">Posted {job.posted}</div>
            </div>
          ))}
        </div>
        {filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
            No jobs match your search. Try different keywords.
          </div>
        )}
      </section>

      <footer className="site-footer">
        <p>&copy; 2026 CareerHub. All rights reserved. | Powered by Consent360 for privacy-first consent management.</p>
      </footer>

      {/* Consent FAB */}
      <button
        className="consent-fab"
        onClick={() => {
          if (!consent.contextToken) {
            consent.initConsent()
          } else {
            consent.setShowBanner(true)
          }
        }}
        title="Manage your consent preferences"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          <path d="M9 12l2 2 4-4"/>
        </svg>
      </button>

      {/* Consent Banner */}
      {consent.showBanner && consent.contextToken && (
        <ConsentBanner
          purposes={consent.purposes}
          onGrant={consent.grantPurpose}
          onWithdraw={consent.withdrawPurpose}
          onAcceptAll={consent.acceptAll}
          onRejectAll={consent.rejectAll}
          onSave={consent.savePreferences}
          onClose={() => consent.setShowBanner(false)}
        />
      )}

      {/* First-visit consent init */}
      {consent.loading && (
        <div className="consent-overlay">
          <div style={{ color: '#fff', textAlign: 'center' }}>
            <div style={{ fontSize: 18, fontWeight: 600 }}>Setting up your consent preferences...</div>
            <div style={{ marginTop: 8, opacity: 0.7 }}>Connecting to Consent360</div>
          </div>
        </div>
      )}

      {/* Login Modal */}
      {showLogin && (
        <div className="modal-overlay" onClick={() => setShowLogin(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Sign In to CareerHub</h2>
            <p className="subtitle">Access your applications and saved jobs</p>
            <div className="form-group">
              <label>Email</label>
              <input type="email" placeholder="you@example.com" />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input type="password" placeholder="Enter your password" />
            </div>
            <div className="modal-footer">
              <button className="btn btn-ghost" onClick={() => setShowLogin(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={() => setShowLogin(false)}>Sign In</button>
            </div>
          </div>
        </div>
      )}

      {consent.contextToken && !consent.consented && (
        <div className="toast">Your consent preferences have not been saved yet. Click the shield icon to review.</div>
      )}
    </div>
  )
}

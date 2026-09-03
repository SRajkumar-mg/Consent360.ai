import { Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { getConsentState, readGpcSignal } from '../consentGate'
import type { ConsentState } from '../consentGate'

export function CookiePolicy() {
  const [state, setState] = useState<ConsentState | null>(getConsentState())
  const gpc = readGpcSignal()

  useEffect(() => {
    const onC = () => setState(getConsentState())
    window.addEventListener('consent360:change', onC)
    return () => window.removeEventListener('consent360:change', onC)
  }, [])

  const row = (label: string, on: boolean) => (
    <tr>
      <td>{label}</td>
      <td>
        <span style={{ color: on ? 'var(--success, #16a34a)' : 'var(--danger, #dc2626)', fontWeight: 600 }}>
          {on ? 'On' : 'Off'}
        </span>
      </td>
    </tr>
  )

  return (
    <div style={{ minHeight: '100vh', background: '#f7f8fb', fontFamily: 'inherit', color: '#1e293b' }}>
      <div style={{ maxWidth: 860, margin: '0 auto', padding: '40px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <h1 style={{ fontSize: 24, margin: 0 }}>Cookie Policy</h1>
          <Link to="/" className="btn btn-ghost">← Back</Link>
        </div>

        <div className="card" style={{ padding: 24, marginBottom: 16 }}>
          <h2 style={{ fontSize: 18 }}>How we use cookies</h2>
          <p style={{ fontSize: 14, lineHeight: 1.7, marginTop: 8 }}>
            This portal uses cookies to stay secure, remember your preferences, and understand how it is used.
            We will never set non-essential cookies without your consent. Your stored preferences are enforced
            in the browser: a category you have switched off will not load the corresponding scripts or tags.
          </p>
        </div>

        <div className="card" style={{ padding: 24, marginBottom: 16 }}>
          <h2 style={{ fontSize: 18 }}>Your current preferences</h2>
          <table className="table" style={{ width: '100%', marginTop: 12 }}>
            <thead>
              <tr>
                <th>Category</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {row('Necessary (always on)', true)}
              {row('Functional', state?.categories.functional !== false)}
              {row('Analytics / Performance', state?.categories.analytics === true)}
              {row('Advertising / Social', state?.categories.advertising === true)}
            </tbody>
          </table>
          {state?.source === 'gpc' && (
            <p style={{ fontSize: 13, color: '#b45309', marginTop: 12 }}>
              Your browser sent a Global Privacy Control (GPC) signal. We have treated this as an implicit
              rejection of non-essential cookies for this visit unless you make an explicit choice below.
            </p>
          )}{' '}
          {gpc.present && gpc.enabled && state?.source !== 'gpc' && (
            <p style={{ fontSize: 13, color: '#b45309', marginTop: 12 }}>
              GPC is enabled in your browser. We honour it as an objection to selling/sharing your data.
            </p>
          )}
        </div>

        <div className="card" style={{ padding: 24, marginBottom: 16 }}>
          <h2 style={{ fontSize: 18 }}>Change your preferences</h2>
          <p style={{ fontSize: 14, marginTop: 8 }}>
            Return to the portal and open the consent centre to review or change your choices at any time.
            Your decisions are remembered and enforced for future visits.
          </p>
          <div style={{ marginTop: 12 }}>
            <Link to="/" className="btn btn-primary">Manage consent</Link>
          </div>
        </div>

        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ fontSize: 18 }}>Data sharing and your rights</h2>
          <p style={{ fontSize: 14, lineHeight: 1.7, marginTop: 8 }}>
            You have the right to access, correct, erase or withdraw consent for your personal data at any time,
            and to raise a grievance with a response within a published period. Contact our Data Protection Officer
            or use the consent centre to exercise these rights.
          </p>
        </div>
      </div>
    </div>
  )
}

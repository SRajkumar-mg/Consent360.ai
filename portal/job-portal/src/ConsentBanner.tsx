import { useEffect } from 'react'
import { useConsent } from '../useConsent'

interface ConsentBannerProps {
  purposes: { code: string; name: string; description: string; granted: boolean }[]
  onGrant: (code: string) => void
  onWithdraw: (code: string) => void
  onAcceptAll: () => void
  onRejectAll: () => void
  onSave: () => void
  onClose: () => void
  isAnonymous?: boolean
}

export function ConsentBanner({
  purposes,
  onGrant,
  onWithdraw,
  onAcceptAll,
  onRejectAll,
  onSave,
  onClose,
  isAnonymous = false,
}: ConsentBannerProps) {
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  return (
    <div className="consent-overlay" onClick={onClose}>
      <div className="consent-banner" onClick={(e) => e.stopPropagation()}>
        <div className="consent-header">
          <h2>Privacy &amp; Consent Preferences</h2>
          <p>
            We use cookies and data processing to provide you with a better experience.
            You can manage your consent preferences below. Required purposes are always enabled.
          </p>
          {isAnonymous && (
            <p className="consent-anon-note" style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
              You're currently browsing anonymously. Your choices are saved on this device only and
              will be applied to your CareerHub account when you sign in.
            </p>
          )}
        </div>

        <div className="consent-body">
          {purposes.map((p) => (
            <div key={p.code} className="consent-purpose">
              <div className="consent-purpose-info">
                <h4>{p.name}</h4>
                <p>{p.description}</p>
              </div>
              <label className="consent-toggle">
                <input
                  type="checkbox"
                  checked={p.granted}
                  onChange={() => (p.granted ? onWithdraw(p.code) : onGrant(p.code))}
                />
                <span className="slider" />
              </label>
            </div>
          ))}
          {purposes.length === 0 && (
            <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)', fontSize: 13 }}>
              No consent purposes available. Connect to Consent360 to manage preferences.
            </div>
          )}
        </div>

        <div className="consent-footer">
          <button className="btn btn-ghost" onClick={onRejectAll}>Reject All</button>
          <button className="btn btn-ghost" onClick={onSave}>Save Preferences</button>
          <button className="btn btn-success" onClick={onAcceptAll}>Accept All</button>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { getErrorMessage } from '../api/client'
import { Spinner } from '../components/ui'
import { Consent360Logo } from '../components/Logo'
import { IconCheck, IconX, IconShield } from '../components/icons'

interface PortalPurpose {
  code: string
  name: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  consent_text: string
  translations: Record<string, unknown>
  status: 'GRANTED' | 'PARTIAL' | 'NOT_GRANTED'
  granted_count: number
  total_count: number
}

interface PortalOverview {
  customer: { id: number; external_id: string; name: string; email: string; phone: string }
  purposes: PortalPurpose[]
}

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  GRANTED: { label: 'Active', cls: 'b-success' },
  PARTIAL: { label: 'Partially active', cls: 'b-warning' },
  NOT_GRANTED: { label: 'Not granted', cls: 'b-danger' },
}

export function PortalConsentPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const ctx = params.get('ctx') || ''
  const returnUrl = params.get('return_url') || ''

  const [data, setData] = useState<PortalOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    api
      .get<PortalOverview>('/portal/overview', { headers: { 'X-Context-Token': ctx } })
      .then((r) => setData(r.data))
      .catch((e) => setError(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [ctx])

  const act = async (purpose: PortalPurpose, action: 'grant' | 'withdraw') => {
    setBusy(`${action}:${purpose.code}`)
    try {
      await api.post(`/portal/${action}`, { purpose_code: purpose.code }, { headers: { 'X-Context-Token': ctx } })
      load()
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusy('')
    }
  }

  if (loading) return <Spinner />

  return (
    <div className="login-page">
      <div className="login-left">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
          <div className="logo-badge" style={{ width: 44, height: 44, fontSize: 20 }}><Consent360Logo size={20} /></div>
          <div>
            <div style={{ fontSize: 19, fontWeight: 700 }}>Consent 360</div>
            <div style={{ fontSize: 12.5, color: '#9fb0d4' }}>Your data, your choices</div>
          </div>
        </div>
        <h1 style={{ fontSize: 26, lineHeight: 1.3, maxWidth: 440 }}>
          Manage your <span style={{ color: '#8aa2ff' }}>consent</span> preferences
        </h1>
        <p style={{ marginTop: 16, color: '#b9c4da', maxWidth: 440, fontSize: 14, lineHeight: 1.6 }}>
          Review the purposes we process your data for, and grant or withdraw your consent
          at any time. Your choices take effect immediately.
        </p>
        <div style={{ display: 'flex', gap: 24, marginTop: 28, fontSize: 12.5, color: '#9fb0d4' }}>
          <span>✓ Grant anytime</span>
          <span>✓ Withdraw anytime</span>
          <span>✓ Same ease both ways</span>
        </div>
      </div>

      <div className="login-right">
        <div className="landing-entry login-card" style={{ maxWidth: 560, width: '100%' }}>
          {error && (
            <div className="card">
              <div className="card-body" style={{ textAlign: 'center', padding: 36 }}>
                <IconX size={36} style={{ color: 'var(--danger)', margin: '0 auto 12px', display: 'block' }} />
                <h3>Unable to load your consent</h3>
                <p className="text-secondary mt">{error}</p>
                <div className="mt">
                  <button className="btn" onClick={() => navigate(returnUrl || '/login')}>Return</button>
                </div>
              </div>
            </div>
          )}

          {!error && data && (
            <div className="card">
              <div className="card-header">
                <div>
                  <h3>Your consent preferences</h3>
                  <div className="text-xs text-muted">
                    {data.customer.name} · {data.customer.email || data.customer.phone || data.customer.external_id}
                  </div>
                </div>
                <span className="badge b-success"><IconCheck size={12} /> Verified</span>
              </div>

              <div className="card-body" style={{ padding: 0 }}>
                {data.purposes.length === 0 ? (
                  <div className="text-sm muted" style={{ padding: 24 }}>No consent-required purposes are active for your account.</div>
                ) : (
                  data.purposes.map((p) => {
                    const s = STATUS_LABEL[p.status] || STATUS_LABEL.NOT_GRANTED
                    return (
                      <div key={p.code} style={{ borderBottom: '1px solid var(--border)', padding: '16px 20px' }}>
                        <div className="flex-between" style={{ gap: 12 }}>
                          <div style={{ minWidth: 0 }}>
                            <div className="flex" style={{ gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                              <b>{p.name}</b>
                              <span className={`badge ${s.cls}`}>{s.label}</span>
                              {p.granted_count > 0 && p.total_count > 0 && (
                                <span className="text-xs text-muted">{p.granted_count}/{p.total_count} active</span>
                              )}
                            </div>
                            {p.description && <p className="text-sm text-secondary" style={{ marginTop: 4 }}>{p.description}</p>}
                            {p.consent_text && (
                              <p className="text-sm muted" style={{ marginTop: 4, fontStyle: 'italic' }}>“{p.consent_text}”</p>
                            )}
                            <div className="text-xs text-muted" style={{ marginTop: 4 }}>
                              Legal basis: {p.legal_basis} · Retention: {p.retention_period_days} days
                            </div>
                          </div>
                        </div>
                        <div className="flex" style={{ gap: 8, marginTop: 12 }}>
                          {p.status !== 'GRANTED' ? (
                            <button
                              className="btn btn-primary btn-sm"
                              disabled={!!busy}
                              onClick={() => act(p, 'grant')}
                            >
                              {busy === `grant:${p.code}` ? 'Granting…' : `Grant ${p.name}`}
                            </button>
                          ) : null}
                          {p.status === 'GRANTED' || p.status === 'PARTIAL' ? (
                            <button
                              className="btn btn-danger btn-sm"
                              disabled={!!busy}
                              onClick={() => act(p, 'withdraw')}
                            >
                              {busy === `withdraw:${p.code}` ? 'Withdrawing…' : `Withdraw ${p.name}`}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              <div className="card-body" style={{ borderTop: '1px solid var(--border)' }}>
                <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
                  {returnUrl ? (
                    <button className="btn" onClick={() => navigate(returnUrl)}>Return to sender</button>
                  ) : (
                    <button className="btn" onClick={() => navigate('/')}>Done</button>
                  )}
                </div>
                <p className="text-xs text-muted" style={{ marginTop: 12 }}>
                  <IconShield size={12} style={{ verticalAlign: 'middle' }} /> You can change these choices at any time. Withdrawing consent means we stop processing your data for that purpose.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { consentsApi, customersApi } from '../api'
import { getErrorMessage } from '../api/client'
import { useToast, Badge, formatDate, daysUntil, Spinner, Modal, ConfirmDialog, StatCard, MaskedValue } from '../components/ui'
import { IconCheck, IconX, IconRefresh, IconEye, IconClock, IconHistory, IconShield, IconAlert, IconDownload } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Consent, Customer, CustomerConsentSummary } from '../types'

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() || '').join('')
}

type ActionType = 'grant' | 'deny' | 'withdraw' | 'renew' | 'request'

function ActionModal({
  open, type, consent, onClose, onDone,
}: {
  open: boolean
  type: ActionType
  consent: Consent | null
  onClose: () => void
  onDone: () => void
}) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [days, setDays] = useState('365')
  const [loading, setLoading] = useState(false)

  const labels: Record<ActionType, { title: string; label: string; btn: string; cls: string }> = {
    grant: { title: 'Grant Consent', label: 'Record that the data principal has granted consent', btn: 'Grant Consent', cls: 'btn-success' },
    deny: { title: 'Deny Consent', label: 'Record that consent has been explicitly denied', btn: 'Deny Consent', cls: 'btn-danger' },
    withdraw: { title: 'Withdraw Consent', label: 'Record that the data principal has withdrawn consent', btn: 'Withdraw Consent', cls: 'btn-danger' },
    renew: { title: 'Renew Consent', label: 'Renew consent with a fresh validity period', btn: 'Renew Consent', cls: 'btn-primary' },
    request: { title: 'Request Consent', label: 'Send a consent request to the data principal', btn: 'Request Consent', cls: 'btn-primary' },
  }

  const run = async () => {
    if (!consent) return
    setLoading(true)
    try {
      if (type === 'grant') await consentsApi.grant(consent.id, { reason, expires_in_days: Number(days) || undefined })
      if (type === 'deny') await consentsApi.deny(consent.id, { reason })
      if (type === 'withdraw') await consentsApi.withdraw(consent.id, { reason })
      if (type === 'renew') await consentsApi.renew(consent.id, { reason, expires_in_days: Number(days) || undefined })
      if (type === 'request') await consentsApi.request(consent.id, { reason })
      toast('success', `${labels[type].btn} recorded successfully`)
      onDone()
      onClose()
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open={open}
      title={consent ? `${labels[type].title} — ${consent.purpose_name}` : labels[type].title}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className={`btn ${labels[type].cls}`} onClick={run} disabled={loading}>
            {loading ? 'Processing…' : labels[type].btn}
          </button>
        </>
      }
    >
      {consent && (
        <div className="mb">
          <div className="detail-grid" style={{ gridTemplateColumns: '1fr' }}>
            <div className="detail-item"><span className="k">Scope</span><div className="v">{consent.purpose_name} · {consent.data_category_name} · {consent.processing_activity_name}</div></div>
            <div className="detail-item"><span className="k">Current status</span><div className="v"><Badge status={consent.status} /></div></div>
            {consent.expires_at && <div className="detail-item"><span className="k">Expires</span><div className="v">{formatDate(consent.expires_at)} ({daysUntil(consent.expires_at)} days)</div></div>}
          </div>
          <div className="divider" />
          <div className="form-group">
            <label>{labels[type].label}</label>
            <textarea className="textarea" placeholder="Optional reason / notes" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          {(type === 'grant' || type === 'renew') && (
            <div className="form-group">
              <label>Validity period (days)</label>
              <input className="input" type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} />
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

export function CustomerConsentPage() {
  const { customerId } = useParams()
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('consent.manage')
  const [summary, setSummary] = useState<Awaited<ReturnType<typeof consentsApi.summary>>['data'] | null>(null)
  const [customer, setCustomer] = useState<Customer | null>(null)
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<{ type: ActionType; consent: Consent } | null>(null)
  const [confirmWithdraw, setConfirmWithdraw] = useState<Consent | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const load = async () => {
    if (!customerId) return
    const r = await consentsApi.summary(customerId)
    setSummary(r.data)
    setCustomer(r.data.customer)
  }

  useEffect(() => {
    if (!customerId) return
    setLoading(true)
    customersApi.get(customerId)
      .catch(() => undefined)
      .then(load)
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [customerId])

  const refresh = async () => {
    setRefreshing(true)
    await load().catch((e) => toast('error', getErrorMessage(e)))
    setRefreshing(false)
  }

  const downloadConsents = async () => {
    if (!customerId) return
    setDownloading(true)
    try {
      const r = await consentsApi.exportCustomer(customerId)
      const blob = new Blob([r.data], { type: 'application/pdf' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `consent-report-${customerId}-${new Date().toISOString().slice(0, 10)}.pdf`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast('success', 'Consent report downloaded')
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setDownloading(false)
    }
  }

  const grouped = useMemo(() => {
    if (!summary) return []
    const map = new Map<string, { purpose_code: string; purpose_name: string; purpose_id: number; consents: Consent[] }>()
    for (const c of summary.consents) {
      const key = c.purpose_code
      if (!map.has(key)) map.set(key, { purpose_code: key, purpose_name: c.purpose_name, purpose_id: c.purpose_id, consents: [] })
      map.get(key)!.consents.push(c)
    }
    return [...map.values()]
  }, [summary])

  if (loading) return <Spinner />
  if (!summary || !customer) return <div className="alert alert-error">Customer not found</div>

  const statusOrder = ['ACTIVE', 'GRANTED', 'PENDING', 'REQUESTED', 'DENIED', 'WITHDRAWN', 'EXPIRED', 'NOT_REQUESTED']

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="text-sm text-muted mb" style={{ marginBottom: 4 }}><Link to="/customers">← Customers</Link></div>
          <div className="flex" style={{ gap: 12 }}>
            <span className="avatar avatar-lg">{initials(customer.name)}</span>
            <div>
              <h1>{customer.name}</h1>
              <div className="sub">
                <span className="mono">{customer.external_id}</span> · {customer.email ? <MaskedValue type="email" value={customer.email} /> : 'no email'} · {customer.phone ? <MaskedValue type="phone" value={customer.phone} /> : 'no phone'}
                {' · '}<Badge status={customer.status} />
              </div>
            </div>
          </div>
        </div>
        <div className="flex" style={{ gap: 8 }}>
          <button className="btn" onClick={downloadConsents} disabled={downloading}>
            <IconDownload size={15} /> {downloading ? 'Exporting…' : 'Download PDF'}
          </button>
          <button className="btn" onClick={refresh} disabled={refreshing}>
            <IconRefresh size={15} /> Refresh
          </button>
        </div>
      </div>

      <div className="stat-grid mb">
        <StatCard label="Active Consents" value={count(summary, ['ACTIVE', 'GRANTED', 'RENEWED', 'UPDATED'])} icon={<IconCheck size={20} />} tone="success" sub={`of ${summary.consents.length} records`} />
        <StatCard label="Pending" value={count(summary, ['PENDING', 'REQUESTED', 'NOT_REQUESTED'])} icon={<IconClock size={20} />} tone="warning" sub="awaiting action" />
        <StatCard label="Denied" value={count(summary, ['DENIED'])} icon={<IconX size={20} />} tone="danger" sub="explicitly refused" />
        <StatCard label="Withdrawn" value={count(summary, ['WITHDRAWN'])} icon={<IconAlert size={20} />} tone="danger" sub="revoked by principal" />
        <StatCard label="Expired" value={count(summary, ['EXPIRED'])} icon={<IconHistory size={20} />} tone="slate" sub="past validity" />
        <StatCard label="Expiring ≤ 30 days" value={summary.expiring_soon.length} icon={<IconShield size={20} />} tone="warning" sub="renew soon" />
      </div>

      {summary.expiring_soon.length > 0 && (
        <div className="card mb" style={{ borderColor: '#f3d7a5', background: '#fffdf7' }}>
          <div className="card-header">
            <h3 style={{ color: 'var(--warning)' }}>⚠ Expiring consents</h3>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Purpose</th><th>Data</th><th>Activity</th><th>Expires</th><th>Days left</th></tr></thead>
              <tbody>
                {summary.expiring_soon.map((c) => (
                  <tr key={c.id}>
                    <td>{c.purpose_name}</td><td>{c.data_category_name}</td><td>{c.processing_activity_name}</td>
                    <td>{formatDate(c.expires_at)}</td>
                    <td>{daysUntil(c.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {grouped.map((g) => {
        const active = g.consents.filter((c) => ['ACTIVE', 'GRANTED', 'RENEWED', 'UPDATED'].includes(c.status)).length
        const allActive = active === g.consents.length
        const noneActive = active === 0
        return (
          <div className="card card-hover mb" key={g.purpose_code}>
            <div className="card-header">
              <div style={{ flex: 1, minWidth: 0 }}>
                <h3>{g.purpose_name}</h3>
                <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                  {g.purpose_code} · {active}/{g.consents.length} active
                </div>
                <div className="progress mt-sm" style={{ maxWidth: 320 }}>
                  <div
                    className="progress-bar"
                    style={{
                      width: `${g.consents.length ? (active / g.consents.length) * 100 : 0}%`,
                      background: allActive ? 'var(--success)' : noneActive ? 'var(--slate)' : 'var(--warning)',
                    }}
                  />
                </div>
              </div>
              <div className="flex">
                {canManage && !allActive && g.consents.some((c) => ['REQUESTED', 'PENDING', 'NOT_REQUESTED', 'DENIED', 'WITHDRAWN', 'EXPIRED'].includes(c.status)) && (
                  <button className="btn btn-success btn-sm" onClick={() => { const c = g.consents.find((x) => x.status !== 'ACTIVE' && x.status !== 'GRANTED') || g.consents[0]; setAction({ type: 'grant', consent: c }) }}>
                    <IconCheck size={13} /> Grant
                  </button>
                )}
                {canManage && allActive && (
                  <button className="btn btn-danger btn-sm" onClick={() => setConfirmWithdraw(g.consents[0])}>
                    <IconX size={13} /> Withdraw all
                  </button>
                )}
                {canManage && noneActive && (
                  <button className="btn btn-primary btn-sm" onClick={() => setAction({ type: 'renew', consent: g.consents[0] })}>
                    <IconRefresh size={13} /> Renew
                  </button>
                )}
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Data Category</th><th>Processing Activity</th><th>Status</th><th>Granted</th><th>Expires</th><th>Version</th><th>Actions</th></tr>
                </thead>
                <tbody>
                  {[...g.consents].sort((a, b) => statusOrder.indexOf(a.status) - statusOrder.indexOf(b.status)).map((c) => (
                    <tr key={c.id}>
                      <td>{c.data_category_name}</td>
                      <td>{c.processing_activity_name}</td>
                      <td><Badge status={c.status} />{c.re_consent_required && <div className="mt-sm"><Badge status="REQUESTED">re-consent</Badge></div>}</td>
                      <td className="muted">{formatDate(c.granted_at)}</td>
                      <td className="muted">{formatDate(c.expires_at)}</td>
                      <td className="muted">v{c.consent_version}</td>
                      <td>
                        <div className="flex" style={{ gap: 4 }}>
                          {canManage && ['NOT_REQUESTED', 'REQUESTED', 'PENDING', 'DENIED', 'WITHDRAWN', 'EXPIRED'].includes(c.status) && (
                            <button className="btn btn-sm btn-success" title="Grant" onClick={() => setAction({ type: 'grant', consent: c })}>
                              <IconCheck size={13} />
                            </button>
                          )}
                          {canManage && ['REQUESTED', 'PENDING', 'NOT_REQUESTED'].includes(c.status) && (
                            <button className="btn btn-sm" title="Deny" onClick={() => setAction({ type: 'deny', consent: c })}>
                              <IconX size={13} />
                            </button>
                          )}
                          {canManage && ['ACTIVE', 'GRANTED', 'RENEWED', 'UPDATED'].includes(c.status) && (
                            <button className="btn btn-sm btn-danger" title="Withdraw" onClick={() => setConfirmWithdraw(c)}>
                              <IconX size={13} />
                            </button>
                          )}
                          {canManage && ['ACTIVE', 'GRANTED', 'RENEWED', 'UPDATED', 'EXPIRED', 'WITHDRAWN'].includes(c.status) && (
                            <button className="btn btn-sm" title="Renew" onClick={() => setAction({ type: 'renew', consent: c })}>
                              <IconRefresh size={13} />
                            </button>
                          )}
                          {canManage && c.status === 'NOT_REQUESTED' && (
                            <button className="btn btn-sm" title="Request consent" onClick={() => setAction({ type: 'request', consent: c })}>
                              <IconClock size={13} />
                            </button>
                          )}
                          <Link className="btn btn-sm" title="View details" to={`/consent/${c.id}`}>
                            <IconEye size={13} />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}

      <ActionModal
        open={!!action}
        type={action?.type ?? 'grant'}
        consent={action?.consent ?? null}
        onClose={() => setAction(null)}
        onDone={refresh}
      />
      <ConfirmDialog
        open={!!confirmWithdraw}
        title="Withdraw consent"
        message={`Withdraw consent for ${confirmWithdraw?.purpose_name} (${confirmWithdraw?.data_category_name} / ${confirmWithdraw?.processing_activity_name})? This will prevent further processing of this data for the purpose.`}
        confirmLabel="Withdraw Consent"
        danger
        onClose={() => setConfirmWithdraw(null)}
        onConfirm={async () => {
          if (!confirmWithdraw) return
          try {
            await consentsApi.withdraw(confirmWithdraw.id, { reason: 'Withdrawn by consent administrator' })
            toast('success', 'Consent withdrawn')
            setConfirmWithdraw(null)
            refresh()
          } catch (e) {
            toast('error', getErrorMessage(e))
          }
        }}
      />
    </div>
  )
}

function count(summary: CustomerConsentSummary, statuses: string[]) {
  let n = 0
  for (const s of statuses) n += summary.status_counts[s] || 0
  return n
}

/**
 * R3-07: processors, their contracts, and the propagation of instructions to
 * them (`/processors`).
 *
 * s.8(2) makes the fiduciary responsible for a processor's compliance and only
 * permits engaging one "under a valid contract". The contract-coverage report
 * is therefore the point of this screen: it is not a list of vendors, it is a
 * per-processor answer to "is this engagement lawful, and if not, which clause
 * is missing". Gaps are shown per row, in words, rather than as a red dot.
 *
 * The webhook secret is shown exactly once, at creation and at rotation, and
 * the screen says so before it is generated. Afterwards only a fingerprint is
 * stored, so a "show me the secret again" affordance would be a lie.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { organizationsApi, processorsApi, sharingEventsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDate, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox, IconLink, IconPlus } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type {
  DataSharingEvent, Organization, Processor, ProcessorAlert, ProcessorCoverage, ProcessorSecret,
} from '../types'

type Tab = 'register' | 'coverage' | 'alerts' | 'sharing'

const BLANK_PROCESSOR = {
  name: '', type: 'PROCESSOR', country: 'IN', tenant_code: '', contact_name: '', contact_email: '',
  contact_phone: '', escalation_email: '', contract_ref: '', contract_signed_on: '', contract_valid_from: '',
  contract_valid_until: '', security_clause_ref: '', security_measures: '', erasure_clause_ref: '',
  erasure_sla_days: '', webhook_url: '', ack_sla_hours: '24', notes: '', is_active: true,
}

export function ProcessorsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('policy.manage')
  const canAck = hasPermission('consent.manage')
  const canSeeSharing = hasPermission('consent.view')

  const [tab, setTab] = useState<Tab>('register')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [processors, setProcessors] = useState<Processor[]>([])
  const [coverage, setCoverage] = useState<ProcessorCoverage | null>(null)
  const [alerts, setAlerts] = useState<ProcessorAlert[]>([])
  const [sharing, setSharing] = useState<DataSharingEvent[]>([])
  const [tenants, setTenants] = useState<Organization[]>([])
  const [includeInactive, setIncludeInactive] = useState(false)
  const [alertStatus, setAlertStatus] = useState('')

  const [form, setForm] = useState<typeof BLANK_PROCESSOR | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [secret, setSecret] = useState<ProcessorSecret | null>(null)

  const load = useCallback(async () => {
    const [p, c, a] = await Promise.all([
      processorsApi.list({ include_inactive: includeInactive }),
      processorsApi.coverage(),
      processorsApi.alerts(alertStatus ? { status: alertStatus, limit: 200 } : { limit: 200 }),
    ])
    setProcessors(p.data); setCoverage(c.data); setAlerts(a.data)
    if (canSeeSharing) {
      const s = await sharingEventsApi.list().catch(() => ({ data: [] as DataSharingEvent[] }))
      setSharing(s.data)
    }
  }, [includeInactive, alertStatus, canSeeSharing])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => { organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([])) }, [])

  const run = async (fn: () => Promise<unknown>, message: string): Promise<boolean> => {
    setBusy(true); setRefusal('')
    try {
      await fn(); toast('success', message); await load(); return true
    } catch (e) { setRefusal(getErrorMessage(e)); return false } finally { setBusy(false) }
  }

  const overdueAlerts = useMemo(
    () => alerts.filter((a) => a.acknowledged_at == null && new Date(a.due_at).getTime() < Date.now()).length,
    [alerts],
  )

  const openEdit = (p: Processor) => {
    setEditingId(p.id)
    setForm({
      name: p.name, type: p.type, country: p.country, tenant_code: '',
      contact_name: p.contact_name, contact_email: '', contact_phone: '', escalation_email: '',
      contract_ref: p.contract_ref,
      contract_signed_on: p.contract_signed_on || '', contract_valid_from: p.contract_valid_from || '',
      contract_valid_until: p.contract_valid_until || '', security_clause_ref: p.security_clause_ref,
      security_measures: p.security_measures, erasure_clause_ref: p.erasure_clause_ref,
      erasure_sla_days: p.erasure_sla_days == null ? '' : String(p.erasure_sla_days),
      webhook_url: p.webhook_url, ack_sla_hours: String(p.ack_sla_hours), notes: p.notes, is_active: p.is_active,
    })
  }

  const submit = async () => {
    if (!form) return
    const body: Record<string, unknown> = {
      name: form.name, type: form.type, country: form.country,
      contact_name: form.contact_name, contract_ref: form.contract_ref,
      contract_signed_on: form.contract_signed_on || null,
      contract_valid_from: form.contract_valid_from || null,
      contract_valid_until: form.contract_valid_until || null,
      security_clause_ref: form.security_clause_ref, security_measures: form.security_measures,
      erasure_clause_ref: form.erasure_clause_ref,
      erasure_sla_days: form.erasure_sla_days === '' ? null : Number(form.erasure_sla_days),
      webhook_url: form.webhook_url, ack_sla_hours: Number(form.ack_sla_hours || 24),
      notes: form.notes, is_active: form.is_active,
    }
    // Contact details are write-only: the API returns them masked, so sending
    // an empty string back would silently wipe a stored address.
    if (form.contact_email) body.contact_email = form.contact_email
    if (form.contact_phone) body.contact_phone = form.contact_phone
    if (form.escalation_email) body.escalation_email = form.escalation_email
    if (!editingId && form.tenant_code) body.tenant_code = form.tenant_code

    setBusy(true); setRefusal('')
    try {
      if (editingId) {
        await processorsApi.update(editingId, body)
        toast('success', 'Processor updated.')
      } else {
        const r = await processorsApi.create(body)
        if (r.data.webhook_secret) setSecret({
          processor_id: r.data.id, webhook_secret: r.data.webhook_secret,
          webhook_secret_fingerprint: r.data.webhook_secret_fingerprint,
          webhook_secret_set_at: r.data.webhook_secret_set_at || new Date().toISOString(),
        })
        toast('success', 'Processor registered.')
      }
      setForm(null); setEditingId(null)
      await load()
    } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Processors & contracts"
        subtitle="Every processor this fiduciary engages, whether each engagement rests on a valid contract, and whether instructions reach them inside their SLA"
        actions={canManage && (
          <button className="btn btn-primary" onClick={() => { setEditingId(null); setForm({ ...BLANK_PROCESSOR, tenant_code: tenants[0]?.code || '' }) }}>
            <IconPlus size={14} /> Register a processor
          </button>
        )}
      />

      {!canManage && <ReadOnlyBanner permission="policy.manage" what="Registering, editing and deactivating a processor, and dispatching alerts" />}

      <RefusalNotice title="The processor register refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      {coverage && (
        <div className="metric-grid mb">
          <MetricCard label="Processors" value={coverage.processors_total} tone="primary" icon={<IconLink size={20} />}
            sub={`${processors.filter((p) => p.is_active).length} active`} />
          <MetricCard label="Contract coverage" value={formatPct(coverage.coverage_pct)}
            tone={coverage.coverage_pct == null ? 'slate' : coverage.coverage_pct >= 100 ? 'success' : 'danger'}
            icon={<IconCheck size={20} />}
            sub={coverage.processors_total === 0
              ? 'no processor is registered — nothing to measure'
              : `${coverage.processors_covered} of ${coverage.processors_total} on a valid s.8(2) contract`} />
          <MetricCard label="With gaps" value={coverage.processors_with_gaps} tone={coverage.processors_with_gaps ? 'warning' : 'success'}
            icon={<IconAlert size={20} />} sub="a missing clause is a lawfulness problem, not paperwork" />
          <MetricCard label="Alerts past SLA" value={overdueAlerts} tone={overdueAlerts ? 'danger' : 'success'}
            icon={<IconClock size={20} />} sub="unacknowledged past the agreed window" />
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'register', label: 'Register', count: processors.length },
          { id: 'coverage', label: 'Contract coverage', count: coverage?.processors_with_gaps ?? null },
          { id: 'alerts', label: 'Propagation alerts', count: alerts.length },
          { id: 'sharing', label: 'Sharing events', count: canSeeSharing ? sharing.length : null },
        ]}
      />

      {tab === 'register' && (
        <div className="card">
          <div className="card-header">
            <h3>Processor register</h3>
            <label className="flex text-sm" style={{ gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} />
              Include deactivated
            </label>
          </div>
          {processors.length === 0 ? (
            <div className="card-body"><EmptyState message="No processor is registered." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Processor</th><th>Type</th><th>Country</th><th>Contract</th><th>Webhook</th><th>Ack SLA</th><th>State</th><th /></tr></thead>
                <tbody>
                  {processors.map((p) => (
                    <tr key={p.id}>
                      <td><b>{p.name}</b>
                        <div className="text-xs text-muted">{p.contact_name}{p.contact_email_masked ? ` · ${p.contact_email_masked}` : ''}</div></td>
                      <td>{p.type}</td>
                      <td>{p.country}</td>
                      <td>
                        <span className="mono text-xs">{p.contract_ref || <span className="text-danger">none</span>}</span>
                        <div className="text-xs text-muted">
                          {p.contract_valid_until ? `until ${formatDate(p.contract_valid_until)}` : 'no end date'}
                          {' · '}
                          {p.contract_in_force ? <span className="text-success">in force</span> : <span className="text-danger">not in force</span>}
                        </div>
                      </td>
                      <td className="text-xs">
                        {p.webhook_configured
                          ? <>configured<div className="text-muted mono">{p.webhook_secret_fingerprint.slice(0, 12)}…</div></>
                          : <span className="text-muted">not configured</span>}
                      </td>
                      <td>{p.ack_sla_hours} h</td>
                      <td><Badge status={p.is_active ? 'ACTIVE' : 'EXPIRED'}>{p.is_active ? 'Active' : 'Deactivated'}</Badge></td>
                      <td>
                        {canManage && (
                          <div className="flex" style={{ gap: 6, flexWrap: 'wrap' }}>
                            <button className="btn btn-sm" onClick={() => openEdit(p)}>Edit</button>
                            <button className="btn btn-sm" disabled={busy} onClick={async () => {
                              setBusy(true); setRefusal('')
                              try {
                                const r = await processorsApi.rotateSecret(p.id)
                                setSecret(r.data)
                                await load()
                              } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
                            }}>Rotate secret</button>
                            {p.is_active && (
                              <button className="btn btn-ghost-danger btn-sm" disabled={busy}
                                onClick={() => run(() => processorsApi.deactivate(p.id), `${p.name} deactivated.`)}>
                                Deactivate
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'coverage' && coverage && (
        <div className="card">
          <div className="card-header">
            <h3>s.8(2) contract coverage</h3>
            <span className="text-sm text-muted">Generated {formatDateTime(coverage.generated_at)}</span>
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              s.8(2) permits engaging a processor only under a valid contract. A processor is <b>covered</b> when it
              has a contract reference in force, a security clause, an erasure clause, and a route by which
              instructions can reach it. The gaps column names what is missing rather than scoring it.
            </p>
            {coverage.contracts_expiring_within_90_days.length > 0 && (
              <div className="alert alert-info">
                <b>{coverage.contracts_expiring_within_90_days.length} contract(s) expire within 90 days.</b> An expired
                contract makes the engagement itself unlawful, not merely out of date.
              </div>
            )}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Processor</th><th>Contract</th><th>In force</th><th>Security</th><th>Erasure</th><th>Reachable</th><th>Covered</th><th>Gaps</th></tr></thead>
              <tbody>
                {coverage.processors.map((r) => (
                  <tr key={r.processor_id}>
                    <td><b>{r.name}</b><div className="text-xs text-muted">{r.type} · {r.country}</div></td>
                    <td className="mono text-xs">{r.contract_ref || '—'}</td>
                    <td>{r.contract_in_force ? <IconCheck size={15} /> : <span className="text-danger">no</span>}</td>
                    <td>{r.has_security_clause ? <IconCheck size={15} /> : <span className="text-danger">no</span>}</td>
                    <td>{r.has_erasure_clause ? <IconCheck size={15} /> : <span className="text-danger">no</span>}</td>
                    <td>{r.reachable_for_instructions ? <IconCheck size={15} /> : <span className="text-danger">no</span>}</td>
                    <td><Badge status={r.covered ? 'ACTIVE' : 'DENIED'}>{r.covered ? 'Covered' : 'Not covered'}</Badge></td>
                    <td className="text-xs text-secondary">{r.gaps.length ? r.gaps.join('; ') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'alerts' && (
        <div className="card">
          <div className="card-header">
            <h3>Propagation alerts</h3>
            <div className="flex" style={{ gap: 8 }}>
              <select className="input" style={{ width: 'auto' }} value={alertStatus} onChange={(e) => setAlertStatus(e.target.value)} aria-label="Alert status">
                <option value="">All statuses</option>
                {['PENDING', 'SENT', 'ACKNOWLEDGED', 'FAILED', 'ESCALATED'].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              {canManage && (
                <button className="btn btn-sm" disabled={busy} onClick={async () => {
                  setBusy(true); setRefusal('')
                  try {
                    const r = await processorsApi.dispatch()
                    toast('success', `Dispatched: ${r.data.sent} sent, ${r.data.failed_or_retrying} failed or retrying of ${r.data.attempted} attempted.`)
                    await load()
                  } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
                }}>Dispatch now</button>
              )}
            </div>
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              When a principal withdraws consent or an erasure executes, the instruction has to reach every processor
              holding that data. An alert is that instruction; the SLA is the window the contract gives them to
              acknowledge it.
            </p>
          </div>
          {alerts.length === 0 ? (
            <div className="card-body"><EmptyState message="No propagation alert has been raised. Alerts are created by withdrawals and erasures, not by hand." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Alert</th><th>Processor</th><th>Type</th><th>Status</th><th>Attempts</th><th>Due</th><th>Acknowledged</th><th /></tr></thead>
                <tbody>
                  {alerts.map((a) => {
                    const overdue = !a.acknowledged_at && new Date(a.due_at).getTime() < Date.now()
                    return (
                      <tr key={a.alert_ref}>
                        <td className="mono text-xs">{a.alert_ref}<div className="text-muted">{a.trigger_ref}</div></td>
                        <td>{a.processor_name}</td>
                        <td>{a.alert_type}</td>
                        <td>
                          <Badge status={a.status === 'ACKNOWLEDGED' ? 'ACTIVE' : a.status === 'FAILED' ? 'DENIED' : 'PENDING'}>{a.status}</Badge>
                          {a.last_error && <div className="job-blocked">{a.last_error}</div>}
                        </td>
                        <td>{a.attempts}/{a.max_attempts}{a.http_status ? ` · HTTP ${a.http_status}` : ''}</td>
                        <td className={overdue ? 'text-danger text-xs' : 'text-xs'}>
                          {formatDateTime(a.due_at)}<div className="text-muted">{a.ack_sla_hours} h SLA</div>
                        </td>
                        <td className="text-xs">
                          {a.acknowledged_at
                            ? <>{formatDateTime(a.acknowledged_at)}<div className="text-muted">{a.acknowledged_by} · {a.ack_method}</div></>
                            : <span className="text-muted">not acknowledged</span>}
                        </td>
                        <td>
                          {canAck && !a.acknowledged_at && (
                            <button className="btn btn-sm" disabled={busy} onClick={() => {
                              const who = window.prompt('Who at the processor acknowledged this, and how?')
                              if (!who) return
                              const ref = window.prompt('Their reference for the acknowledgement (optional)') || ''
                              run(() => processorsApi.ackManual(a.alert_ref, { acknowledged_by: who, reference: ref }),
                                'Manual acknowledgement recorded.')
                            }}>Record manual ack</button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'sharing' && (
        !canSeeSharing ? <ReadOnlyBanner permission="consent.view" what="Reading the data-sharing log" /> : (
          <div className="card">
            <div className="card-header"><h3>Data-sharing events</h3></div>
            <div className="card-body">
              <p className="text-sm text-secondary">
                Each row records personal data actually leaving for a processor: which principal, which purpose, which
                categories, and the legal basis relied on. <b>Signature valid</b> is the check that the row has not been
                altered since it was written.
              </p>
            </div>
            {sharing.length === 0 ? (
              <div className="card-body"><EmptyState message="No sharing event has been recorded." /></div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>When</th><th>Principal</th><th>Processor</th><th>Purpose</th><th>Categories</th><th>Event</th><th>Basis</th><th>Signature</th></tr></thead>
                  <tbody>
                    {sharing.map((s) => (
                      <tr key={s.id}>
                        <td className="text-xs">{formatDateTime(s.occurred_at)}</td>
                        <td>#{s.customer_id}</td>
                        <td>{s.processor_name}</td>
                        <td className="mono text-xs">{s.purpose_code}</td>
                        <td className="text-xs">{s.data_category_codes.join(', ')}</td>
                        <td>{s.event_type}</td>
                        <td className="text-xs">{s.legal_basis}</td>
                        <td>{s.signature_valid
                          ? <span className="text-success">valid</span>
                          : <span className="text-danger">INVALID</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      )}

      <FooterRow left={coverage ? `${coverage.processors_covered} of ${coverage.processors_total} processors on a valid contract` : ''} />

      {/* --------------------------- Editor --------------------------- */}
      <Modal open={!!form} wide title={editingId ? 'Edit processor' : 'Register a processor'} onClose={() => { setForm(null); setEditingId(null) }}
        footer={
          <>
            <button className="btn" onClick={() => { setForm(null); setEditingId(null) }} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !form?.name} onClick={submit}>
              {busy ? 'Saving…' : editingId ? 'Save' : 'Register'}
            </button>
          </>
        }>
        {form && (
          <>
            {!editingId && (
              <div className="alert alert-info mb">
                A webhook signing secret is generated on registration and shown <b>once</b>, on the next screen. Only a
                fingerprint is stored afterwards, so it cannot be shown again — copy it before closing.
              </div>
            )}
            <div className="form-row">
              <div className="form-group"><label>Name</label>
                <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
              <div className="form-group"><label>Type</label>
                <input className="input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} /></div>
              <div className="form-group"><label>Country</label>
                <input className="input" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} /></div>
              {!editingId && (
                <div className="form-group"><label>Tenant</label>
                  <select className="select" value={form.tenant_code} onChange={(e) => setForm({ ...form, tenant_code: e.target.value })}>
                    <option value="">Platform-wide</option>
                    {tenants.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                  </select></div>
              )}
            </div>

            <h4 className="kpi-section-title">Contract (s.8(2))</h4>
            <div className="form-row">
              <div className="form-group"><label>Contract reference</label>
                <input className="input" value={form.contract_ref} onChange={(e) => setForm({ ...form, contract_ref: e.target.value })} /></div>
              <div className="form-group"><label>Signed on</label>
                <input className="input" type="date" value={form.contract_signed_on} onChange={(e) => setForm({ ...form, contract_signed_on: e.target.value })} /></div>
              <div className="form-group"><label>Valid from</label>
                <input className="input" type="date" value={form.contract_valid_from} onChange={(e) => setForm({ ...form, contract_valid_from: e.target.value })} /></div>
              <div className="form-group"><label>Valid until</label>
                <input className="input" type="date" value={form.contract_valid_until} onChange={(e) => setForm({ ...form, contract_valid_until: e.target.value })} /></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label>Security clause reference</label>
                <input className="input" value={form.security_clause_ref} onChange={(e) => setForm({ ...form, security_clause_ref: e.target.value })} /></div>
              <div className="form-group"><label>Erasure clause reference</label>
                <input className="input" value={form.erasure_clause_ref} onChange={(e) => setForm({ ...form, erasure_clause_ref: e.target.value })} /></div>
              <div className="form-group"><label>Erasure SLA (days)</label>
                <input className="input" type="number" min={0} value={form.erasure_sla_days}
                  onChange={(e) => setForm({ ...form, erasure_sla_days: e.target.value })} /></div>
            </div>
            <div className="form-group"><label>Security measures</label>
              <textarea className="textarea" rows={2} value={form.security_measures}
                onChange={(e) => setForm({ ...form, security_measures: e.target.value })} /></div>

            <h4 className="kpi-section-title">Reaching them with an instruction</h4>
            <div className="form-row">
              <div className="form-group"><label>Webhook URL</label>
                <input className="input" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} /></div>
              <div className="form-group"><label>Acknowledgement SLA (hours)</label>
                <input className="input" type="number" min={1} value={form.ack_sla_hours}
                  onChange={(e) => setForm({ ...form, ack_sla_hours: e.target.value })} /></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label>Contact name</label>
                <input className="input" value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} /></div>
              <div className="form-group"><label>Contact email {editingId && <span className="text-xs text-muted">(blank keeps the stored one)</span>}</label>
                <input className="input" type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></div>
              <div className="form-group"><label>Escalation email</label>
                <input className="input" type="email" value={form.escalation_email} onChange={(e) => setForm({ ...form, escalation_email: e.target.value })} /></div>
            </div>
            <div className="form-group"><label>Notes</label>
              <input className="input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
            <div className="form-group">
              <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                <span>Active</span>
              </label>
            </div>
          </>
        )}
      </Modal>

      {/* --------------------------- Secret --------------------------- */}
      <Modal open={!!secret} title="Webhook signing secret — shown once" onClose={() => setSecret(null)}
        footer={<button className="btn btn-primary" onClick={() => setSecret(null)}>I have copied it</button>}>
        {secret && (
          <>
            <div className="alert alert-error mb">
              <b>This is the only time this value is displayed.</b> Only its fingerprint is stored, so it cannot be
              retrieved later — rotating produces a new secret and invalidates this one.
            </div>
            <div className="form-group">
              <label>Secret</label>
              <input className="input mono" readOnly value={secret.webhook_secret} onFocus={(e) => e.currentTarget.select()} />
            </div>
            <dl className="detail-grid">
              <div className="detail-item"><dt>Fingerprint</dt><dd className="mono text-xs">{secret.webhook_secret_fingerprint}</dd></div>
              <div className="detail-item"><dt>Set at</dt><dd>{formatDateTime(secret.webhook_secret_set_at)}</dd></div>
            </dl>
            {secret.warning && <div className="alert alert-info">{secret.warning}</div>}
          </>
        )}
      </Modal>
    </div>
  )
}

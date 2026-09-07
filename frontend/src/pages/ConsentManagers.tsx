/**
 * R3-10: the Consent Manager register (`/consent-managers`).
 *
 * A Consent Manager is the s.6(7) intermediary a Data Principal may use to
 * give, manage, review and withdraw consent. It is registered with the Board,
 * it must be *data blind*, and under the 2025 Rules' First Schedule Part B it
 * has to publish who owns and runs it.
 *
 * Two honest limits this screen states rather than papers over:
 *
 *  1. **Artefacts are not readable from this console.** The artefact APIs
 *     authenticate with an integration API key bound to one tenant, not with a
 *     staff JWT — deliberately, since an artefact belongs to the Consent
 *     Manager that brokered it. Showing an empty artefact table here would read
 *     as "no consents have been brokered", which this console cannot know.
 *  2. **Availability is measured over a window.** The metrics tile says which
 *     window, because "99%" over one hour and over thirty days are different
 *     claims.
 */
import { useCallback, useEffect, useState } from 'react'
import { consentManagerApi, organizationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox, IconPlus, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type {
  ConsentManagerMetrics, ConsentManagerRegistration, FiduciaryOnboarding, Organization,
} from '../types'

type Tab = 'register' | 'fiduciaries' | 'service'

// consent_managers.registration_status and fiduciary_onboardings.status CHECK
// constraints — the API rejects anything else.
const REGISTRATION_STATUSES = ['PENDING', 'REGISTERED', 'SUSPENDED', 'DEREGISTERED']
const ONBOARDING_STATUSES = ['PENDING', 'ACTIVE', 'SUSPENDED', 'TERMINATED']

const WINDOWS = [
  { hours: 24, label: 'Last 24 hours' },
  { hours: 24 * 7, label: 'Last 7 days' },
  { hours: 24 * 30, label: 'Last 30 days' },
]

export function ConsentManagersPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('policy.manage')

  // The register itself is policy.manage-only; an auditor holding audit.view can
  // still read the service level, so that is where they land rather than on a
  // tab that can only tell them they are not allowed to see it.
  const [tab, setTab] = useState<Tab>(canManage ? 'register' : 'service')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [registrations, setRegistrations] = useState<ConsentManagerRegistration[]>([])
  const [metrics, setMetrics] = useState<ConsentManagerMetrics | null>(null)
  const [tenants, setTenants] = useState<Organization[]>([])
  const [windowHours, setWindowHours] = useState(24)

  const [selected, setSelected] = useState<ConsentManagerRegistration | null>(null)
  const [fiduciaries, setFiduciaries] = useState<FiduciaryOnboarding[]>([])

  const [form, setForm] = useState<null | {
    cm_ref: string | null; name: string; tenant_code: string; board_registration_number: string
    registration_status: string; contact_email: string; website_url: string
  }>(null)
  const [onboardForm, setOnboardForm] = useState<null | { source_app: string; status: string; allowed_purpose_codes: string; notes: string }>(null)

  const load = useCallback(async () => {
    const [r, m] = await Promise.all([
      canManage ? consentManagerApi.registrations() : Promise.resolve({ data: [] as ConsentManagerRegistration[] }),
      consentManagerApi.metrics({ window_hours: windowHours }).catch(() => ({ data: null })),
    ])
    setRegistrations(r.data)
    setMetrics(m.data as ConsentManagerMetrics | null)
  }, [canManage, windowHours])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => { organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([])) }, [])

  useEffect(() => {
    if (!selected) { setFiduciaries([]); return }
    consentManagerApi.fiduciaries(selected.cm_ref)
      .then((r) => setFiduciaries(r.data))
      .catch((e) => setRefusal(getErrorMessage(e)))
  }, [selected])

  const run = async (fn: () => Promise<unknown>, message: string): Promise<boolean> => {
    setBusy(true); setRefusal('')
    try { await fn(); toast('success', message); await load(); return true }
    catch (e) { setRefusal(getErrorMessage(e)); return false }
    finally { setBusy(false) }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Consent Managers"
        subtitle="The s.6(7) intermediaries registered with the Board, the fiduciaries each has onboarded, and the service level the platform actually delivered to them"
        actions={canManage && (
          <button className="btn btn-primary" onClick={() => setForm({
            cm_ref: null, name: '', tenant_code: tenants[0]?.code || '', board_registration_number: '',
            registration_status: 'PENDING', contact_email: '', website_url: '',
          })}><IconPlus size={14} /> Register a Consent Manager</button>
        )}
      />

      {!canManage && <ReadOnlyBanner permission="policy.manage" what="Reading the register and onboarding a fiduciary" />}

      <RefusalNotice title="The Consent Manager API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      {metrics && (
        <div className="metric-grid mb">
          <MetricCard label="Registered" value={metrics.registered_consent_manager_count} tone="primary" icon={<IconShield size={20} />}
            sub={`${metrics.onboarded_fiduciary_count} fiduciary onboarding(s)`} />
          <MetricCard
            label={`Availability (${WINDOWS.find((w) => w.hours === windowHours)?.label.toLowerCase()})`}
            value={formatPct(metrics.availability_pct, 2)}
            tone={metrics.availability_pct == null ? 'slate' : metrics.availability_pct >= 99.9 ? 'success' : 'warning'}
            icon={<IconCheck size={20} />}
            sub={metrics.total_calls === 0
              ? 'no call was made in this window — unknown, not 100%'
              : `${metrics.total_calls} calls · ${formatPct(metrics.error_rate_pct, 2, 'unknown')} errors`} />
          <MetricCard label="p95 latency"
            value={metrics.latency_ms_p95 == null ? 'No data yet' : `${metrics.latency_ms_p95} ms`}
            tone={metrics.latency_ms_p95 == null ? 'slate' : 'info'} icon={<IconClock size={20} />}
            sub={metrics.latency_ms_p50 == null ? 'no sample in this window' : `p50 ${metrics.latency_ms_p50} ms`} />
          <MetricCard label="Artefacts" value={metrics.artefacts_total} tone="info" icon={<IconInbox size={20} />}
            sub={`${metrics.artefacts_active} active · ${metrics.artefacts_withdrawn} withdrawn`} />
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'register', label: 'Register', count: registrations.length },
          { id: 'fiduciaries', label: 'Onboarded fiduciaries', count: selected ? fiduciaries.length : null },
          { id: 'service', label: 'Service level' },
        ]}
      />

      {tab === 'register' && (
        <div className="card">
          <div className="card-header"><h3>Registered Consent Managers</h3></div>
          {!canManage ? (
            <div className="card-body">
              <EmptyState
                title="Not visible to your role"
                message="The Consent Manager register requires the policy.manage permission. The service-level tab is readable with audit.view and is unaffected."
              />
            </div>
          ) : registrations.length === 0 ? (
            <div className="card-body">
              <EmptyState title="No Consent Manager is registered"
                message="Nothing brokers consent for this platform yet. This is a real state, not a missing screen." />
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Consent Manager</th><th>Tenant</th><th>Board registration</th><th>Status</th><th>Fiduciaries</th><th>Registered</th><th /></tr></thead>
                <tbody>
                  {registrations.map((cm) => (
                    <tr key={cm.cm_ref}>
                      <td><b>{cm.name}</b><div className="mono text-xs text-muted">{cm.cm_ref}</div>
                        {cm.website_url && <div className="text-xs"><a href={cm.website_url} target="_blank" rel="noreferrer">{cm.website_url}</a></div>}</td>
                      <td className="mono text-xs">{cm.tenant_code}</td>
                      <td className="mono text-xs">{cm.board_registration_number || <span className="text-danger">none</span>}</td>
                      <td><Badge status={cm.registration_status === 'REGISTERED' ? 'ACTIVE' : cm.registration_status === 'SUSPENDED' ? 'DENIED' : 'PENDING'}>{cm.registration_status}</Badge></td>
                      <td>{cm.onboarded_fiduciary_count}</td>
                      <td className="text-xs">{cm.registered_at ? formatDateTime(cm.registered_at) : <span className="text-muted">not registered</span>}</td>
                      <td>
                        <div className="flex" style={{ gap: 6 }}>
                          <button className="btn btn-sm" onClick={() => { setSelected(cm); setTab('fiduciaries') }}>Fiduciaries</button>
                          {canManage && (
                            <button className="btn btn-sm" onClick={() => setForm({
                              cm_ref: cm.cm_ref, name: cm.name, tenant_code: cm.tenant_code,
                              board_registration_number: cm.board_registration_number,
                              registration_status: cm.registration_status, contact_email: '', website_url: cm.website_url,
                            })}>Edit</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="card-body">
            <p className="text-xs text-muted">
              Only REGISTERED, active Consent Managers appear on the public transparency disclosure at{' '}
              <span className="mono">/consent-manager/disclosures</span>, which is deliberately unauthenticated — a
              disclosure nobody can read without a credential is not a disclosure.
            </p>
          </div>
        </div>
      )}

      {tab === 'fiduciaries' && (
        <div className="card">
          <div className="card-header">
            <h3>Onboarded fiduciaries{selected ? ` — ${selected.name}` : ''}</h3>
            <div className="flex" style={{ gap: 8 }}>
              <select className="input" style={{ width: 'auto' }} value={selected?.cm_ref || ''}
                onChange={(e) => setSelected(registrations.find((r) => r.cm_ref === e.target.value) || null)}
                aria-label="Consent Manager">
                <option value="">Select a Consent Manager…</option>
                {registrations.map((r) => <option key={r.cm_ref} value={r.cm_ref}>{r.name}</option>)}
              </select>
              {canManage && selected && (
                <button className="btn btn-primary btn-sm" onClick={() => setOnboardForm({
                  source_app: tenants[0]?.code || '', status: 'ACTIVE', allowed_purpose_codes: '', notes: '',
                })}><IconPlus size={13} /> Onboard a fiduciary</button>
              )}
            </div>
          </div>
          <div className="card-body">
            {!selected ? (
              <EmptyState message="Choose a Consent Manager to see which fiduciaries it has been onboarded to." />
            ) : fiduciaries.length === 0 ? (
              <EmptyState message="This Consent Manager has not been onboarded to any fiduciary." />
            ) : null}
          </div>
          {selected && fiduciaries.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Fiduciary (source_app)</th><th>Status</th><th>Allowed purposes</th><th>Onboarded</th><th>Terminated</th><th>Notes</th><th /></tr></thead>
                <tbody>
                  {fiduciaries.map((f) => (
                    <tr key={f.id}>
                      <td className="mono text-xs">{f.source_app}</td>
                      <td><Badge status={f.status === 'ACTIVE' ? 'ACTIVE' : 'EXPIRED'}>{f.status}</Badge></td>
                      <td className="text-xs">
                        {f.allowed_purpose_codes?.length
                          ? f.allowed_purpose_codes.join(', ')
                          : <span className="text-muted">all purposes of this fiduciary</span>}
                      </td>
                      <td className="text-xs">{f.onboarded_at ? formatDateTime(f.onboarded_at) : '—'}</td>
                      <td className="text-xs">{f.terminated_at ? formatDateTime(f.terminated_at) : '—'}</td>
                      <td className="text-sm">{f.notes}</td>
                      <td>
                        {canManage && selected && f.status === 'ACTIVE' && (
                          <button className="btn btn-ghost-danger btn-sm" disabled={busy} onClick={() => {
                            const note = window.prompt(`Why is ${f.source_app} being terminated? (recorded)`)
                            if (note == null) return
                            run(() => consentManagerApi.updateOnboarding(selected.cm_ref, f.source_app, { status: 'TERMINATED', notes: note }),
                              'Onboarding terminated.')
                          }}>Terminate</button>
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

      {tab === 'service' && (
        <div className="card">
          <div className="card-header">
            <h3>Service level</h3>
            <select className="input" style={{ width: 'auto' }} value={windowHours}
              onChange={(e) => setWindowHours(Number(e.target.value))} aria-label="Window">
              {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
            </select>
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              What the Consent Manager APIs actually delivered over the selected window. A percentage with no calls
              behind it is shown as <b>No data yet</b>, never as 100% — an untested endpoint has not proved anything.
            </p>
            {!metrics ? (
              <EmptyState message="Service metrics are unavailable with your permissions." />
            ) : metrics.total_calls === 0 ? (
              <div className="alert alert-info">
                <b>No Consent Manager API call was made in this window.</b> Availability, error rate and latency are
                therefore unknown rather than perfect.
              </div>
            ) : (
              <>
                <dl className="detail-grid mb">
                  <div className="detail-item"><dt>Calls</dt><dd>{metrics.total_calls}</dd></div>
                  <div className="detail-item"><dt>Availability</dt><dd>{formatPct(metrics.availability_pct, 3)}</dd></div>
                  <div className="detail-item"><dt>Error rate</dt><dd>{formatPct(metrics.error_rate_pct, 3)}</dd></div>
                  <div className="detail-item"><dt>Latency p50 / p95 / max</dt>
                    <dd>{metrics.latency_ms_p50 ?? '—'} / {metrics.latency_ms_p95 ?? '—'} / {metrics.latency_ms_max ?? '—'} ms</dd></div>
                  <div className="detail-item"><dt>Record retrieval p95</dt><dd>{metrics.record_retrieval_ms_p95 ?? '—'} ms</dd></div>
                </dl>
                {metrics.per_endpoint.length > 0 && (
                  <>
                    <h4 className="kpi-section-title">Per endpoint</h4>
                    <pre className="code-block" style={{ maxHeight: 320, overflow: 'auto' }}>
                      {JSON.stringify(metrics.per_endpoint, null, 2)}
                    </pre>
                  </>
                )}
              </>
            )}
            <div className="alert alert-info" style={{ marginTop: 16 }}>
              <IconAlert size={15} /> <b>Artefacts are not listed in this console.</b> The artefact APIs authenticate
              with a tenant-bound integration API key rather than a staff token, because an artefact belongs to the
              Consent Manager that brokered it. The counts above come from the metrics endpoint; an artefact table here
              would either be empty (and read as &ldquo;nothing brokered&rdquo;) or require putting an integration key
              in the browser.
            </div>
          </div>
        </div>
      )}

      <FooterRow left={`${registrations.length} Consent Manager(s) on the register`} />

      <Modal open={!!form} title={form?.cm_ref ? 'Edit Consent Manager' : 'Register a Consent Manager'} onClose={() => setForm(null)}
        footer={
          <>
            <button className="btn" onClick={() => setForm(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !form?.name} onClick={async () => {
              if (!form) return
              const body: Record<string, unknown> = {
                name: form.name, board_registration_number: form.board_registration_number,
                registration_status: form.registration_status, website_url: form.website_url,
              }
              if (form.contact_email) body.contact_email = form.contact_email
              if (!form.cm_ref) body.tenant_code = form.tenant_code
              const ok = form.cm_ref
                ? await run(() => consentManagerApi.updateRegistration(form.cm_ref as string, body), 'Consent Manager updated.')
                : await run(() => consentManagerApi.register(body), 'Consent Manager registered.')
              if (ok) setForm(null)
            }}>Save</button>
          </>
        }>
        {form && (
          <>
            <div className="form-group"><label>Name</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="form-row">
              {!form.cm_ref && (
                <div className="form-group"><label>Tenant</label>
                  <select className="select" value={form.tenant_code} onChange={(e) => setForm({ ...form, tenant_code: e.target.value })}>
                    {tenants.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                  </select></div>
              )}
              <div className="form-group"><label>Board registration number</label>
                <input className="input" value={form.board_registration_number}
                  onChange={(e) => setForm({ ...form, board_registration_number: e.target.value })} /></div>
              <div className="form-group"><label>Registration status</label>
                <select className="select" value={form.registration_status} onChange={(e) => setForm({ ...form, registration_status: e.target.value })}>
                  {REGISTRATION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label>Contact email</label>
                <input className="input" type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></div>
              <div className="form-group"><label>Website</label>
                <input className="input" value={form.website_url} onChange={(e) => setForm({ ...form, website_url: e.target.value })} /></div>
            </div>
            <p className="text-xs text-muted">
              Marking a Consent Manager REGISTERED publishes it on the unauthenticated disclosure endpoint. Only do so
              once the Board registration number above is the real one.
            </p>
          </>
        )}
      </Modal>

      <Modal open={!!onboardForm} title="Onboard a fiduciary" onClose={() => setOnboardForm(null)}
        footer={
          <>
            <button className="btn" onClick={() => setOnboardForm(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !onboardForm?.source_app} onClick={async () => {
              if (!onboardForm || !selected) return
              const ok = await run(() => consentManagerApi.onboardFiduciary(selected.cm_ref, {
                source_app: onboardForm.source_app,
                status: onboardForm.status,
                allowed_purpose_codes: onboardForm.allowed_purpose_codes.split(',').map((s) => s.trim()).filter(Boolean),
                notes: onboardForm.notes,
              }), 'Fiduciary onboarded.')
              if (ok) {
                setOnboardForm(null)
                const r = await consentManagerApi.fiduciaries(selected.cm_ref).catch(() => ({ data: [] as FiduciaryOnboarding[] }))
                setFiduciaries(r.data)
              }
            }}>Onboard</button>
          </>
        }>
        {onboardForm && (
          <>
            <div className="form-row">
              <div className="form-group"><label>Fiduciary (source_app)</label>
                <select className="select" value={onboardForm.source_app} onChange={(e) => setOnboardForm({ ...onboardForm, source_app: e.target.value })}>
                  {tenants.map((t) => <option key={t.id} value={t.code}>{t.name} ({t.code})</option>)}
                </select></div>
              <div className="form-group"><label>Status</label>
                <select className="select" value={onboardForm.status} onChange={(e) => setOnboardForm({ ...onboardForm, status: e.target.value })}>
                  {ONBOARDING_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select></div>
            </div>
            <div className="form-group"><label>Allowed purpose codes (comma separated, blank for all)</label>
              <input className="input" value={onboardForm.allowed_purpose_codes}
                onChange={(e) => setOnboardForm({ ...onboardForm, allowed_purpose_codes: e.target.value })} /></div>
            <div className="form-group"><label>Notes</label>
              <input className="input" value={onboardForm.notes} onChange={(e) => setOnboardForm({ ...onboardForm, notes: e.target.value })} /></div>
          </>
        )}
      </Modal>
    </div>
  )
}

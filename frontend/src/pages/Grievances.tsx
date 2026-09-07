/**
 * R2-06: the rights and grievance queues (`/grievances`).
 *
 * s.13 gives every Data Principal a right of grievance redressal and requires
 * the fiduciary to respond within a period it publishes; the tenant's own
 * `grievance_response_days` (capped at 90 by a database constraint) is that
 * period, and every grievance carries the resulting `due_at`. So the queue is
 * organised around the clock rather than around status: overdue first, then
 * due soon, then the rest. An overdue grievance is a contravention in
 * progress, not a backlog item.
 *
 * The tabs are the three principal-initiated queues this console can work:
 *  - Grievances (s.13), the full workflow: assign, escalate, resolve, close.
 *  - Objections, the standing withdrawal-adjacent objection to a purpose.
 *  - Rights requests, which a separate module is building. That tab states
 *    plainly that it is not wired up rather than showing an empty table that
 *    would read as "no requests".
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { grievancesApi, objectionsApi, organizationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox, IconPlus, IconUsers } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Grievance, GrievanceStats, Objection, Organization } from '../types'

type Tab = 'queue' | 'objections' | 'rights'

// Exactly app/models/grievance.py::GRIEVANCE_CATEGORIES — the API validates
// against this pattern, so an invented value here is a 422 the operator cannot
// fix from the form.
const CATEGORIES = [
  'CONSENT_NOT_HONOURED', 'ACCESS_REQUEST', 'CORRECTION_REQUEST', 'ERASURE_REQUEST', 'NOMINATION',
  'UNAUTHORISED_PROCESSING', 'EXCESSIVE_COLLECTION', 'DATA_ACCURACY', 'SECURITY_INCIDENT',
  'NOTICE_UNCLEAR', 'OTHER',
]
/** app/models/grievance.py::GRIEVANCE_CHANNELS. How the grievance reached us. */
const CHANNELS = ['PORTAL', 'STAFF', 'EMAIL', 'PHONE', 'POST', 'OTHER']

function dueTone(g: Grievance): 'danger' | 'warning' | 'ok' {
  if (g.overdue) return 'danger'
  if (g.days_remaining != null && g.days_remaining <= 3) return 'warning'
  return 'ok'
}

export function GrievancesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('grievance.manage')
  const canSeeObjections = hasPermission('consent.view')
  const canResolveObjections = hasPermission('consent.manage')

  const [tab, setTab] = useState<Tab>('queue')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [items, setItems] = useState<Grievance[]>([])
  const [stats, setStats] = useState<GrievanceStats | null>(null)
  const [objections, setObjections] = useState<Objection[]>([])
  const [tenants, setTenants] = useState<Organization[]>([])

  const [filter, setFilter] = useState<{ status: string; category: string; scope: 'all' | 'open' | 'overdue' }>({
    status: '', category: '', scope: 'open',
  })

  const [detail, setDetail] = useState<Grievance | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ customer_external_id: '', category: 'CONSENT_NOT_HONOURED', subject: '', description: '', channel: 'STAFF' })

  const [action, setAction] = useState<null | { kind: 'resolve' | 'escalate' | 'close' | 'assign'; g: Grievance }>(null)
  const [actionText, setActionText] = useState('')

  const load = useCallback(async () => {
    const params: Record<string, unknown> = { limit: 200 }
    if (filter.status) params.status = filter.status
    if (filter.category) params.category = filter.category
    if (filter.scope === 'open') params.open_only = true
    if (filter.scope === 'overdue') params.overdue_only = true
    const [g, s] = await Promise.all([grievancesApi.list(params), grievancesApi.stats()])
    setItems(g.data)
    setStats(s.data)
    if (canSeeObjections) {
      const o = await objectionsApi.list().catch(() => ({ data: [] as Objection[] }))
      setObjections(o.data)
    }
  }, [filter, canSeeObjections])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => { organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([])) }, [])

  const run = async (fn: () => Promise<unknown>, message: string): Promise<boolean> => {
    setBusy(true)
    setRefusal('')
    try {
      await fn()
      toast('success', message)
      await load()
      return true
    } catch (e) {
      setRefusal(getErrorMessage(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  /** Overdue first, then nearest to its statutory due date. */
  const ordered = useMemo(() => [...items].sort((a, b) => {
    if (a.overdue !== b.overdue) return a.overdue ? -1 : 1
    return new Date(a.due_at).getTime() - new Date(b.due_at).getTime()
  }), [items])

  const responseDays = [...new Set(tenants.map((t) => t.grievance_response_days).filter(Boolean))].sort((a, b) => a - b)
  const responsePeriodLabel = responseDays.length === 0
    ? 'past the published response period'
    : responseDays.length === 1
      ? `published response period ${responseDays[0]} days`
      : `published response periods ${responseDays[0]}–${responseDays[responseDays.length - 1]} days`

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Rights & grievances"
        subtitle="The s.13 grievance queue and the principal-initiated rights queues, ordered by how close each one is to its published response deadline"
        actions={canManage && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}><IconPlus size={14} /> Log a grievance</button>
        )}
      />

      {!canManage && <ReadOnlyBanner permission="grievance.manage" what="Assigning, escalating, resolving and closing a grievance" />}

      <RefusalNotice title="The grievance API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      {stats && (
        <div className="metric-grid mb">
          <MetricCard label="Open" value={stats.open} tone={stats.open ? 'primary' : 'success'} icon={<IconInbox size={20} />}
            sub={`${stats.total} received in total`} />
          <MetricCard label="Overdue" value={stats.overdue} tone={stats.overdue ? 'danger' : 'success'} icon={<IconAlert size={20} />}
            sub={responsePeriodLabel} />
          <MetricCard label="Escalated" value={stats.escalated} tone={stats.escalated ? 'warning' : 'slate'} icon={<IconUsers size={20} />}
            sub="with the DPO as the s.10(2)(a) point of contact" />
          <MetricCard
            label="On-time closure"
            value={formatPct(stats.on_time_closure_rate)}
            tone={stats.on_time_closure_rate == null ? 'slate' : stats.on_time_closure_rate >= 95 ? 'success' : 'warning'}
            icon={<IconCheck size={20} />}
            sub={stats.on_time_closure_rate == null
              ? 'nothing resolved yet — unknown, not zero'
              : `${stats.resolved_total} resolved · avg ${stats.average_days_to_resolution?.toFixed(1) ?? '—'} days`}
          />
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'queue', label: 'Grievances', count: items.length },
          { id: 'objections', label: 'Objections', count: canSeeObjections ? objections.length : null },
          { id: 'rights', label: 'Rights requests' },
        ]}
      />

      {tab === 'queue' && (
        <>
          <div className="card mb">
            <div className="card-body">
              <div className="filter-chips">
                {(['open', 'overdue', 'all'] as const).map((s) => (
                  <button key={s} type="button" className={`filter-chip${filter.scope === s ? ' active' : ''}`}
                    aria-pressed={filter.scope === s} onClick={() => setFilter({ ...filter, scope: s })}>
                    {s === 'open' ? 'Open only' : s === 'overdue' ? 'Overdue only' : 'All'}
                  </button>
                ))}
                <select className="input" style={{ width: 'auto' }} value={filter.category}
                  onChange={(e) => setFilter({ ...filter, category: e.target.value })} aria-label="Category">
                  <option value="">All categories</option>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
                </select>
                <select className="input" style={{ width: 'auto' }} value={filter.status}
                  onChange={(e) => setFilter({ ...filter, status: e.target.value })} aria-label="Status">
                  <option value="">All statuses</option>
                  {['RECEIVED', 'ACKNOWLEDGED', 'IN_PROGRESS', 'ESCALATED', 'RESOLVED', 'CLOSED'].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
          </div>

          {ordered.length === 0 ? (
            <EmptyState icon={<IconInbox size={30} />} title="Nothing in this queue"
              message="No grievance matches this filter." />
          ) : (
            <div className="card">
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Reference</th><th>Principal</th><th>Category</th><th>Subject</th><th>Status</th><th>Due</th><th>Assigned</th><th /></tr>
                  </thead>
                  <tbody>
                    {ordered.map((g) => (
                      <tr key={g.reference_no}>
                        <td className="mono text-xs">{g.reference_no}<div className="text-muted">{formatDateTime(g.received_at)}</div></td>
                        <td className="text-xs">{g.customer_external_id || `#${g.customer_id}`}<div className="text-muted mono">{g.source_app}</div></td>
                        <td className="text-xs">{g.category.replace(/_/g, ' ')}</td>
                        <td>{g.subject || <span className="text-muted">—</span>}</td>
                        <td><Badge status={g.status === 'CLOSED' || g.status === 'RESOLVED' ? 'ACTIVE' : g.status === 'ESCALATED' ? 'DENIED' : 'PENDING'}>{g.status}</Badge></td>
                        <td>
                          <span className={`due-pill due-${dueTone(g)}`}>
                            {g.overdue
                              ? `Overdue by ${Math.abs(g.days_remaining ?? 0)} d`
                              : `${g.days_remaining ?? '—'} d left`}
                          </span>
                          <div className="text-xs text-muted">{formatDateTime(g.due_at)} · {g.response_days} d period</div>
                        </td>
                        <td className="text-xs">{g.assigned_to || <span className="text-muted">unassigned</span>}</td>
                        <td><button className="btn btn-sm" onClick={() => setDetail(g)}>Open</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {tab === 'objections' && (
        !canSeeObjections ? (
          <ReadOnlyBanner permission="consent.view" what="Reading the objection queue" />
        ) : objections.length === 0 ? (
          <EmptyState icon={<IconInbox size={30} />} title="No objections"
            message="No principal has objected to a purpose. An objection is separate from a withdrawal: it asks the fiduciary to stop processing for a named purpose and records the reason." />
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Principal</th><th>Purpose</th><th>Reason</th><th>Status</th><th>Objected</th><th>Resolved</th><th /></tr></thead>
                <tbody>
                  {objections.map((o) => (
                    <tr key={o.id}>
                      <td>#{o.customer_id}<div className="text-xs text-muted mono">{o.source_app}</div></td>
                      <td className="mono text-xs">{o.purpose_code}</td>
                      <td>{o.reason || <span className="text-muted">—</span>}</td>
                      <td><Badge status={o.status === 'RESOLVED' ? 'ACTIVE' : 'PENDING'}>{o.status}</Badge></td>
                      <td className="text-xs">{formatDateTime(o.objected_at)}</td>
                      <td className="text-xs">
                        {o.resolved_at ? <>{formatDateTime(o.resolved_at)}<div className="text-muted">{o.resolved_by}</div></> : '—'}
                      </td>
                      <td>
                        {canResolveObjections && o.status !== 'RESOLVED' && (
                          <button className="btn btn-sm" disabled={busy} onClick={() => {
                            const note = window.prompt('Resolution note for this objection')
                            if (note == null) return
                            run(() => objectionsApi.resolve(o.id, { resolution_note: note }), 'Objection resolved.')
                          }}>Resolve</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      )}

      {tab === 'rights' && (
        <div className="card">
          <div className="card-header"><h3>Rights requests (s.11–s.13)</h3></div>
          <div className="card-body">
            {/*
              Deliberately not an empty table. A table with no rows reads as
              "no requests have been made", which would be a false compliance
              statement; this build has no rights-request endpoint to query.
            */}
            <div className="alert alert-info">
              <b>Not wired up in this build.</b> No rights-request endpoint is mounted on this backend — the module is
              being built separately. Its permissions have already landed in <span className="mono">rbac.py</span> (
              <span className="mono">rights.view</span> / <span className="mono">rights.manage</span>), so this tab can
              be gated on them the moment the router appears. Until then an empty table here would say &ldquo;no
              principal has exercised a right&rdquo;, which is not something this console can currently know.
            </div>
            <p className="text-sm text-secondary">
              The two adjacent rights that <i>are</i> live are already worked from this console:
            </p>
            <ul className="danger-list">
              <li><b>s.12(3) erasure</b> — raised, authorised and executed on <a href="/erasure">Retention &amp; erasure</a>, with its pre-erasure notice and legal-hold checks.</li>
              <li><b>s.13 grievance redressal</b> — the queue on the first tab of this page.</li>
              <li><b>Objection to a purpose</b> — the second tab.</li>
            </ul>
          </div>
        </div>
      )}

      <FooterRow left={stats ? `${stats.total} grievances received · ${stats.overdue} past their published response period` : ''} />

      {/* ------------------------------ Detail ------------------------------ */}
      <Modal open={!!detail} wide title={detail ? `Grievance ${detail.reference_no}` : ''} onClose={() => setDetail(null)}>
        {detail && (
          <>
            {detail.overdue && (
              <div className="alert alert-error mb">
                <b>Past the published response period.</b> This grievance was due on {formatDateTime(detail.due_at)},
                {' '}{Math.abs(detail.days_remaining ?? 0)} day(s) ago. The response period is the tenant&rsquo;s own
                published figure ({detail.response_days} days) and s.13 requires it to be met.
              </div>
            )}
            <dl className="detail-grid mb">
              <div className="detail-item"><dt>Status</dt><dd>{detail.status}</dd></div>
              <div className="detail-item"><dt>Category</dt><dd>{detail.category}</dd></div>
              <div className="detail-item"><dt>Channel</dt><dd>{detail.channel}</dd></div>
              <div className="detail-item"><dt>Principal</dt><dd>{detail.customer_external_id || `#${detail.customer_id}`}</dd></div>
              <div className="detail-item"><dt>Received</dt><dd>{formatDateTime(detail.received_at)}</dd></div>
              <div className="detail-item"><dt>Acknowledged</dt><dd>{formatDateTime(detail.acknowledged_at)}</dd></div>
              <div className="detail-item"><dt>Due</dt><dd>{formatDateTime(detail.due_at)} ({detail.response_days} day period)</dd></div>
              <div className="detail-item"><dt>Assigned to</dt><dd>{detail.assigned_to || '—'}</dd></div>
              {detail.escalated_at && (
                <div className="detail-item"><dt>Escalated</dt><dd>{formatDateTime(detail.escalated_at)} to {detail.escalated_to}</dd></div>
              )}
              {detail.resolved_at && (
                <div className="detail-item"><dt>Resolved</dt><dd>{formatDateTime(detail.resolved_at)} by {detail.resolved_by}</dd></div>
              )}
              {detail.feedback_rating != null && (
                <div className="detail-item"><dt>Principal feedback</dt><dd>{detail.feedback_rating}/5 — {detail.feedback_comment || 'no comment'}</dd></div>
              )}
            </dl>

            <h4 className="kpi-section-title">{detail.subject || 'Grievance'}</h4>
            <p className="text-sm text-secondary" style={{ whiteSpace: 'pre-wrap' }}>{detail.description}</p>

            {detail.resolution_summary && (
              <>
                <h4 className="kpi-section-title">Resolution</h4>
                <div className="quote">{detail.resolution_summary}</div>
              </>
            )}

            {canManage && (
              <div className="flex" style={{ gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
                <button className="btn btn-sm" disabled={busy} onClick={() => { setActionText(detail.assigned_to || ''); setAction({ kind: 'assign', g: detail }) }}>Assign</button>
                {!['RESOLVED', 'CLOSED'].includes(detail.status) && (
                  <button className="btn btn-warning btn-sm" disabled={busy} onClick={() => { setActionText(''); setAction({ kind: 'escalate', g: detail }) }}>Escalate to the DPO</button>
                )}
                {!['RESOLVED', 'CLOSED'].includes(detail.status) && (
                  <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => { setActionText(''); setAction({ kind: 'resolve', g: detail }) }}>Resolve</button>
                )}
                {detail.status === 'RESOLVED' && (
                  <button className="btn btn-sm" disabled={busy} onClick={() => { setActionText(''); setAction({ kind: 'close', g: detail }) }}>Close</button>
                )}
              </div>
            )}

            {detail.events?.length > 0 && (
              <>
                <h4 className="kpi-section-title">History</h4>
                <div className="timeline">
                  {detail.events.map((e) => (
                    <div className="timeline-item" key={e.id}>
                      <div style={{ fontWeight: 600 }}>{e.event.replace(/_/g, ' ')}</div>
                      <div className="text-xs text-muted">
                        {formatDateTime(e.created_at)} · {e.actor_username || 'system'}
                        {e.from_status && e.to_status && ` · ${e.from_status} → ${e.to_status}`}
                      </div>
                      {e.note && <div className="text-sm text-secondary">{e.note}</div>}
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </Modal>

      {/* ------------------------------ Actions ------------------------------ */}
      <Modal
        open={!!action}
        title={action ? {
          assign: 'Assign this grievance',
          escalate: 'Escalate to the DPO',
          resolve: 'Record the resolution',
          close: 'Close this grievance',
        }[action.kind] : ''}
        onClose={() => setAction(null)}
        footer={
          <>
            <button className="btn" onClick={() => setAction(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || (action?.kind === 'resolve' && !actionText.trim())} onClick={async () => {
              if (!action) return
              const ref = action.g.reference_no
              const fns = {
                assign: () => grievancesApi.update(ref, { assigned_to: actionText || null }),
                escalate: () => grievancesApi.escalate(ref, { reason: actionText }),
                resolve: () => grievancesApi.resolve(ref, { resolution_summary: actionText }),
                close: () => grievancesApi.close(ref, { note: actionText }),
              }
              const ok = await run(fns[action.kind], `Grievance ${ref} updated.`)
              if (ok) { setAction(null); setDetail(null) }
            }}>Confirm</button>
          </>
        }
      >
        {action?.kind === 'escalate' && (
          <p className="text-sm text-secondary">
            Escalation moves this grievance to the Data Protection Officer, who under s.10(2)(a) is the fiduciary&rsquo;s
            point of contact for grievance redressal. The published response deadline does not move.
          </p>
        )}
        {action?.kind === 'resolve' && (
          <p className="text-sm text-secondary">
            The resolution summary is shown to the principal. It is the fiduciary&rsquo;s answer to the grievance, so
            write it for them rather than for the queue.
          </p>
        )}
        <div className="form-group">
          <label>{action?.kind === 'assign' ? 'Assign to (username, blank to unassign)' : action?.kind === 'resolve' ? 'Resolution summary' : 'Note'}</label>
          {action?.kind === 'assign'
            ? <input className="input" value={actionText} onChange={(e) => setActionText(e.target.value)} />
            : <textarea className="textarea" rows={4} value={actionText} onChange={(e) => setActionText(e.target.value)} />}
        </div>
      </Modal>

      {/* ------------------------------ Create ------------------------------ */}
      <Modal open={creating} title="Log a grievance received off-platform" onClose={() => setCreating(false)}
        footer={
          <>
            <button className="btn" onClick={() => setCreating(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !form.customer_external_id || !form.description} onClick={async () => {
              const ok = await run(() => grievancesApi.create({
                customer_external_id: form.customer_external_id,
                category: form.category,
                subject: form.subject,
                description: form.description,
                channel: form.channel,
              }), 'Grievance logged and acknowledged.')
              if (ok) { setCreating(false); setForm({ customer_external_id: '', category: 'CONSENT_NOT_HONOURED', subject: '', description: '', channel: 'STAFF' }) }
            }}>Log grievance</button>
          </>
        }>
        <div className="alert alert-info mb">
          Logging starts the published response clock from now. If the grievance actually arrived earlier, that earlier
          time is the one s.13 measures from — log it promptly rather than in a batch.
        </div>
        <div className="form-row">
          <div className="form-group"><label>Principal external id</label>
            <input className="input" value={form.customer_external_id} onChange={(e) => setForm({ ...form, customer_external_id: e.target.value })} /></div>
          <div className="form-group"><label>Category</label>
            <select className="select" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select></div>
          <div className="form-group"><label>Channel</label>
            <select className="select" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select></div>
        </div>
        <div className="form-group"><label>Subject</label>
          <input className="input" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></div>
        <div className="form-group"><label>Description (in the principal&rsquo;s own words where possible)</label>
          <textarea className="textarea" rows={5} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
      </Modal>
    </div>
  )
}

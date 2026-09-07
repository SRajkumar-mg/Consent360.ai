/**
 * R1-06 / R1-10: the retention and erasure screen (`/erasure`, `/retention`).
 *
 * This page can destroy a named human being's personal data. Two design rules
 * follow from that and neither is negotiable:
 *
 *  1. **No single unguarded click.** Executing an erasure and releasing a legal
 *     hold both go through a typed confirmation naming the exact record. A hold
 *     release is treated as exactly as consequential as the erasure it unblocks,
 *     because that is what it is.
 *
 *  2. **The engine's refusal is the most useful thing on the screen.** Execution
 *     is refused with a *named rule* — "no pre-erasure notice on record", "the
 *     48-hour notice period has not elapsed", "legal hold LH-… is in force". That
 *     sentence tells the operator which precondition failed and therefore what to
 *     do next. It is rendered verbatim and stays on screen; it is never replaced
 *     by a generic error, and a refused call never leaves the UI looking as
 *     though anything happened.
 *
 * The two retention surfaces here are genuinely different and are kept apart:
 * an **erasure retention policy** (`/erasure/policies`) decides when one
 * principal's data becomes due for erasure, while the **record-class schedule**
 * (`/retention/schedule`) sets how long a *class* of record is kept and is
 * floored by statute. The floor wins over the ceiling, and the schedule tab
 * says so per class.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { erasureApi, organizationsApi, retentionApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, TypedConfirm, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox, IconPlus, IconTrash } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type {
  ErasureJob, ErasureMetrics, ErasureScanResult, LegalHold, Organization, RetentionPolicy,
  RetentionScheduleRow,
} from '../types'

type Tab = 'jobs' | 'holds' | 'policies' | 'schedule' | 'scans'

const RECORD_CLASSES = ['principal_personal_data', 'directory_record', 'consent_contexts', 'notifications'] as const

function jobTone(status: string): 'ACTIVE' | 'PENDING' | 'DENIED' | 'EXPIRED' | 'REQUESTED' {
  if (status === 'EXECUTED') return 'ACTIVE'
  if (status === 'BLOCKED' || status === 'FAILED') return 'DENIED'
  if (status === 'CANCELLED') return 'EXPIRED'
  if (status === 'NOTIFIED') return 'REQUESTED'
  return 'PENDING'
}

export function ErasurePage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('erasure.manage')
  const canManageSchedule = hasPermission('policy.manage')

  const [tab, setTab] = useState<Tab>('jobs')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [jobs, setJobs] = useState<ErasureJob[]>([])
  const [holds, setHolds] = useState<LegalHold[]>([])
  const [policies, setPolicies] = useState<RetentionPolicy[]>([])
  const [schedule, setSchedule] = useState<RetentionScheduleRow[]>([])
  const [metrics, setMetrics] = useState<ErasureMetrics | null>(null)
  const [tenants, setTenants] = useState<Organization[]>([])
  const [jobStatus, setJobStatus] = useState('')

  const [raising, setRaising] = useState(false)
  const [raiseForm, setRaiseForm] = useState({ customer_external_id: '', trigger: 'MANUAL' as 'MANUAL' | 'RIGHTS_REQUEST', request_ref: '', reason: '', authorisation_basis: '' })

  const [placing, setPlacing] = useState(false)
  const [holdForm, setHoldForm] = useState({ legal_basis: '', reason: '', customer_external_id: '', record_class: '', tenant_code: '', expires_at: '' })

  const [policyForm, setPolicyForm] = useState<null | {
    record_class: string; scope: string; retention_days: string; inactivity_days: string
    pre_erasure_notice_hours: string; action: 'ERASE' | 'ANONYMISE'; legal_basis_for_retention: string
    is_active: boolean; notes: string
  }>(null)

  const [confirm, setConfirm] = useState<null | { kind: 'execute'; job: ErasureJob } | { kind: 'release'; hold: LegalHold }>(null)
  const [releaseReason, setReleaseReason] = useState('')
  const [scanResult, setScanResult] = useState<null | { kind: string; propose: boolean; result: ErasureScanResult }>(null)

  const load = useCallback(async () => {
    const [j, h, p, s, m] = await Promise.all([
      erasureApi.jobs(jobStatus ? { status: jobStatus, limit: 200 } : { limit: 200 }),
      erasureApi.holds(),
      erasureApi.policies(),
      retentionApi.schedule().catch(() => ({ data: [] as RetentionScheduleRow[] })),
      erasureApi.metrics(),
    ])
    setJobs(j.data); setHolds(h.data); setPolicies(p.data); setSchedule(s.data); setMetrics(m.data)
  }, [jobStatus])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => { organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([])) }, [])

  /** Every mutating call routes through here so a refusal is never mistaken
   *  for a success and its text is never rewritten. */
  const run = async (fn: () => Promise<unknown>, successMessage: string): Promise<boolean> => {
    setBusy(true)
    setRefusal('')
    try {
      await fn()
      toast('success', successMessage)
      await load()
      return true
    } catch (e) {
      setRefusal(getErrorMessage(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  const activeHolds = useMemo(() => holds.filter((h) => h.is_active), [holds])
  const blockedJobs = useMemo(() => jobs.filter((j) => j.status === 'BLOCKED'), [jobs])

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Retention & erasure"
        subtitle="Erasure jobs and their preconditions, legal holds, the per-principal retention policies and the statutory record-class floors"
      />

      {!canManage && <ReadOnlyBanner permission="erasure.manage" what="Raising, authorising and executing an erasure, and placing or releasing a legal hold" />}

      {/*
        The refusal lives at the top of the page rather than beside one button,
        because the same engine refuses from four different controls and the
        operator needs the sentence whichever one they pressed.
      */}
      <RefusalNotice title="The erasure engine refused this. Nothing was destroyed." detail={refusal} onDismiss={() => setRefusal('')} />

      {metrics && (
        <div className="metric-grid mb">
          <MetricCard label="Erasure backlog" value={metrics.erasure_backlog} tone={metrics.erasure_backlog ? 'warning' : 'success'}
            icon={<IconInbox size={20} />} sub={Object.entries(metrics.backlog_by_status).map(([k, v]) => `${k} ${v}`).join(' · ') || 'nothing outstanding'} />
          <MetricCard label="Executed" value={metrics.jobs_executed} tone="primary" icon={<IconCheck size={20} />}
            sub={metrics.median_tat_hours == null ? 'no median turnaround yet' : `median ${metrics.median_tat_hours.toFixed(1)} h turnaround`} />
          {/* Nothing executed means the compliance rate is unknown, not 0%. */}
          <MetricCard label="Notice compliance" value={formatPct(metrics.notice_compliance_pct)}
            tone={metrics.notice_compliance_pct == null ? 'slate' : metrics.notice_compliance_pct >= 100 ? 'success' : 'warning'}
            icon={<IconClock size={20} />}
            sub={metrics.jobs_executed === 0
              ? 'no erasure has been executed yet — nothing to measure'
              : `${metrics.executed_with_compliant_notice} of ${metrics.jobs_executed} executed with a compliant pre-erasure notice`} />
          <MetricCard label="Legal holds in force" value={metrics.legal_holds_active} tone={metrics.legal_holds_active ? 'info' : 'slate'}
            icon={<IconAlert size={20} />} sub="each one blocks an erasure outright" />
        </div>
      )}

      {blockedJobs.length > 0 && (
        <div className="alert alert-error mb">
          <b>{blockedJobs.length} erasure job{blockedJobs.length === 1 ? ' is' : 's are'} blocked.</b> A blocked job
          carries the engine&rsquo;s own reason on the row below — read it before re-trying, because re-trying without
          removing the cause produces the same refusal.
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'jobs', label: 'Erasure jobs', count: jobs.length },
          { id: 'holds', label: 'Legal holds', count: activeHolds.length },
          { id: 'policies', label: 'Retention policies', count: policies.length },
          { id: 'schedule', label: 'Record-class floors', count: schedule.length },
          { id: 'scans', label: 'Scans' },
        ]}
      />

      {/* ------------------------------ Jobs ------------------------------ */}
      {tab === 'jobs' && (
        <div className="card">
          <div className="card-header">
            <h3>Erasure jobs</h3>
            <div className="flex" style={{ gap: 8 }}>
              <select className="input" style={{ width: 'auto' }} value={jobStatus} onChange={(e) => setJobStatus(e.target.value)} aria-label="Job status">
                <option value="">All statuses</option>
                {['PROPOSED', 'SCHEDULED', 'NOTIFIED', 'BLOCKED', 'EXECUTED', 'CANCELLED', 'FAILED'].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              {canManage && <button className="btn btn-primary btn-sm" onClick={() => setRaising(true)}><IconPlus size={13} /> Raise an erasure</button>}
            </div>
          </div>
          {jobs.length === 0 ? (
            <div className="card-body"><EmptyState message="No erasure jobs. A scan proposes them, a rights request raises one, or you can raise one by hand." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Job</th><th>Principal</th><th>Trigger</th><th>Action</th><th>Status</th><th>Notice</th><th>Executable after</th><th /></tr>
                </thead>
                <tbody>
                  {jobs.map((j) => {
                    const noticeDue = j.execute_after ? new Date(j.execute_after).getTime() > Date.now() : false
                    return (
                      <tr key={j.job_ref}>
                        <td>
                          <div className="mono text-xs">{j.job_ref}</div>
                          <div className="text-xs text-muted">{formatDateTime(j.created_at)}</div>
                        </td>
                        <td>#{j.customer_id}<div className="text-xs text-muted mono">{j.source_app}</div></td>
                        <td>{j.trigger}<div className="text-xs text-muted mono">{j.trigger_ref}</div></td>
                        <td>{j.action}</td>
                        <td>
                          <Badge status={jobTone(j.status)}>{j.status}</Badge>
                          {j.blocked_reason && (
                            /* The engine's own words about why this job is blocked. */
                            <div className="job-blocked">{j.blocked_reason}</div>
                          )}
                          {j.error && <div className="job-blocked">{j.error}</div>}
                        </td>
                        <td>
                          {!j.notice_required
                            ? <span className="text-muted text-xs">not required</span>
                            : j.notice_sent_at
                              ? <span className="text-xs">sent {formatDateTime(j.notice_sent_at)}</span>
                              : <span className="text-xs text-danger">not sent ({j.notice_hours} h required)</span>}
                        </td>
                        <td className="text-xs">
                          {j.executed_at
                            ? `executed ${formatDateTime(j.executed_at)}`
                            : j.execute_after
                              ? `${formatDateTime(j.execute_after)}${noticeDue ? ' (not yet)' : ''}`
                              : '—'}
                        </td>
                        <td>
                          {canManage && (
                            <div className="flex" style={{ gap: 6, flexWrap: 'wrap' }}>
                              {j.status === 'PROPOSED' && (
                                <button className="btn btn-sm" disabled={busy} onClick={() => {
                                  const basis = window.prompt('Authorisation basis — the statutory ground for erasing this principal')
                                  if (!basis) return
                                  run(() => erasureApi.authorise(j.job_ref, { basis }), `Job ${j.job_ref} authorised.`)
                                }}>Authorise</button>
                              )}
                              {(j.status === 'SCHEDULED' || j.status === 'BLOCKED') && j.notice_required && !j.notice_sent_at && (
                                <button className="btn btn-sm" disabled={busy}
                                  onClick={() => run(() => erasureApi.sendNotice(j.job_ref), `Pre-erasure notice sent for ${j.job_ref}.`)}>
                                  Send {j.notice_hours}h notice
                                </button>
                              )}
                              {['SCHEDULED', 'NOTIFIED', 'BLOCKED'].includes(j.status) && (
                                <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => setConfirm({ kind: 'execute', job: j })}>
                                  <IconTrash size={13} /> Execute
                                </button>
                              )}
                              {!['EXECUTED', 'CANCELLED'].includes(j.status) && (
                                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => {
                                  const reason = window.prompt('Why is this erasure being cancelled?')
                                  if (!reason) return
                                  run(() => erasureApi.cancel(j.job_ref, { reason }), `Job ${j.job_ref} cancelled.`)
                                }}>Cancel</button>
                              )}
                              {j.status === 'EXECUTED' && (
                                <button className="btn btn-sm" onClick={() => {
                                  toast('info', `Erased: ${JSON.stringify(j.records_erased)}. Retained: ${JSON.stringify(j.records_retained)}`)
                                }}>Outcome</button>
                              )}
                            </div>
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

      {/* ------------------------------ Holds ------------------------------ */}
      {tab === 'holds' && (
        <div className="card">
          <div className="card-header">
            <h3>Legal holds</h3>
            {canManage && <button className="btn btn-primary btn-sm" onClick={() => setPlacing(true)}><IconPlus size={13} /> Place a hold</button>}
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              A hold outranks both the retention ceiling and the principal&rsquo;s own s.12(3) request — s.8(7) permits
              retention that is necessary for compliance with a law in force. It <b>blocks</b> an erasure rather than
              delaying it, and a blocked job names the hold that blocked it. Releasing a hold un-blocks a statutory
              erasure, which is why releasing one is guarded exactly as heavily as executing one.
            </p>
          </div>
          {holds.length === 0 ? (
            <div className="card-body"><EmptyState message="No legal hold has ever been placed." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Hold</th><th>Scope</th><th>Legal basis</th><th>Placed</th><th>Expires</th><th>State</th><th /></tr></thead>
                <tbody>
                  {holds.map((h) => (
                    <tr key={h.hold_ref}>
                      <td className="mono text-xs">{h.hold_ref}</td>
                      <td>
                        {h.customer_id ? `Principal #${h.customer_id}` : h.record_class ? `Class ${h.record_class}` : 'Tenant-wide'}
                        {h.reason && <div className="text-xs text-muted">{h.reason}</div>}
                      </td>
                      <td className="text-sm">{h.legal_basis}</td>
                      <td className="text-xs">{formatDateTime(h.placed_at)}<div className="text-muted">by {h.placed_by}</div></td>
                      <td className="text-xs">{h.expires_at ? formatDateTime(h.expires_at) : 'no expiry'}</td>
                      <td>
                        <Badge status={h.is_active ? 'ACTIVE' : 'EXPIRED'}>{h.is_active ? 'In force' : 'Released'}</Badge>
                        {!h.is_active && h.released_at && (
                          <div className="text-xs text-muted">{formatDateTime(h.released_at)} by {h.released_by}</div>
                        )}
                      </td>
                      <td>
                        {canManage && h.is_active && (
                          <button className="btn btn-danger btn-sm" onClick={() => { setReleaseReason(''); setConfirm({ kind: 'release', hold: h }) }}>
                            Release
                          </button>
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

      {/* ---------------------------- Policies ---------------------------- */}
      {tab === 'policies' && (
        <div className="card">
          <div className="card-header">
            <h3>Erasure retention policies</h3>
            {canManage && (
              <button className="btn btn-primary btn-sm" onClick={() => setPolicyForm({
                record_class: RECORD_CLASSES[0], scope: '*', retention_days: '', inactivity_days: '',
                pre_erasure_notice_hours: '48', action: 'ERASE', legal_basis_for_retention: '', is_active: true, notes: '',
              })}><IconPlus size={13} /> New / update policy</button>
            )}
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              These decide when one principal&rsquo;s data becomes due for erasure. They are not the record-class
              floors: a policy that would erase inside a statutory floor is overruled by the floor, and the job
              records what it retained and why.
            </p>
          </div>
          {policies.length === 0 ? (
            <div className="card-body"><EmptyState message="No erasure policy configured." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Policy</th><th>Record class</th><th>Retention</th><th>Inactivity</th><th>Notice</th><th>Action</th><th>Floor</th><th>State</th><th /></tr></thead>
                <tbody>
                  {policies.map((p) => (
                    <tr key={p.policy_ref}>
                      <td className="mono text-xs">{p.policy_ref}<div className="text-muted">{p.scope}</div></td>
                      <td>{p.record_class}</td>
                      <td>{p.retention_days == null ? '—' : `${p.retention_days} d`}</td>
                      <td>{p.inactivity_days == null ? '—' : `${p.inactivity_days} d`}</td>
                      <td>{p.pre_erasure_notice_hours} h</td>
                      <td>{p.action}</td>
                      <td>
                        {p.floor_days == null ? <span className="text-muted">none</span> : `${p.floor_days} d`}
                        {!p.hard_delete_permitted && <div className="text-xs text-muted">hard delete not permitted</div>}
                      </td>
                      <td><Badge status={p.is_active ? 'ACTIVE' : 'EXPIRED'}>{p.is_active ? 'Active' : 'Inactive'}</Badge></td>
                      <td>
                        {canManage && (
                          <button className="btn btn-sm" onClick={() => setPolicyForm({
                            record_class: p.record_class, scope: p.scope,
                            retention_days: p.retention_days == null ? '' : String(p.retention_days),
                            inactivity_days: p.inactivity_days == null ? '' : String(p.inactivity_days),
                            pre_erasure_notice_hours: String(p.pre_erasure_notice_hours),
                            action: p.action, legal_basis_for_retention: p.legal_basis_for_retention,
                            is_active: p.is_active, notes: p.notes,
                          })}>Edit</button>
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

      {/* ---------------------------- Schedule ---------------------------- */}
      {tab === 'schedule' && (
        <div className="card">
          <div className="card-header"><h3>Record-class retention floors</h3></div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              A <b>floor</b> is the minimum the law requires this class to be kept for; a <b>configured retention</b> is
              how long this deployment keeps it. The floor always wins. A class marked not deletable is retained and
              superseded by design — the reason is given per row.
            </p>
          </div>
          {schedule.length === 0 ? (
            <div className="card-body"><EmptyState message="The retention schedule is empty or unreadable with your permissions." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Record class</th><th>Storage</th><th>Floor</th><th>Configured</th><th>Meets floor</th><th>Deletable</th><th>Basis</th><th /></tr></thead>
                <tbody>
                  {schedule.map((r) => (
                    <tr key={r.record_class}>
                      <td><b>{r.label}</b><div className="text-xs text-muted mono">{r.record_class}</div></td>
                      <td className="text-xs">{r.storage}{r.table ? ` · ${r.table}` : ''}</td>
                      <td>{r.effective_floor_days} d{r.effective_floor_days !== r.floor_days && <div className="text-xs text-muted">base {r.floor_days} d</div>}</td>
                      <td>{r.configured_retention_days} d</td>
                      <td>{r.schedule_meets_floor
                        ? <span className="text-success">Yes</span>
                        : <span className="text-danger">No — the configured period is below the statutory floor</span>}</td>
                      <td>{r.deletable ? 'Yes' : <span title={r.undeletable_reason}>No</span>}</td>
                      <td className="text-xs text-secondary">{r.basis}</td>
                      <td>
                        {canManageSchedule && (
                          <button className="btn btn-sm" onClick={() => {
                            const v = window.prompt(`Retention days for ${r.label} (floor is ${r.effective_floor_days})`, String(r.configured_retention_days))
                            if (!v) return
                            run(() => retentionApi.updateSchedule(r.record_class, { retention_days: Number(v) }),
                              `${r.label} retention updated.`)
                          }}>Edit</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {schedule.some((r) => !r.deletable) && (
            <div className="card-body">
              <h4 className="kpi-section-title">Why some classes are never deleted</h4>
              {schedule.filter((r) => !r.deletable).map((r) => (
                <div className="quote" key={r.record_class}>
                  <b>{r.label}.</b> {r.undeletable_reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ------------------------------ Scans ------------------------------ */}
      {tab === 'scans' && (
        <div className="card">
          <div className="card-header"><h3>Scans</h3></div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              A scan finds principals whose data is due for erasure. <b>Dry run</b> counts them and proposes nothing.
              <b> Propose</b> raises PROPOSED jobs — which still need authorising, a notice and an execution before
              anything is destroyed. A scan never erases.
            </p>
            <div className="flex" style={{ gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
              {(['retention', 'inactivity'] as const).map((kind) => (
                <div key={kind} className="scan-block">
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>
                    {kind === 'retention' ? 'Retention scan' : 'Inactivity scan'}
                  </div>
                  <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
                    {kind === 'retention'
                      ? 'Data past its policy retention period.'
                      : 'Principals inactive beyond the configured inactivity period.'}
                  </div>
                  <div className="flex" style={{ gap: 6 }}>
                    <button className="btn btn-sm" disabled={!canManage || busy} onClick={async () => {
                      setBusy(true); setRefusal('')
                      try {
                        const r = kind === 'retention' ? await erasureApi.scanRetention(false) : await erasureApi.scanInactivity(false)
                        setScanResult({ kind, propose: false, result: r.data })
                      } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
                    }}>Dry run</button>
                    <button className="btn btn-warning btn-sm" disabled={!canManage || busy} onClick={async () => {
                      setBusy(true); setRefusal('')
                      try {
                        const r = kind === 'retention' ? await erasureApi.scanRetention(true) : await erasureApi.scanInactivity(true)
                        setScanResult({ kind, propose: true, result: r.data })
                        await load()
                      } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
                    }}>Propose jobs</button>
                  </div>
                </div>
              ))}
            </div>
            {scanResult && (
              <div className="alert alert-info" style={{ marginTop: 16 }}>
                <b>{scanResult.kind === 'retention' ? 'Retention' : 'Inactivity'} scan {scanResult.propose ? '(proposing)' : '(dry run)'}:</b>{' '}
                {scanResult.result.policies} polic{scanResult.result.policies === 1 ? 'y' : 'ies'} evaluated,{' '}
                {scanResult.result.candidates} candidate{scanResult.result.candidates === 1 ? '' : 's'},{' '}
                {scanResult.result.jobs_proposed} job{scanResult.result.jobs_proposed === 1 ? '' : 's'} proposed
                {scanResult.result.inactivity_clock_breaches != null && <> · {scanResult.result.inactivity_clock_breaches} inactivity clock breach(es)</>}.
                {!scanResult.propose && ' Nothing was created.'}
              </div>
            )}
          </div>
        </div>
      )}

      <FooterRow left={`${jobs.length} job${jobs.length === 1 ? '' : 's'} · ${activeHolds.length} hold${activeHolds.length === 1 ? '' : 's'} in force`} />

      {/* ---------------------- Execute confirmation ---------------------- */}
      {confirm?.kind === 'execute' && (
        <TypedConfirm
          open
          title="Execute this erasure"
          phrase={confirm.job.job_ref}
          busy={busy}
          intro={<>
            This destroys the personal data of principal <b>#{confirm.job.customer_id}</b> under job{' '}
            <span className="mono">{confirm.job.job_ref}</span>. It cannot be undone by this console or by anyone else.
          </>}
          consequences={[
            `Action: ${confirm.job.action}. Personal data in scope is ${confirm.job.action === 'ERASE' ? 'destroyed' : 'irreversibly anonymised'}.`,
            'Records held back by a statutory floor are kept, and the job records which and why.',
            'The audit ledger keeps every row: erasure anonymises the principal, it does not erase the trail.',
            confirm.job.notice_required && !confirm.job.notice_sent_at
              ? `No pre-erasure notice has been sent. The engine requires ${confirm.job.notice_hours} hours' notice and will refuse this — the refusal will name the rule.`
              : 'The engine re-checks every precondition itself; if one fails it refuses and destroys nothing.',
          ]}
          confirmLabel="Erase permanently"
          onConfirm={async () => {
            // Close once the engine has answered, whether it acted or refused.
            // A refusal renders at the top of the page; leaving the dialog up
            // would hide the one sentence that says which rule stopped this,
            // and leave "Erase permanently" sitting there inviting a re-click.
            await run(() => erasureApi.execute(confirm.job.job_ref), `Erasure ${confirm.job.job_ref} executed.`)
            setConfirm(null)
          }}
          onClose={() => setConfirm(null)}
        />
      )}

      {/* ---------------------- Release confirmation ---------------------- */}
      {confirm?.kind === 'release' && (
        <TypedConfirm
          open
          title="Release this legal hold"
          phrase={confirm.hold.hold_ref}
          busy={busy}
          intro={<>
            Releasing <span className="mono">{confirm.hold.hold_ref}</span> removes the only thing currently standing
            between the data it covers and erasure. That makes this exactly as consequential as the erasure it unblocks.
          </>}
          consequences={[
            confirm.hold.customer_id
              ? `Principal #${confirm.hold.customer_id} becomes erasable again.`
              : confirm.hold.record_class
                ? `Record class ${confirm.hold.record_class} becomes erasable again.`
                : 'Everything this tenant-wide hold covered becomes erasable again.',
            `The hold's legal basis was: ${confirm.hold.legal_basis}`,
            'Any erasure job that was blocked by this hold can be executed immediately afterwards.',
            'A released hold is not deleted — the release, its reason and its author stay on the record.',
          ]}
          confirmLabel="Release hold"
          onConfirm={async () => {
            // The reason is checked here, before the call: that keeps the dialog
            // open for something the operator can still fix in it. Anything the
            // API itself refuses closes the dialog and shows the refusal above.
            if (!releaseReason.trim()) { toast('warning', 'A release reason is required'); return }
            await run(() => erasureApi.releaseHold(confirm.hold.hold_ref, { release_reason: releaseReason }),
              `Hold ${confirm.hold.hold_ref} released.`)
            setConfirm(null)
          }}
          onClose={() => setConfirm(null)}
        >
          <div className="form-group">
            <label>Release reason (recorded permanently)</label>
            <textarea className="textarea" rows={3} value={releaseReason} onChange={(e) => setReleaseReason(e.target.value)}
              placeholder="Why the legal ground for retention no longer applies" />
          </div>
        </TypedConfirm>
      )}

      {/* --------------------------- Raise a job --------------------------- */}
      <Modal open={raising} title="Raise an erasure" onClose={() => setRaising(false)}
        footer={
          <>
            <button className="btn" onClick={() => setRaising(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !raiseForm.customer_external_id || !raiseForm.authorisation_basis}
              onClick={async () => {
                const ok = await run(() => erasureApi.raiseJob({
                  customer_external_id: raiseForm.customer_external_id,
                  trigger: raiseForm.trigger,
                  request_ref: raiseForm.request_ref || null,
                  reason: raiseForm.reason,
                  authorisation_basis: raiseForm.authorisation_basis,
                }), 'Erasure job raised.')
                if (ok) { setRaising(false); setRaiseForm({ customer_external_id: '', trigger: 'MANUAL', request_ref: '', reason: '', authorisation_basis: '' }) }
              }}>Raise</button>
          </>
        }>
        <div className="alert alert-info mb">
          Raising a job destroys nothing. It has to be authorised, given its pre-erasure notice, and then executed —
          three separate acts, deliberately.
        </div>
        <div className="form-group"><label>Principal external id</label>
          <input className="input" value={raiseForm.customer_external_id}
            onChange={(e) => setRaiseForm({ ...raiseForm, customer_external_id: e.target.value })} /></div>
        <div className="form-row">
          <div className="form-group"><label>Trigger</label>
            <select className="select" value={raiseForm.trigger} onChange={(e) => setRaiseForm({ ...raiseForm, trigger: e.target.value as 'MANUAL' | 'RIGHTS_REQUEST' })}>
              <option value="MANUAL">MANUAL</option>
              <option value="RIGHTS_REQUEST">RIGHTS_REQUEST</option>
            </select></div>
          <div className="form-group"><label>Request reference</label>
            <input className="input" value={raiseForm.request_ref} onChange={(e) => setRaiseForm({ ...raiseForm, request_ref: e.target.value })} /></div>
        </div>
        <div className="form-group"><label>Authorisation basis (required)</label>
          <textarea className="textarea" rows={2} value={raiseForm.authorisation_basis}
            onChange={(e) => setRaiseForm({ ...raiseForm, authorisation_basis: e.target.value })}
            placeholder="e.g. s.12(3) request by the Data Principal on 2 Sep 2026" /></div>
        <div className="form-group"><label>Reason</label>
          <input className="input" value={raiseForm.reason} onChange={(e) => setRaiseForm({ ...raiseForm, reason: e.target.value })} /></div>
      </Modal>

      {/* --------------------------- Place a hold --------------------------- */}
      <Modal open={placing} title="Place a legal hold" onClose={() => setPlacing(false)}
        footer={
          <>
            <button className="btn" onClick={() => setPlacing(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !holdForm.legal_basis} onClick={async () => {
              const ok = await run(() => erasureApi.placeHold({
                legal_basis: holdForm.legal_basis,
                reason: holdForm.reason,
                customer_external_id: holdForm.customer_external_id || null,
                record_class: holdForm.record_class || null,
                tenant_code: holdForm.tenant_code || null,
                expires_at: holdForm.expires_at ? new Date(holdForm.expires_at).toISOString() : null,
              }), 'Legal hold placed.')
              if (ok) { setPlacing(false); setHoldForm({ legal_basis: '', reason: '', customer_external_id: '', record_class: '', tenant_code: '', expires_at: '' }) }
            }}>Place hold</button>
          </>
        }>
        <div className="form-group"><label>Legal basis (required)</label>
          <textarea className="textarea" rows={2} value={holdForm.legal_basis}
            onChange={(e) => setHoldForm({ ...holdForm, legal_basis: e.target.value })}
            placeholder="The law in force that requires this data to be retained" /></div>
        <div className="form-group"><label>Reason</label>
          <input className="input" value={holdForm.reason} onChange={(e) => setHoldForm({ ...holdForm, reason: e.target.value })} /></div>
        <div className="form-row">
          <div className="form-group"><label>Principal external id (optional)</label>
            <input className="input" value={holdForm.customer_external_id}
              onChange={(e) => setHoldForm({ ...holdForm, customer_external_id: e.target.value })} /></div>
          <div className="form-group"><label>Record class (optional)</label>
            <select className="select" value={holdForm.record_class} onChange={(e) => setHoldForm({ ...holdForm, record_class: e.target.value })}>
              <option value="">—</option>
              {RECORD_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select></div>
        </div>
        <div className="form-row">
          <div className="form-group"><label>Tenant (optional)</label>
            <select className="select" value={holdForm.tenant_code} onChange={(e) => setHoldForm({ ...holdForm, tenant_code: e.target.value })}>
              <option value="">—</option>
              {tenants.map((t) => <option key={t.id} value={t.code}>{t.name} ({t.code})</option>)}
            </select></div>
          <div className="form-group"><label>Expires at (optional)</label>
            <input className="input" type="datetime-local" value={holdForm.expires_at}
              onChange={(e) => setHoldForm({ ...holdForm, expires_at: e.target.value })} /></div>
        </div>
      </Modal>

      {/* ------------------------ Retention policy ------------------------ */}
      <Modal open={!!policyForm} title="Erasure retention policy" onClose={() => setPolicyForm(null)}
        footer={
          <>
            <button className="btn" onClick={() => setPolicyForm(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !policyForm?.legal_basis_for_retention} onClick={async () => {
              if (!policyForm) return
              const ok = await run(() => erasureApi.upsertPolicy({
                record_class: policyForm.record_class,
                scope: policyForm.scope || '*',
                retention_days: policyForm.retention_days === '' ? null : Number(policyForm.retention_days),
                inactivity_days: policyForm.inactivity_days === '' ? null : Number(policyForm.inactivity_days),
                pre_erasure_notice_hours: Number(policyForm.pre_erasure_notice_hours || 48),
                action: policyForm.action,
                legal_basis_for_retention: policyForm.legal_basis_for_retention,
                is_active: policyForm.is_active,
                notes: policyForm.notes,
              }), 'Retention policy saved.')
              if (ok) setPolicyForm(null)
            }}>Save</button>
          </>
        }>
        {policyForm && (
          <>
            <div className="form-row">
              <div className="form-group"><label>Record class</label>
                <select className="select" value={policyForm.record_class} onChange={(e) => setPolicyForm({ ...policyForm, record_class: e.target.value })}>
                  {RECORD_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select></div>
              <div className="form-group"><label>Scope</label>
                <input className="input" value={policyForm.scope} onChange={(e) => setPolicyForm({ ...policyForm, scope: e.target.value })} /></div>
              <div className="form-group"><label>Action</label>
                <select className="select" value={policyForm.action} onChange={(e) => setPolicyForm({ ...policyForm, action: e.target.value as 'ERASE' | 'ANONYMISE' })}>
                  <option value="ERASE">ERASE</option><option value="ANONYMISE">ANONYMISE</option>
                </select></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label>Retention days</label>
                <input className="input" type="number" min={0} value={policyForm.retention_days}
                  onChange={(e) => setPolicyForm({ ...policyForm, retention_days: e.target.value })} placeholder="none" /></div>
              <div className="form-group"><label>Inactivity days</label>
                <input className="input" type="number" min={0} value={policyForm.inactivity_days}
                  onChange={(e) => setPolicyForm({ ...policyForm, inactivity_days: e.target.value })} placeholder="none" /></div>
              <div className="form-group"><label>Pre-erasure notice (hours)</label>
                <input className="input" type="number" min={0} value={policyForm.pre_erasure_notice_hours}
                  onChange={(e) => setPolicyForm({ ...policyForm, pre_erasure_notice_hours: e.target.value })} /></div>
            </div>
            <div className="form-group"><label>Legal basis for retention (required)</label>
              <textarea className="textarea" rows={3} value={policyForm.legal_basis_for_retention}
                onChange={(e) => setPolicyForm({ ...policyForm, legal_basis_for_retention: e.target.value })} /></div>
            <div className="form-group"><label>Notes</label>
              <input className="input" value={policyForm.notes} onChange={(e) => setPolicyForm({ ...policyForm, notes: e.target.value })} /></div>
            <div className="form-group">
              <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={policyForm.is_active} onChange={(e) => setPolicyForm({ ...policyForm, is_active: e.target.checked })} />
                <span>Active</span>
              </label>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}

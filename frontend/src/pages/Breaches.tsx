/**
 * R3-08: the personal data breach register (`/breaches`).
 *
 * The one thing this screen must not do is flatten the difference between a
 * statutory deadline and an internal target. The backend distinguishes them
 * deliberately, per clock:
 *
 *   STATUTORY_DEADLINE  CERT-In's 6 hours, R.7(2)(b)'s 72 hours. A fixed
 *                       period imposed by law. Missing it is a legal breach.
 *   INTERNAL_TARGET     The principal intimation and the Board's initial
 *                       intimation are owed "without delay" with *no fixed
 *                       period*. The time shown is this organisation's own
 *                       target. Missing it is an internal SLA miss.
 *
 * So no clock here renders a bare countdown. Each one says which kind it is,
 * in words, next to the time — and an internal target is never coloured or
 * labelled as though the law set it. A dashboard that shows "overdue" against
 * a self-imposed target invites either false panic or, worse, the habit of
 * treating a real 6-hour statutory window as equally negotiable.
 *
 * Everything that touches a regulator (filing with the Board, filing with
 * CERT-In, issuing notices to affected principals) and the one act that starts
 * every clock at once (recording awareness) goes through a typed confirmation,
 * and every API refusal is shown verbatim.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { breachesApi, organizationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, TypedConfirm, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox, IconPlus, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import { LANGUAGES } from '../languages'
import type {
  Breach, BreachClock, BreachDetail, BreachExtension, BreachNotification, BreachReportPreview,
  Organization,
} from '../types'

const SEVERITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
const TRANSITIONS: Record<string, string[]> = {
  DETECTED: ['CLASSIFIED'],
  CLASSIFIED: ['CONTAINED', 'NOTIFIED'],
  CONTAINED: ['NOTIFIED', 'CLOSED'],
  NOTIFIED: ['CONTAINED', 'CLOSED'],
  CLOSED: [],
}

function severityTone(s: string): 'danger' | 'warning' | 'info' | 'slate' {
  if (s === 'CRITICAL') return 'danger'
  if (s === 'HIGH') return 'warning'
  if (s === 'MEDIUM') return 'info'
  return 'slate'
}

function hours(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) < 1) return `${Math.round(n * 60)} min`
  if (Math.abs(n) < 48) return `${n.toFixed(1)} h`
  return `${(n / 24).toFixed(1)} d`
}

/**
 * One clock.
 *
 * Which *kind* of obligation this is comes from `window_hours` /
 * `internal_target_hours`, not from `measured_against`. That matters: the
 * derived `measured_against` is "NONE" until awareness is recorded, so reading
 * it alone would label a not-yet-started CERT-In clock "no fixed period" —
 * telling the reader the law sets no deadline for a report the law gives six
 * hours. The two `*_hours` fields are set from the obligation itself and are
 * present before the clock starts, so they are what the label is built from.
 *
 *   window_hours          a fixed period imposed by law (CERT-In 6h, R.7(2)(b) 72h)
 *   internal_target_hours "without delay", no fixed period — the time shown is ours
 */
function ClockCard({ clock }: { clock: BreachClock }) {
  const statutory = typeof clock.window_hours === 'number'
  const internal = !statutory && typeof clock.internal_target_hours === 'number'
  const started = clock.status !== 'NOT_STARTED' && clock.status !== 'NOT_APPLICABLE'
  const reference = statutory ? clock.deadline_at : internal ? clock.internal_target_at : null

  const statusText = (() => {
    switch (clock.status) {
      case 'NOT_APPLICABLE': return 'Not applicable'
      case 'NOT_STARTED': return 'Not started — awareness has not been recorded'
      case 'MET': return statutory ? 'Met within the statutory deadline' : internal ? 'Met within the internal target' : 'Discharged'
      case 'MET_LATE': return statutory ? 'Met, but after the statutory deadline' : 'Met, but after the internal target'
      case 'OVERDUE': return statutory ? 'Statutory deadline passed' : 'Internal target passed'
      default: return 'Open'
    }
  })()

  const tone = (() => {
    if (clock.status === 'NOT_APPLICABLE' || clock.status === 'NOT_STARTED') return 'slate'
    if (clock.status === 'MET') return 'ok'
    // An internal-target overrun is amber, never red: it is not a contravention.
    if (clock.status === 'OVERDUE' || clock.status === 'MET_LATE') return statutory ? 'danger' : 'warn'
    return 'open'
  })()

  return (
    <div className={`clock-card clock-${tone}`}>
      <div className="clock-head">
        <span className="clock-name">{clock.obligation}</span>
        <span className={`clock-kind clock-kind-${statutory ? 'statutory' : internal ? 'internal' : 'none'}`}>
          {statutory
            ? `Statutory · ${clock.window_hours} h`
            : internal
              ? `Internal target · ${clock.internal_target_hours} h`
              : 'No fixed period'}
        </span>
      </div>

      <div className="clock-status">{statusText}</div>

      <dl className="clock-grid">
        <div><dt>Clock starts</dt><dd>{started ? formatDateTime(clock.started_at) : 'on becoming aware'}</dd></div>
        <div>
          <dt>{statutory ? 'Deadline' : internal ? 'Target' : 'Reference'}</dt>
          <dd>{reference ? formatDateTime(reference) : started ? '—' : 'not yet running'}</dd>
        </div>
        <div><dt>Elapsed</dt><dd>{hours(clock.elapsed_hours)}</dd></div>
        <div>
          <dt>Remaining</dt>
          <dd>{clock.remaining_hours == null ? '—' : clock.remaining_hours < 0 ? `${hours(-clock.remaining_hours)} over` : hours(clock.remaining_hours)}</dd>
        </div>
      </dl>

      {/*
        The sentence that keeps the distinction honest. It is deliberately not
        a tooltip: a reader deciding whether they are legally late must not have
        to hover to find out whether the law set this time.
      */}
      <p className="clock-basis">
        {statutory
          ? <><b>Fixed by law: {clock.window_hours} hours from becoming aware.</b> {clock.basis}</>
          : internal
            ? <><b>Not a legal deadline.</b> The law requires this without delay and fixes no period; the {clock.internal_target_hours}-hour time above is this organisation&rsquo;s own target. Legal basis: {clock.basis}</>
            : <>{clock.basis}</>}
      </p>

      {/* A Board-granted extension moves the deadline; it must not erase the
          original, or nobody can later tell an extension from a late filing. */}
      {clock.extension_granted && clock.statutory_deadline_at && (
        <p className="clock-basis">
          <b>Extended by the Board.</b> Without the extension this was due{' '}
          {formatDateTime(clock.statutory_deadline_at)}.
        </p>
      )}
      {clock.scope_note && <p className="clock-basis text-muted">{clock.scope_note}</p>}
      {clock.status === 'NOT_APPLICABLE' && clock.not_applicable_reason && (
        <p className="clock-basis text-muted">Why not applicable: {clock.not_applicable_reason}</p>
      )}
      {clock.filing_status && (
        <div className="clock-foot">Filing status: <span className="mono">{clock.filing_status}</span></div>
      )}
    </div>
  )
}

/**
 * The K-39/K-40/K-41/CERT-In metric groups the register publishes.
 *
 * Each group arrives as a nested object carrying its own definition, its
 * statutory provision and a handful of figures — several of which are null
 * whenever nothing has happened yet. Rendering them as one JSON blob (the
 * first version of this page did) both broke the page width and buried the
 * provision, which is the part that makes the number mean anything. So each
 * group gets a card, the provision is quoted, and a null figure reads
 * "No data yet" rather than 0.
 */
function metricLabel(key: string): string {
  return key.replace(/_/g, ' ').replace(/\bpct\b/, '%').replace(/^./, (c) => c.toUpperCase())
}

function metricValue(key: string, value: unknown): string {
  if (value == null) return 'No data yet'
  if (typeof value === 'number') {
    if (key.endsWith('_pct')) return `${value.toFixed(1)}%`
    if (key.endsWith('_hours')) return hours(value)
    return String(value)
  }
  return String(value)
}

const PROSE_KEYS = new Set(['definition', 'provision', 'note', 'target'])

function BreachMetricsPanel({ metrics }: { metrics: Record<string, unknown> }) {
  const groups = Object.entries(metrics).filter(
    ([k, v]) => typeof v === 'object' && v !== null && !Array.isArray(v),
  ) as Array<[string, Record<string, unknown>]>

  return (
    <div className="card mb">
      <div className="card-header">
        <h3>Register metrics</h3>
        <span className="text-sm text-muted">
          {formatDateTime(String(metrics.generated_at))} · scope {String(metrics.scope)}
        </span>
      </div>
      <div className="card-body">
        <div className="breach-metric-grid">
          {groups.map(([key, group]) => (
            <div className="breach-metric-card" key={key}>
              <div className="breach-metric-head">
                <span className="breach-metric-id">{key.toUpperCase().replace(/_/g, '-')}</span>
                <span className="breach-metric-def">{String(group.definition ?? '')}</span>
              </div>
              <p className="breach-metric-provision">{String(group.provision ?? '')}</p>
              <dl className="breach-metric-values">
                {Object.entries(group)
                  .filter(([k]) => !PROSE_KEYS.has(k))
                  .map(([k, v]) => (
                    <div key={k}>
                      <dt>{metricLabel(k)}</dt>
                      <dd className={v == null ? 'text-muted' : ''}>{metricValue(k, v)}</dd>
                    </div>
                  ))}
              </dl>
              {group.target != null && <p className="breach-metric-target">Target: {String(group.target)}</p>}
              {group.note != null && <p className="breach-metric-note">{String(group.note)}</p>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// List
// --------------------------------------------------------------------------
const BLANK_BREACH = {
  title: '', source_app: '', severity: 'MEDIUM', occurred_at: '', detected_at: '', aware_at: '',
  nature: '', extent: '', location: '', likely_impact: '', likely_consequences: '',
  mitigation_measures: '', safety_measures: '', cause: '', cert_in_reportable: false,
}

export function BreachesPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('breach.manage')

  const [breaches, setBreaches] = useState<Breach[]>([])
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null)
  const [tenants, setTenants] = useState<Organization[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const [refusal, setRefusal] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ ...BLANK_BREACH })
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const [b, m] = await Promise.all([
      breachesApi.list(statusFilter ? { status: statusFilter } : {}),
      breachesApi.metrics(),
    ])
    setBreaches(b.data)
    setMetrics(m.data as unknown as Record<string, unknown>)
  }, [statusFilter])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => {
    organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([]))
  }, [])

  const create = async () => {
    setBusy(true)
    setRefusal('')
    const body: Record<string, unknown> = { ...form }
    for (const key of ['occurred_at', 'detected_at', 'aware_at']) {
      const v = body[key] as string
      body[key] = v ? new Date(v).toISOString() : null
    }
    try {
      const r = await breachesApi.create(body)
      toast('success', `Breach ${r.data.breach_ref} registered.`)
      setCreating(false)
      setForm({ ...BLANK_BREACH })
      navigate(`/breaches/${r.data.breach_ref}`)
    } catch (e) {
      setRefusal(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const counts = useMemo(() => ({
    open: breaches.filter((b) => b.status !== 'CLOSED').length,
    unaware: breaches.filter((b) => !b.aware_at).length,
    certIn: breaches.filter((b) => b.cert_in_reportable && b.status !== 'CLOSED').length,
  }), [breaches])

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Breach register"
        subtitle="Every personal data breach, its three statutory clocks, and the Rule 7 filings made against it"
        actions={
          <div className="flex" style={{ gap: 8 }}>
            <select className="input" style={{ width: 'auto' }} value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)} aria-label="Status">
              <option value="">All statuses</option>
              {['DETECTED', 'CLASSIFIED', 'CONTAINED', 'NOTIFIED', 'CLOSED'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            {canManage && (
              <button className="btn btn-primary" onClick={() => { setForm({ ...BLANK_BREACH, source_app: tenants[0]?.code || '' }); setCreating(true) }}>
                <IconPlus size={14} /> Register a breach
              </button>
            )}
          </div>
        }
      />

      {!canManage && <ReadOnlyBanner permission="breach.manage" what="Registering a breach, recording awareness and filing with a regulator" />}

      <RefusalNotice title="The breach register refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      <div className="metric-grid mb">
        <MetricCard label="Breaches on the register" value={breaches.length} tone="primary" icon={<IconShield size={20} />}
          sub="every incident classed as a personal data breach" />
        <MetricCard label="Not yet closed" value={counts.open} tone="warning" icon={<IconAlert size={20} />}
          sub="at least one Rule 7 duty still open" />
        <MetricCard label="Awareness not recorded" value={counts.unaware} tone="danger" icon={<IconClock size={20} />}
          sub="no clock has started on these" />
        <MetricCard label="CERT-In reportable" value={counts.certIn} tone="info" icon={<IconCheck size={20} />}
          sub="6-hour window applies" />
      </div>

      {metrics && <BreachMetricsPanel metrics={metrics} />}

      {breaches.length === 0 ? (
        <EmptyState icon={<IconInbox size={30} />} title="No breaches on the register"
          message="An empty register is a real finding, not a gap: it means no incident has been classified as a personal data breach." />
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Reference</th><th>Title</th><th>Tenant</th><th>Severity</th><th>Status</th>
                  <th>Aware at</th><th>Affected</th><th>CERT-In</th><th />
                </tr>
              </thead>
              <tbody>
                {breaches.map((b) => (
                  <tr key={b.breach_ref}>
                    <td className="mono text-xs">{b.breach_ref}</td>
                    <td>{b.title}</td>
                    <td className="mono text-xs">{b.source_app}</td>
                    <td><span className={`chip tone-${severityTone(b.severity)}`}>{b.severity}</span></td>
                    <td><Badge status={b.status === 'CLOSED' ? 'ACTIVE' : 'PENDING'}>{b.status}</Badge></td>
                    <td className={b.aware_at ? '' : 'text-danger'}>
                      {b.aware_at ? formatDateTime(b.aware_at) : 'not recorded'}
                    </td>
                    <td>{b.scope_finalised_at ? b.affected_count : `${b.affected_count} (not final)`}</td>
                    <td>{b.cert_in_reportable ? 'Yes' : 'No'}</td>
                    <td><Link className="btn btn-sm" to={`/breaches/${b.breach_ref}`}>Open</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <FooterRow left={`${breaches.length} breach${breaches.length === 1 ? '' : 'es'} · ${counts.open} open`} />

      <Modal open={creating} wide title="Register a personal data breach" onClose={() => setCreating(false)}
        footer={
          <>
            <button className="btn" onClick={() => setCreating(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" onClick={create} disabled={busy || !form.title || !form.source_app}>
              {busy ? 'Registering…' : 'Register'}
            </button>
          </>
        }>
        <div className="alert alert-info mb">
          Registering does <b>not</b> start any clock. Every R.7 and CERT-In clock starts from the moment the
          fiduciary became <i>aware</i>, which is recorded separately on the breach — deliberately, so the two
          cannot be conflated.
        </div>
        <div className="form-row">
          <div className="form-group"><label>Title</label>
            <input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div className="form-group"><label>Tenant (source_app)</label>
            <select className="select" value={form.source_app} onChange={(e) => setForm({ ...form, source_app: e.target.value })}>
              <option value="">Select…</option>
              {tenants.map((t) => <option key={t.id} value={t.code}>{t.name} ({t.code})</option>)}
            </select></div>
          <div className="form-group"><label>Severity</label>
            <select className="select" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select></div>
        </div>
        <div className="form-row">
          <div className="form-group"><label>Occurred at (best known)</label>
            <input className="input" type="datetime-local" value={form.occurred_at} onChange={(e) => setForm({ ...form, occurred_at: e.target.value })} /></div>
          <div className="form-group"><label>Detected at</label>
            <input className="input" type="datetime-local" value={form.detected_at} onChange={(e) => setForm({ ...form, detected_at: e.target.value })} /></div>
          <div className="form-group"><label>Became aware at (optional — starts every clock)</label>
            <input className="input" type="datetime-local" value={form.aware_at} onChange={(e) => setForm({ ...form, aware_at: e.target.value })} /></div>
        </div>
        {([
          ['nature', 'Nature of the breach'],
          ['extent', 'Extent'],
          ['location', 'Location of the affected data'],
          ['likely_impact', 'Likely impact'],
          ['likely_consequences', 'Likely consequences for principals'],
          ['mitigation_measures', 'Mitigation measures taken'],
          ['safety_measures', 'Safety measures principals should take'],
          ['cause', 'Cause'],
        ] as const).map(([key, label]) => (
          <div className="form-group" key={key}>
            <label>{label}</label>
            <textarea className="textarea" rows={2} value={form[key]}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
          </div>
        ))}
        <div className="form-group">
          <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={form.cert_in_reportable}
              onChange={(e) => setForm({ ...form, cert_in_reportable: e.target.checked })} />
            <span>Reportable to CERT-In (28 Apr 2022 Directions) — imposes a 6-hour statutory deadline</span>
          </label>
        </div>
      </Modal>
    </div>
  )
}

// --------------------------------------------------------------------------
// Detail
// --------------------------------------------------------------------------
type DetailTab = 'clocks' | 'filings' | 'affected' | 'extensions' | 'timeline' | 'record'

export function BreachDetailPage() {
  const { breachRef = '' } = useParams()
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('breach.manage')
  const canReadReports = hasPermission('audit.export')

  const [breach, setBreach] = useState<BreachDetail | null>(null)
  const [notifications, setNotifications] = useState<BreachNotification[]>([])
  const [extensions, setExtensions] = useState<BreachExtension[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<DetailTab>('clocks')
  const [refusal, setRefusal] = useState('')
  const [busy, setBusy] = useState(false)

  const [confirm, setConfirm] = useState<null | {
    kind: 'aware' | 'principals' | 'board-initial' | 'board-detailed' | 'cert-in' | 'finalise' | 'close'
    phrase: string
  }>(null)
  const [awareAt, setAwareAt] = useState('')
  const [noticeLang, setNoticeLang] = useState('en')
  const [closeNote, setCloseNote] = useState('')

  const [affectedIds, setAffectedIds] = useState('')
  const [dataInvolved, setDataInvolved] = useState('')
  const [addResult, setAddResult] = useState<{ added: number; already_present: number; unresolved: string[] } | null>(null)

  const [report, setReport] = useState<BreachReportPreview | null>(null)
  const [filingFor, setFilingFor] = useState<BreachNotification | null>(null)
  const [filingRef, setFilingRef] = useState('')

  const [extForm, setExtForm] = useState({ requested_until: '', reason: '', written_request_ref: '' })
  const [showExtForm, setShowExtForm] = useState(false)

  const load = useCallback(async () => {
    const b = await breachesApi.get(breachRef)
    setBreach(b.data)
    const [n, e] = await Promise.all([
      breachesApi.notifications(breachRef).catch(() => ({ data: [] as BreachNotification[] })),
      breachesApi.extensions(breachRef).catch(() => ({ data: [] as BreachExtension[] })),
    ])
    setNotifications(n.data)
    setExtensions(e.data)
  }, [breachRef])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => setRefusal(getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  /** Every mutating call goes through here so a refusal can never be mistaken
   *  for a success: on a rejection we set the refusal and reload nothing. */
  const run = async (fn: () => Promise<unknown>, successMessage: string) => {
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
      setConfirm(null)
    }
  }

  if (loading) return <Spinner />
  if (!breach) {
    return (
      <div>
        <PageHeading title="Breach" subtitle={breachRef} />
        <RefusalNotice title="Could not load this breach" detail={refusal || 'Not found'} />
        <Link className="btn" to="/breaches">Back to the register</Link>
      </div>
    )
  }

  // Same signal the clock cards use: the obligation's own kind, not the derived
  // `measured_against`, so the two figures cannot disagree with the cards below.
  const isStatutory = (c: BreachClock) => typeof c.window_hours === 'number'
  const overdueStatutory = breach.clocks.filter((c) => isStatutory(c) && c.status === 'OVERDUE').length
  const overdueTargets = breach.clocks.filter(
    (c) => !isStatutory(c) && typeof c.internal_target_hours === 'number' && c.status === 'OVERDUE',
  ).length

  const openReport = async (kind: 'board-initial' | 'board-detailed' | 'cert-in') => {
    setRefusal('')
    try {
      const r = await breachesApi.report(breachRef, kind)
      setReport(r.data)
    } catch (e) {
      setRefusal(getErrorMessage(e))
    }
  }

  const confirmSpec = (() => {
    switch (confirm?.kind) {
      case 'aware':
        return {
          title: 'Record the moment of awareness',
          intro: <>This is the single act that starts <b>every</b> statutory clock on this breach. The register
            measures the CERT-In 6-hour window and the R.7(2)(b) 72-hour report from this timestamp, not from
            detection.</>,
          consequences: [
            'The CERT-In 6-hour statutory deadline starts running from the time you enter (if this breach is CERT-In reportable).',
            'The R.7(2)(b) 72-hour Board report deadline starts running from the same time.',
            'The "without delay" obligations to principals and to the Board get an internal target measured from it.',
            'Awareness is recorded once. Backdating it shortens every window that follows.',
          ],
          confirmLabel: 'Record awareness',
          onConfirm: () => run(
            () => breachesApi.markAware(breachRef, { aware_at: awareAt ? new Date(awareAt).toISOString() : null }),
            'Awareness recorded. Every clock has started.',
          ),
          body: (
            <div className="form-group">
              <label>Aware at (leave blank for now)</label>
              <input className="input" type="datetime-local" value={awareAt} onChange={(e) => setAwareAt(e.target.value)} />
            </div>
          ),
        }
      case 'principals':
        return {
          title: 'Issue notices to every affected principal',
          intro: <>This generates and queues an individual intimation to each of the <b>{breach.affected_count}</b> principals
            on the finalised list. Each notice quotes that principal&rsquo;s own involvement.</>,
          consequences: [
            `${breach.affected_count} individual notices will be generated and queued for delivery.`,
            'Each notice is content-hashed; the hash is the evidence of what was said.',
            'Notices already issued are not re-sent, but new ones cannot be recalled.',
          ],
          confirmLabel: 'Issue notices',
          onConfirm: () => run(
            () => breachesApi.notifyPrincipals(breachRef, { language: noticeLang }),
            'Principal notices generated.',
          ),
          body: (
            <div className="form-group">
              <label>Language</label>
              <select className="select" value={noticeLang} onChange={(e) => setNoticeLang(e.target.value)}>
                {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn}</option>)}
              </select>
            </div>
          ),
        }
      case 'board-initial':
        return {
          title: 'File the initial intimation with the Data Protection Board',
          intro: <>This produces a signed, hashed filing addressed to the Board and marks the R.7(2)(a) obligation
            discharged for this breach.</>,
          consequences: [
            'A regulator-facing document is generated and its content hash recorded.',
            'The obligation is recorded as discharged at this moment; the timestamp is evidence.',
            'A filing cannot be withdrawn from the register.',
          ],
          confirmLabel: 'File with the Board',
          onConfirm: () => run(() => breachesApi.notifyBoardInitial(breachRef), 'Initial intimation filed.'),
        }
      case 'board-detailed':
        return {
          title: 'File the 72-hour detailed report with the Board',
          intro: <>R.7(2)(b). This is the full report: the breach, its extent, the principals affected, what was done
            and what remains.</>,
          consequences: [
            'A regulator-facing report is generated and its content hash recorded.',
            'This is measured against a statutory 72-hour deadline running from awareness.',
            'A filing cannot be withdrawn from the register.',
          ],
          confirmLabel: 'File the detailed report',
          onConfirm: () => run(() => breachesApi.notifyBoardDetailed(breachRef), 'Detailed report filed.'),
        }
      case 'cert-in':
        return {
          title: 'File the CERT-In report',
          intro: <>CERT-In Directions of 28 April 2022. The window is <b>6 hours from awareness</b> and is a
            statutory deadline, not an internal target.</>,
          consequences: [
            'A regulator-facing report is generated and its content hash recorded.',
            'The 6-hour statutory clock is marked satisfied at the moment the filing is sent.',
            'A filing cannot be withdrawn from the register.',
          ],
          confirmLabel: 'File with CERT-In',
          onConfirm: () => run(() => breachesApi.notifyCertIn(breachRef), 'CERT-In report filed.'),
        }
      case 'finalise':
        return {
          title: 'Finalise the affected-principal list',
          intro: <>R.7(1). Finalising asserts that the list of {breach.affected_count} principals is complete, and it
            is what the &ldquo;all principals notified&rdquo; clock measures against.</>,
          consequences: [
            'The affected list is frozen at its current membership.',
            'The principal-intimation clock can only be satisfied once every principal on this list has been notified.',
            'Adding principals after finalisation is not an ordinary edit.',
          ],
          confirmLabel: 'Finalise scope',
          onConfirm: () => run(() => breachesApi.finaliseAffected(breachRef), 'Affected-principal list finalised.'),
        }
      case 'close':
        return {
          title: 'Close this breach',
          intro: <>Closing asserts that every Rule 7 duty on this breach has been discharged. The API refuses the
            close while any obligation is outstanding and names each one.</>,
          consequences: [
            'The breach is recorded as closed, with this note, at this moment.',
            'Closure is the register\'s assertion to a regulator that nothing remains outstanding.',
          ],
          confirmLabel: 'Close breach',
          onConfirm: () => run(
            () => breachesApi.changeStatus(breachRef, { status: 'CLOSED', note: closeNote }),
            'Breach closed.',
          ),
          body: (
            <div className="form-group">
              <label>Closure note</label>
              <textarea className="textarea" rows={3} value={closeNote} onChange={(e) => setCloseNote(e.target.value)} />
            </div>
          ),
        }
      default:
        return null
    }
  })()

  return (
    <div>
      <PageHeading
        title={breach.title}
        subtitle={<span className="mono">{breach.breach_ref} · {breach.source_app} · {breach.severity}</span>}
        actions={
          <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
            <Link className="btn" to="/breaches">Back to register</Link>
            {canManage && !breach.aware_at && (
              <button className="btn btn-warning" onClick={() => setConfirm({ kind: 'aware', phrase: breach.breach_ref })}>
                Record awareness
              </button>
            )}
            {canManage && (TRANSITIONS[breach.status] || []).filter((s) => s !== 'CLOSED').map((s) => (
              <button key={s} className="btn btn-sm" disabled={busy}
                onClick={() => run(() => breachesApi.changeStatus(breachRef, { status: s }), `Status changed to ${s}.`)}>
                Move to {s}
              </button>
            ))}
            {canManage && (TRANSITIONS[breach.status] || []).includes('CLOSED') && (
              <button className="btn btn-danger btn-sm" onClick={() => setConfirm({ kind: 'close', phrase: breach.breach_ref })}>
                Close
              </button>
            )}
          </div>
        }
      />

      {!canManage && <ReadOnlyBanner permission="breach.manage" what="Recording awareness, filing with a regulator and closing a breach" />}

      <RefusalNotice title="The breach register refused this action" detail={refusal} onDismiss={() => setRefusal('')} />

      {!breach.aware_at && (
        <div className="alert alert-error mb">
          <b>No clock is running.</b> Every R.7 and CERT-In deadline is measured from the moment the fiduciary became
          aware, and that has not been recorded. Nothing on the Clocks tab is late, because nothing has started.
        </div>
      )}

      {breach.outstanding_obligations.length > 0 && (
        <div className="alert alert-info mb">
          <b>Outstanding before this breach can be closed:</b>
          <ul className="danger-list" style={{ marginBottom: 0 }}>
            {breach.outstanding_obligations.map((o) => <li key={o}>{o}</li>)}
          </ul>
        </div>
      )}

      <div className="metric-grid mb">
        <MetricCard label="Status" value={breach.status} tone="primary" icon={<IconShield size={20} />}
          sub={breach.closed_at ? `closed ${formatDateTime(breach.closed_at)}` : 'open'} />
        <MetricCard label="Statutory deadlines missed" value={overdueStatutory} tone={overdueStatutory ? 'danger' : 'success'}
          icon={<IconAlert size={20} />} sub="fixed periods imposed by law" />
        <MetricCard label="Internal targets missed" value={overdueTargets} tone={overdueTargets ? 'warning' : 'success'}
          icon={<IconClock size={20} />} sub="our own targets — not legal deadlines" />
        <MetricCard label="Affected principals" value={breach.affected_count} tone="info" icon={<IconInbox size={20} />}
          sub={breach.scope_finalised_at ? `finalised ${formatDateTime(breach.scope_finalised_at)}` : 'list not finalised'} />
      </div>

      <Tabs<DetailTab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'clocks', label: 'Clocks', count: breach.clocks.length },
          { id: 'filings', label: 'Filings', count: notifications.length },
          { id: 'affected', label: 'Affected principals', count: breach.affected_count },
          { id: 'extensions', label: 'Extensions', count: extensions.length },
          { id: 'timeline', label: 'Timeline', count: breach.timeline.length },
          { id: 'record', label: 'Record' },
        ]}
      />

      {tab === 'clocks' && (
        <>
          <div className="card mb">
            <div className="card-body">
              <p className="text-sm text-secondary" style={{ margin: 0 }}>
                <b>Two kinds of time are shown here and they are not the same thing.</b> A{' '}
                <span className="clock-kind clock-kind-statutory">Statutory</span> clock carries a fixed period the law
                imposes — CERT-In&rsquo;s 6 hours, R.7(2)(b)&rsquo;s 72 — and missing it is a contravention. An{' '}
                <span className="clock-kind clock-kind-internal">Internal target</span> is this organisation&rsquo;s own
                target for an obligation the law states as &ldquo;without delay&rdquo; with no fixed period; missing it
                is an SLA miss, not a contravention. Which kind a clock is comes from the obligation, so it reads the
                same before the clock starts as after — the register never converts one into the other.
              </p>
            </div>
          </div>
          <div className="clock-grid-outer">
            {breach.clocks.map((c) => <ClockCard key={c.clock} clock={c} />)}
          </div>
        </>
      )}

      {tab === 'filings' && (
        <>
          <div className="card mb">
            <div className="card-header"><h3>Regulator filings</h3></div>
            <div className="card-body">
              <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
                {canReadReports ? (
                  <>
                    <button className="btn btn-sm" onClick={() => openReport('board-initial')}>Preview Board initial</button>
                    <button className="btn btn-sm" onClick={() => openReport('board-detailed')}>Preview Board detailed</button>
                    <button className="btn btn-sm" onClick={() => openReport('cert-in')} disabled={!breach.cert_in_reportable}
                      title={breach.cert_in_reportable ? undefined : 'This breach is not CERT-In reportable'}>
                      Preview CERT-In
                    </button>
                  </>
                ) : (
                  <span className="text-sm text-muted">
                    Report previews require <span className="mono">audit.export</span> — a Board report aggregates every
                    affected principal, so reading one is an export-grade act.
                  </span>
                )}
              </div>
              {canManage && (
                <div className="flex" style={{ gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                  <button className="btn btn-warning btn-sm" disabled={!breach.aware_at}
                    onClick={() => setConfirm({ kind: 'board-initial', phrase: breach.breach_ref })}>
                    File Board initial (R.7(2)(a))
                  </button>
                  <button className="btn btn-warning btn-sm" disabled={!breach.aware_at}
                    onClick={() => setConfirm({ kind: 'board-detailed', phrase: breach.breach_ref })}>
                    File Board detailed (R.7(2)(b))
                  </button>
                  <button className="btn btn-warning btn-sm" disabled={!breach.aware_at || !breach.cert_in_reportable}
                    onClick={() => setConfirm({ kind: 'cert-in', phrase: breach.breach_ref })}>
                    File CERT-In (6 h)
                  </button>
                  <button className="btn btn-warning btn-sm" disabled={!breach.scope_finalised_at || breach.affected_count === 0}
                    title={breach.scope_finalised_at ? undefined : 'Finalise the affected list first'}
                    onClick={() => setConfirm({ kind: 'principals', phrase: breach.breach_ref })}>
                    Issue principal notices
                  </button>
                </div>
              )}
              {!breach.aware_at && (
                <p className="text-sm text-muted" style={{ marginTop: 10 }}>
                  Filing is disabled until awareness is recorded — a filing has to state when the fiduciary became aware.
                </p>
              )}
            </div>
          </div>

          {notifications.length === 0 ? (
            <EmptyState message="Nothing has been filed or issued against this breach yet." />
          ) : (
            <div className="card">
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Recipient</th><th>Stage</th><th>Status</th><th>Measured against</th><th>Sent</th><th>Filing ref</th><th>Hash</th><th /></tr>
                  </thead>
                  <tbody>
                    {notifications.map((n) => (
                      <tr key={n.id}>
                        <td>{n.recipient_type}{n.customer_id ? ` #${n.customer_id}` : ''}</td>
                        <td>{n.stage}</td>
                        <td><Badge status={n.status === 'DELIVERED' || n.status === 'SENT' ? 'ACTIVE' : n.status === 'FAILED' ? 'DENIED' : 'PENDING'}>{n.status}</Badge></td>
                        <td className="text-xs">
                          {n.deadline_at
                            ? <span className="clock-kind clock-kind-statutory">Statutory {formatDateTime(n.deadline_at)}</span>
                            : n.target_at
                              ? <span className="clock-kind clock-kind-internal">Target {formatDateTime(n.target_at)}</span>
                              : <span className="text-muted">no fixed period</span>}
                        </td>
                        <td>{formatDateTime(n.sent_at)}</td>
                        <td className="mono text-xs">{n.filing_reference || '—'}</td>
                        <td className="mono text-xs" title={n.content_hash}>{n.content_hash.slice(0, 12)}…</td>
                        <td>
                          <div className="flex" style={{ gap: 6 }}>
                            <button className="btn btn-sm" onClick={async () => {
                              try {
                                const r = await breachesApi.verifyNotification(breachRef, n.id)
                                toast(r.data.matches ? 'success' : 'error',
                                  r.data.matches
                                    ? `Hash verified (${r.data.algorithm}). The filing is unaltered.`
                                    : 'Hash MISMATCH — the recorded filing no longer matches its content.')
                              } catch (e) { setRefusal(getErrorMessage(e)) }
                            }}>Verify</button>
                            {canManage && n.recipient_type !== 'PRINCIPAL' && (
                              <button className="btn btn-sm" onClick={() => { setFilingFor(n); setFilingRef(n.filing_reference) }}>
                                Filing ref
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {tab === 'affected' && (
        <div className="card">
          <div className="card-header">
            <h3>Affected principals</h3>
            <span className="text-sm text-muted">
              {breach.scope_finalised_at ? `Finalised ${formatDateTime(breach.scope_finalised_at)}` : 'Not finalised'}
            </span>
          </div>
          <div className="card-body">
            {canManage && !breach.scope_finalised_at ? (
              <>
                <div className="form-group">
                  <label>External ids (one per line, or comma separated)</label>
                  <textarea className="textarea" rows={5} value={affectedIds}
                    onChange={(e) => setAffectedIds(e.target.value)} placeholder="CUST-001&#10;CUST-002" />
                </div>
                <div className="form-group">
                  <label>Data involved</label>
                  <input className="input" value={dataInvolved} onChange={(e) => setDataInvolved(e.target.value)}
                    placeholder="What of theirs was in scope" />
                </div>
                <div className="flex" style={{ gap: 8 }}>
                  <button className="btn btn-primary btn-sm" disabled={busy || !affectedIds.trim()} onClick={async () => {
                    setRefusal('')
                    const ids = affectedIds.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)
                    try {
                      const r = await breachesApi.addAffected(breachRef, { external_ids: ids, data_involved: dataInvolved })
                      setAddResult(r.data)
                      setAffectedIds('')
                      await load()
                    } catch (e) { setRefusal(getErrorMessage(e)) }
                  }}>Add to the affected list</button>
                  <button className="btn btn-warning btn-sm" disabled={busy || breach.affected_count === 0}
                    onClick={() => setConfirm({ kind: 'finalise', phrase: breach.breach_ref })}>
                    Finalise scope (R.7(1))
                  </button>
                </div>
                {addResult && (
                  <div className={`alert ${addResult.unresolved.length ? 'alert-error' : 'alert-success'}`} style={{ marginTop: 12 }}>
                    Added {addResult.added}; {addResult.already_present} already on the list.
                    {addResult.unresolved.length > 0 && (
                      <> <b>Not resolved to a principal in this tenant:</b> {addResult.unresolved.join(', ')}. These were
                        <b> not</b> added.</>
                    )}
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-secondary">
                {breach.scope_finalised_at
                  ? `The list was finalised on ${formatDateTime(breach.scope_finalised_at)} with ${breach.affected_count} principals. It is the denominator the principal-notification clock measures against.`
                  : 'Adding principals requires breach.manage.'}
              </p>
            )}
          </div>
        </div>
      )}

      {tab === 'extensions' && (
        <div className="card">
          <div className="card-header">
            <h3>Extension requests to the Board</h3>
            {canManage && <button className="btn btn-sm" onClick={() => setShowExtForm((v) => !v)}>{showExtForm ? 'Cancel' : 'Request an extension'}</button>}
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              An extension moves the R.7(2)(b) deadline only once the Board grants it. Logging a request here does
              not move any clock — the deadline stays where it is until a decision with a Board reference is recorded.
            </p>
            {showExtForm && canManage && (
              <div className="form-row">
                <div className="form-group"><label>Requested until</label>
                  <input className="input" type="datetime-local" value={extForm.requested_until}
                    onChange={(e) => setExtForm({ ...extForm, requested_until: e.target.value })} /></div>
                <div className="form-group"><label>Reason</label>
                  <input className="input" value={extForm.reason} onChange={(e) => setExtForm({ ...extForm, reason: e.target.value })} /></div>
                <div className="form-group"><label>Written request reference</label>
                  <input className="input" value={extForm.written_request_ref}
                    onChange={(e) => setExtForm({ ...extForm, written_request_ref: e.target.value })} /></div>
                <div className="form-group" style={{ maxWidth: 140 }}><label>&nbsp;</label>
                  <button className="btn btn-primary btn-sm" disabled={!extForm.requested_until || !extForm.reason}
                    onClick={() => run(
                      () => breachesApi.requestExtension(breachRef, {
                        requested_until: new Date(extForm.requested_until).toISOString(),
                        reason: extForm.reason,
                        written_request_ref: extForm.written_request_ref,
                      }),
                      'Extension request logged.',
                    ).then((ok) => { if (ok) { setShowExtForm(false); setExtForm({ requested_until: '', reason: '', written_request_ref: '' }) } })}>
                    Log request
                  </button></div>
              </div>
            )}
            {extensions.length === 0 ? (
              <EmptyState message="No extension has been requested for this breach." />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Requested</th><th>Until</th><th>Reason</th><th>Status</th><th>Board ref</th><th>Granted until</th><th /></tr></thead>
                  <tbody>
                    {extensions.map((x) => (
                      <tr key={x.id}>
                        <td>{formatDateTime(x.requested_at)}</td>
                        <td>{formatDateTime(x.requested_until)}</td>
                        <td>{x.reason}</td>
                        <td><Badge status={x.status === 'GRANTED' ? 'ACTIVE' : x.status === 'REFUSED' ? 'DENIED' : 'PENDING'}>{x.status}</Badge></td>
                        <td className="mono text-xs">{x.board_reference || '—'}</td>
                        <td>{x.granted_until ? formatDateTime(x.granted_until) : '—'}</td>
                        <td>
                          {canManage && x.status === 'REQUESTED' && (
                            <div className="flex" style={{ gap: 6 }}>
                              <button className="btn btn-sm" onClick={() => {
                                const ref = window.prompt('Board reference for the granted extension')
                                if (ref == null) return
                                run(() => breachesApi.decideExtension(breachRef, x.id, {
                                  status: 'GRANTED', granted_until: x.requested_until, board_reference: ref,
                                }), 'Extension recorded as granted.')
                              }}>Granted</button>
                              <button className="btn btn-ghost-danger btn-sm" onClick={() =>
                                run(() => breachesApi.decideExtension(breachRef, x.id, { status: 'REFUSED' }), 'Extension recorded as refused.')}>
                                Refused
                              </button>
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
        </div>
      )}

      {tab === 'timeline' && (
        <div className="card">
          <div className="card-header"><h3>Timeline</h3></div>
          <div className="card-body">
            <div className="timeline">
              {breach.timeline.map((e, i) => (
                <div className={`timeline-item ${String(e.event).includes('DEADLINE') ? 'warn' : String(e.event).includes('SENT') ? 'success' : ''}`} key={`${e.event}-${i}`}>
                  <div style={{ fontWeight: 600 }}>{String(e.event).replace(/_/g, ' ')}</div>
                  <div className="text-xs text-muted">{formatDateTime(e.at)}</div>
                  <div className="text-sm text-secondary">{e.detail}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === 'record' && (
        <div className="card">
          <div className="card-header"><h3>Register record</h3></div>
          <div className="card-body">
            <dl className="detail-grid">
              {([
                ['Occurred at', formatDateTime(breach.occurred_at)],
                ['Detected at', formatDateTime(breach.detected_at)],
                ['Became aware at', breach.aware_at ? formatDateTime(breach.aware_at) : 'not recorded'],
                ['Nature', breach.nature],
                ['Extent', breach.extent],
                ['Location', breach.location],
                ['Likely impact', breach.likely_impact],
                ['Likely consequences', breach.likely_consequences],
                ['Mitigation measures', breach.mitigation_measures],
                ['Safety measures for principals', breach.safety_measures],
                ['Cause', breach.cause],
                ['Findings on the actor', breach.findings_on_actor],
                ['Remedial measures', breach.remedial_measures],
                ['CERT-In reportable', breach.cert_in_reportable ? 'Yes' : `No — ${breach.cert_in_not_reportable_reason || 'no reason recorded'}`],
                ['Contact', [breach.contact_name, breach.contact_email, breach.contact_phone].filter(Boolean).join(' · ')],
                ['Registered by', `${breach.created_by} on ${formatDateTime(breach.created_at)}`],
              ] as const).map(([k, v]) => (
                <div className="detail-item" key={k}><dt>{k}</dt><dd>{v || <span className="text-muted">—</span>}</dd></div>
              ))}
            </dl>
          </div>
        </div>
      )}

      {confirmSpec && confirm && (
        <TypedConfirm
          open
          title={confirmSpec.title}
          phrase={confirm.phrase}
          intro={confirmSpec.intro}
          consequences={confirmSpec.consequences}
          confirmLabel={confirmSpec.confirmLabel}
          busy={busy}
          onConfirm={confirmSpec.onConfirm}
          onClose={() => setConfirm(null)}
        >
          {confirmSpec.body}
        </TypedConfirm>
      )}

      <Modal open={!!report} wide title={report ? `${report.report_type} — ${report.breach_ref}` : ''} onClose={() => setReport(null)}>
        {report && (
          <>
            <dl className="detail-grid mb">
              <div className="detail-item"><dt>Provision</dt><dd>{report.provision}</dd></div>
              <div className="detail-item">
                <dt>Deadline</dt>
                <dd>
                  {report.deadline_at
                    ? <><span className="clock-kind clock-kind-statutory">Statutory</span> {formatDateTime(report.deadline_at)}</>
                    : <span className="clock-kind clock-kind-internal">No fixed period</span>}
                </dd>
              </div>
              <div className="detail-item"><dt>Deadline basis</dt><dd>{report.deadline_basis}</dd></div>
              <div className="detail-item"><dt>Content hash</dt><dd className="mono text-xs">{report.content_hash}</dd></div>
            </dl>
            {report.missing_mandated_sections.length > 0 && (
              <div className="alert alert-error mb">
                <b>This report is missing sections the provision mandates:</b>{' '}
                {report.missing_mandated_sections.join(', ')}. Filing it in this state files an incomplete report.
              </div>
            )}
            <pre className="code-block" style={{ maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{report.content}</pre>
          </>
        )}
      </Modal>

      <Modal open={!!filingFor} title="Record the filing reference" onClose={() => setFilingFor(null)}
        footer={
          <>
            <button className="btn" onClick={() => setFilingFor(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={!filingRef.trim()} onClick={() => {
              if (!filingFor) return
              run(() => breachesApi.recordFiling(breachRef, filingFor.id, { filing_reference: filingRef, delivered: true }),
                'Filing reference recorded.').then((ok) => { if (ok) setFilingFor(null) })
            }}>Record</button>
          </>
        }>
        <p className="text-sm text-secondary">
          The acknowledgement number the regulator returned. This is the evidence that the filing arrived, separate
          from the register&rsquo;s own record that it was sent.
        </p>
        <div className="form-group">
          <label>Filing reference</label>
          <input className="input" value={filingRef} onChange={(e) => setFilingRef(e.target.value)} />
        </div>
      </Modal>
    </div>
  )
}

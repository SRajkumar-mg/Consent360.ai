import { useEffect, useState } from 'react'
import { breachesApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, Modal, PageHead, Spinner, StatCard, TableSkeleton,
  formatDateTime, formatDate, useToast, daysUntil,
} from '../components/ui'
import {
  IconPlus, IconAlert, IconCheck, IconClock, IconShield,
} from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Breach, BreachNotification, BoardReport } from '../types'

const EMPTY_FORM = {
  tenant_id: 1,
  breach_type: 'CONFIDENTIALITY',
  detected_at: '',
  aware_at: '',
  nature: '',
  extent: '',
  timing: '',
  location: '',
  likely_impact: '',
  cause: '',
  mitigation: '',
  remedial_measures: '',
  findings_on_actor: '',
}

const STATUS_FLOW = ['OPEN', 'INVESTIGATING', 'RESOLVED'] as const

function statusBadge(status: string) {
  const s = status.toUpperCase()
  if (s === 'RESOLVED') return 'b-success'
  if (s === 'OPEN') return 'b-danger'
  if (s === 'INVESTIGATING') return 'b-warning'
  return 'b-info'
}

function notifStatusBadge(status: string) {
  const s = status.toUpperCase()
  if (s === 'SENT' || s === 'DELIVERED') return 'b-success'
  if (s === 'PENDING') return 'b-warning'
  if (s === 'OVERDUE' || s === 'MISSED') return 'b-danger'
  return 'b-info'
}

function toLocalInput(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function BreachesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('user.manage')

  const [breaches, setBreaches] = useState<Breach[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)

  const [detailBreach, setDetailBreach] = useState<Breach | null>(null)
  const [detailTab, setDetailTab] = useState<'info' | 'notifications' | 'report'>('info')
  const [notifications, setNotifications] = useState<BreachNotification[]>([])
  const [boardReport, setBoardReport] = useState<BoardReport | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = async () => {
    const r = await breachesApi.list()
    setBreaches(r.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setShowCreate(true)
  }

  const closeCreate = () => {
    setShowCreate(false)
  }

  const saveBreach = async () => {
    if (!form.detected_at || !form.aware_at) {
      toast('warning', 'Detected at and aware at are required')
      return
    }
    setSaving(true)
    try {
      await breachesApi.create({
        ...form,
        detected_at: new Date(form.detected_at).toISOString(),
        aware_at: new Date(form.aware_at).toISOString(),
      })
      toast('success', 'Breach reported successfully')
      closeCreate()
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const openDetail = async (breach: Breach) => {
    setDetailBreach(breach)
    setDetailTab('info')
    setNotifications([])
    setBoardReport(null)
    setDetailLoading(true)
    try {
      const [nRes, bRes] = await Promise.all([
        breachesApi.listNotifications(breach.id).catch(() => ({ data: [] as BreachNotification[] })),
        breachesApi.boardReport(breach.id).catch(() => ({ data: null as unknown as BoardReport })),
      ])
      setNotifications(nRes.data)
      setBoardReport(bRes.data)
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setDetailLoading(false)
    }
  }

  const closeDetail = () => {
    setDetailBreach(null)
    setBoardReport(null)
    setNotifications([])
  }

  const updateStatus = async (breach: Breach, newStatus: string) => {
    try {
      await breachesApi.updateStatus(breach.id, newStatus)
      toast('success', `Status updated to ${newStatus}`)
      setDetailBreach({ ...breach, status: newStatus })
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  const activeCount = breaches.filter((b) => b.status === 'OPEN' || b.status === 'INVESTIGATING').length
  const resolvedCount = breaches.filter((b) => b.status === 'RESOLVED').length

  return (
    <div>
      <PageHead
        title="Breach Register"
        subtitle="Data breach tracking, notifications, and compliance reporting"
        actions={
          canManage ? (
            <button className="btn btn-primary" onClick={openCreate}>
              <IconPlus size={14} /> Report Breach
            </button>
          ) : undefined
        }
      />

      <div className="stat-grid mb">
        <StatCard label="Total Breaches" value={breaches.length} icon={<IconAlert size={20} />} tone="primary" sub="All recorded" />
        <StatCard label="Active" value={activeCount} icon={<IconClock size={20} />} tone="warning" sub="OPEN / INVESTIGATING" />
        <StatCard label="Resolved" value={resolvedCount} icon={<IconCheck size={20} />} tone="success" sub="Closed breaches" />
        <StatCard label="Notifications" value={breaches.length > 0 ? breaches.length : 0} icon={<IconShield size={20} />} tone="info" sub="Sent to authorities" />
      </div>

      {breaches.length === 0 ? (
        <div className="card">
          <div className="card-body">
            <EmptyState
              title="No breaches recorded"
              message="When a data breach is identified, report it here for tracking, notification, and compliance reporting."
              icon={<IconAlert size={26} />}
            />
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="card-header">
            <h3>Recorded Breaches ({breaches.length})</h3>
          </div>
          <div className="card-body">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Reference No</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Detected At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {breaches.map((b) => {
                    const nextIdx = STATUS_FLOW.indexOf(b.status as typeof STATUS_FLOW[number])
                    const nextStatus = nextIdx >= 0 && nextIdx < STATUS_FLOW.length - 1 ? STATUS_FLOW[nextIdx + 1] : null
                    return (
                      <tr key={b.id}>
                        <td className="mono" style={{ fontWeight: 600 }}>{b.reference_no}</td>
                        <td>
                          <span className="chip">{b.breach_type}</span>
                        </td>
                        <td><Badge status={b.status}>{b.status}</Badge></td>
                        <td className="muted">{formatDateTime(b.detected_at)}</td>
                        <td>
                          <div className="flex" style={{ gap: 6 }}>
                            <button className="btn btn-sm" onClick={() => openDetail(b)}>View</button>
                            {canManage && nextStatus && (
                              <button
                                className="btn btn-sm btn-ghost"
                                onClick={() => updateStatus(b, nextStatus)}
                              >
                                {nextStatus === 'INVESTIGATING' ? 'Investigate' : 'Resolve'}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      <Modal
        open={showCreate}
        title="Report Data Breach"
        onClose={closeCreate}
        wide
        footer={
          <>
            <button className="btn" onClick={closeCreate}>Cancel</button>
            <button className="btn btn-primary" onClick={saveBreach} disabled={saving}>
              {saving ? 'Saving...' : 'Submit Report'}
            </button>
          </>
        }
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="form-group">
            <label>Tenant ID *</label>
            <input
              className="input"
              type="number"
              value={form.tenant_id}
              onChange={(e) => setForm({ ...form, tenant_id: Number(e.target.value) })}
            />
          </div>
          <div className="form-group">
            <label>Breach Type *</label>
            <select
              className="select"
              value={form.breach_type}
              onChange={(e) => setForm({ ...form, breach_type: e.target.value })}
            >
              <option value="CONFIDENTIALITY">Confidentiality</option>
              <option value="INTEGRITY">Integrity</option>
              <option value="AVAILABILITY">Availability</option>
            </select>
          </div>
          <div className="form-group">
            <label>Detected At *</label>
            <input
              className="input"
              type="datetime-local"
              value={form.detected_at}
              onChange={(e) => setForm({ ...form, detected_at: e.target.value })}
            />
          </div>
          <div className="form-group">
            <label>Aware At *</label>
            <input
              className="input"
              type="datetime-local"
              value={form.aware_at}
              onChange={(e) => setForm({ ...form, aware_at: e.target.value })}
            />
          </div>
        </div>

        <div className="form-group">
          <label>Nature</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Nature of the breach..."
            value={form.nature}
            onChange={(e) => setForm({ ...form, nature: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Extent</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Scope and extent of the breach..."
            value={form.extent}
            onChange={(e) => setForm({ ...form, extent: e.target.value })}
          />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="form-group">
            <label>Timing</label>
            <input
              className="input"
              placeholder="When the breach occurred"
              value={form.timing}
              onChange={(e) => setForm({ ...form, timing: e.target.value })}
            />
          </div>
          <div className="form-group">
            <label>Location</label>
            <input
              className="input"
              placeholder="Physical or logical location"
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
            />
          </div>
        </div>

        <div className="form-group">
          <label>Likely Impact</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Assessment of likely impact on data subjects..."
            value={form.likely_impact}
            onChange={(e) => setForm({ ...form, likely_impact: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Cause</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Root cause of the breach..."
            value={form.cause}
            onChange={(e) => setForm({ ...form, cause: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Mitigation</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Immediate mitigation measures taken..."
            value={form.mitigation}
            onChange={(e) => setForm({ ...form, mitigation: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Remedial Measures</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Long-term remedial measures..."
            value={form.remedial_measures}
            onChange={(e) => setForm({ ...form, remedial_measures: e.target.value })}
          />
        </div>

        <div className="form-group">
          <label>Findings on Actor</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Information about the threat actor..."
            value={form.findings_on_actor}
            onChange={(e) => setForm({ ...form, findings_on_actor: e.target.value })}
          />
        </div>
      </Modal>

      <Modal
        open={!!detailBreach}
        title={detailBreach ? `${detailBreach.reference_no} - Breach Details` : 'Breach Details'}
        onClose={closeDetail}
        wide
        footer={
          <div className="flex" style={{ gap: 8 }}>
            <button className="btn" onClick={closeDetail}>Close</button>
            {canManage && detailBreach && detailBreach.status !== 'RESOLVED' && (
              <button
                className="btn btn-danger"
                onClick={() => {
                  const nextIdx = STATUS_FLOW.indexOf(detailBreach.status as typeof STATUS_FLOW[number])
                  if (nextIdx >= 0 && nextIdx < STATUS_FLOW.length - 1) {
                    updateStatus(detailBreach, STATUS_FLOW[nextIdx + 1])
                  }
                }}
              >
                Update Status
              </button>
            )}
          </div>
        }
      >
        {detailLoading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : detailBreach ? (
          <>
            <div className="tabs">
              <button className={`tab ${detailTab === 'info' ? 'active' : ''}`} onClick={() => setDetailTab('info')}>Information</button>
              <button className={`tab ${detailTab === 'notifications' ? 'active' : ''}`} onClick={() => setDetailTab('notifications')}>Notifications</button>
              <button className={`tab ${detailTab === 'report' ? 'active' : ''}`} onClick={() => setDetailTab('report')}>Board Report</button>
            </div>

            {detailTab === 'info' && (
              <div>
                <div className="flex" style={{ gap: 12, marginBottom: 20, alignItems: 'center' }}>
                  <Badge status={detailBreach.status}>{detailBreach.status}</Badge>
                  <span className="chip">{detailBreach.breach_type}</span>
                </div>

                <div className="alert alert-info" style={{ marginBottom: 20 }}>
                  <IconAlert size={16} /> Reference: <strong>{detailBreach.reference_no}</strong>
                </div>

                <div className="detail-grid">
                  <div className="detail-item">
                    <div className="k">Detected At</div>
                    <div className="v">{formatDateTime(detailBreach.detected_at)}</div>
                  </div>
                  <div className="detail-item">
                    <div className="k">Aware At</div>
                    <div className="v">{formatDateTime(detailBreach.aware_at)}</div>
                  </div>
                  <div className="detail-item">
                    <div className="k">Created At</div>
                    <div className="v">{formatDateTime(detailBreach.created_at)}</div>
                  </div>
                  <div className="detail-item">
                    <div className="k">Tenant ID</div>
                    <div className="v">{detailBreach.tenant_id}</div>
                  </div>
                </div>

                {detailBreach.nature && (
                  <div style={{ marginTop: 16 }}>
                    <div className="detail-item">
                      <div className="k">Nature</div>
                      <div className="v">{detailBreach.nature}</div>
                    </div>
                  </div>
                )}

                {detailBreach.extent && (
                  <div style={{ marginTop: 12 }}>
                    <div className="detail-item">
                      <div className="k">Extent</div>
                      <div className="v">{detailBreach.extent}</div>
                    </div>
                  </div>
                )}

                {canManage && (
                  <div style={{ marginTop: 20 }}>
                    <h4 style={{ marginBottom: 12, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Status Timeline</h4>
                    <div className="timeline">
                      {STATUS_FLOW.map((s, i) => {
                        const currentIdx = STATUS_FLOW.indexOf(detailBreach.status as typeof STATUS_FLOW[number])
                        const isActive = i <= currentIdx
                        const isCurrent = i === currentIdx
                        return (
                          <div
                            key={s}
                            className={`timeline-item ${isActive ? (isCurrent ? '' : 'success') : 'muted'}`}
                          >
                            <div style={{ fontWeight: isCurrent ? 700 : 400 }}>
                              {s}
                              {isCurrent && <span style={{ marginLeft: 8, fontSize: '0.75rem', color: 'var(--text-muted)' }}>(current)</span>}
                            </div>
                            {i < STATUS_FLOW.length - 1 && isActive && i < currentIdx && (
                              <div className="text-xs muted" style={{ marginTop: 2 }}>Completed</div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {detailTab === 'notifications' && (
              <div>
                {notifications.length === 0 ? (
                  <EmptyState
                    title="No notifications sent"
                    message="Notifications to the supervisory authority and affected data subjects will appear here."
                    icon={<IconClock size={26} />}
                  />
                ) : (
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Recipient Type</th>
                          <th>Channel</th>
                          <th>Deadline</th>
                          <th>Sent At</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {notifications.map((n) => {
                          const days = daysUntil(n.deadline_at)
                          return (
                            <tr key={n.id}>
                              <td><span className="chip">{n.recipient_type}</span></td>
                              <td>{n.channel}</td>
                              <td>
                                <div>{formatDateTime(n.deadline_at)}</div>
                                {days !== null && days < 0 && (
                                  <div className="text-xs" style={{ color: 'var(--danger)', fontWeight: 600 }}>
                                    {Math.abs(days)} days overdue
                                  </div>
                                )}
                                {days !== null && days >= 0 && days <= 3 && (
                                  <div className="text-xs" style={{ color: 'var(--warning)', fontWeight: 600 }}>
                                    {days} days remaining
                                  </div>
                                )}
                              </td>
                              <td className="muted">{n.sent_at ? formatDateTime(n.sent_at) : '—'}</td>
                              <td><Badge status={n.status}>{n.status}</Badge></td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {detailTab === 'report' && (
              <div>
                {!boardReport ? (
                  <EmptyState
                    title="No board report available"
                    message="The board report will be generated once the breach investigation is underway."
                    icon={<IconShield size={26} />}
                  />
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                    {(['overview', 'impact', 'timeline', 'actions', 'compliance', 'next_steps'] as const).map((section) => {
                      const sectionData = boardReport[section]
                      const sectionLabels: Record<string, string> = {
                        overview: 'Overview',
                        impact: 'Impact Assessment',
                        timeline: 'Incident Timeline',
                        actions: 'Actions Taken',
                        compliance: 'Compliance Status',
                        next_steps: 'Next Steps',
                      }
                      const toneClass = section === 'impact' ? 'alert-warning' : 'alert-info'
                      return (
                        <div key={section} className={`alert ${toneClass}`}>
                          <div style={{ fontWeight: 700, marginBottom: 8, textTransform: 'uppercase', fontSize: '0.75rem', letterSpacing: '0.5px' }}>
                            {sectionLabels[section]}
                          </div>
                          {sectionData && typeof sectionData === 'object' ? (
                            <div className="detail-grid">
                              {Object.entries(sectionData).map(([key, val]) => (
                                <div key={key} className="detail-item">
                                  <div className="k">{key.replace(/_/g, ' ')}</div>
                                  <div className="v">{String(val)}</div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div>{String(sectionData)}</div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <EmptyState title="No data" message="Could not load breach details." />
        )}
      </Modal>
    </div>
  )
}

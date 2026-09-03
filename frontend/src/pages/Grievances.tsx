import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'

interface Grievance {
  id: number
  tenant_id: number
  customer_id: number
  reference_no: string
  category: string
  description: string
  status: string
  received_at: string
  acknowledged_at: string | null
  due_at: string | null
  escalated_at: string | null
  resolved_at: string | null
  resolution_summary: string
  feedback: string
  created_at: string
}

const CATEGORY_LABEL: Record<string, string> = {
  'UNAUTHORIZED_DISCLOSURE': 'Unauthorized Disclosure',
  'DATA_BREACH': 'Data Breach',
  'CONSENT_ISSUE': 'Consent Issue',
  'ACCESS_RIGHT': 'Access Right',
  'CORRECTION_RIGHT': 'Correction Right',
  'ERASURE_RIGHT': 'Erasure Right',
  'WITHDRAWAL': 'Withdrawal of Consent',
  'PROFILING_AUTOMATION': 'Profiling / Automation',
  'OTHER': 'Other',
}

function tone(status: string): string {
  if (status === 'RESOLVED') return 'b-success'
  if (status === 'ESCALATED') return 'b-danger'
  if (status === 'RECEIVED') return 'b-warning'
  return 'b-info'
}

export function GrievancesPage() {
  const toast = useToast()
  const [grievances, setGrievances] = useState<Grievance[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ status: '', overdue: '' })
  const [selected, setSelected] = useState<Grievance | null>(null)
  const [resolutionText, setResolutionText] = useState('')
  const [feedbackText, setFeedbackText] = useState('')

  const load = async () => {
    const params: Record<string, string> = {}
    if (filter.status) params.status = filter.status
    if (filter.overdue) params.overdue = filter.overdue
    try {
      const r = await api.get<Grievance[]>('/grievances', { params })
      setGrievances(r.data)
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [filter])

  const runAction = async (id: number, endpoint: string, body?: Record<string, string>) => {
    try {
      await api.put(`/grievances/${id}/${endpoint}`, body)
      toast('success', `Grievance ${endpoint}d`)
      setSelected(null)
      setResolutionText('')
      setFeedbackText('')
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const isOverdue = (g: Grievance) => {
    if (!g.due_at) return false
    return new Date(g.due_at) < new Date() && g.status !== 'RESOLVED'
  }

  const slaText = (g: Grievance) => {
    if (!g.due_at || g.status === 'RESOLVED') return null
    const remaining = new Date(g.due_at).getTime() - Date.now()
    const days = Math.floor(remaining / 86400000)
    return days < 0 ? `${Math.abs(days)}d overdue` : `${days}d left`
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead title="Grievances" subtitle="Grievance redressal queue with escalation, SLA, and feedback" />

      <div className="card mb">
        <div className="card-body flex" style={{ gap: 8, flexWrap: 'wrap' }}>
          <select className="select" style={{ flex: 1, minWidth: 130 }} value={filter.status}
            onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
            <option value="">All statuses</option>
            <option value="RECEIVED">Received</option>
            <option value="ACKNOWLEDGED">Acknowledged</option>
            <option value="ESCALATED">Escalated</option>
            <option value="RESOLVED">Resolved</option>
          </select>
          <select className="select" style={{ flex: 1, minWidth: 130 }} value={filter.overdue}
            onChange={(e) => setFilter({ ...filter, overdue: e.target.value })}>
            <option value="">Any SLA</option>
            <option value="true">Overdue</option>
            <option value="false">On time</option>
          </select>
          <span className="text-sm text-muted" style={{ alignSelf: 'center' }}>{grievances.length} grievances</span>
        </div>
      </div>

      {grievances.length === 0 ? (
        <div className="card"><div className="card-body">
          <EmptyState title="No grievances" message="Submitted grievances appear here for redressal." />
        </div></div>
      ) : (
        <div className="card"><div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Ref #</th><th>Category</th><th>Status</th><th>Customer</th><th>Received</th><th>SLA</th><th></th></tr>
            </thead>
            <tbody>
              {grievances.map((g) => (
                <tr key={g.id} style={isOverdue(g) ? { background: 'rgba(239,68,68,0.05)' } : undefined}>
                  <td className="mono">{g.reference_no}</td>
                  <td>{CATEGORY_LABEL[g.category] || g.category}</td>
                  <td><span className={`badge ${tone(g.status)}`}>{g.status.replace(/_/g, ' ')}</span></td>
                  <td className="mono">#{g.customer_id}</td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(g.received_at)}</td>
                  <td>
                    {isOverdue(g) ? <span style={{ color: '#ef4444', fontWeight: 600 }}>Overdue</span>
                      : <span className="text-xs text-muted">{slaText(g) ?? '—'}</span>}
                  </td>
                  <td>
                    <div className="flex" style={{ gap: 6 }}>
                      <button className="btn btn-sm" onClick={() => { setSelected({ ...g }); setResolutionText(g.resolution_summary || ''); setFeedbackText(g.feedback || '') }}>View</button>
                      {g.status === 'RECEIVED' && <button className="btn btn-sm btn-ghost" onClick={() => runAction(g.id, 'acknowledge')}>Ack</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div></div>
      )}

      <Modal open={!!selected} title={selected ? `Grievance ${selected.reference_no}` : ''}
        onClose={() => setSelected(null)} wide footer={<button className="btn" onClick={() => setSelected(null)}>Close</button>}>
        {selected && (
          <div>
            <div className="stat-grid mb" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>
              <div><div className="text-xs text-muted">Status</div><b>{selected.status.replace(/_/g, ' ')}</b></div>
              <div><div className="text-xs text-muted">Received</div><b>{formatDateTime(selected.received_at)}</b></div>
              <div><div className="text-xs text-muted">Due</div><b>{selected.due_at ? formatDateTime(selected.due_at) : '—'}</b></div>
            </div>
            <div className="text-sm muted mb">Category: {CATEGORY_LABEL[selected.category] || selected.category} · Customer #{selected.customer_id}</div>
            {selected.description && <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}><b>Complaint:</b><br />{selected.description}</p>}
            {selected.acknowledged_at && <div className="text-sm muted">Acknowledged {formatDateTime(selected.acknowledged_at)}</div>}
            {selected.escalated_at && <div className="text-sm" style={{ color: '#f59e0b' }}>Escalated to officer {formatDateTime(selected.escalated_at)}</div>}
            {selected.resolution_summary && <div className="mt"><b>Resolution:</b> <span className="text-sm">{selected.resolution_summary}</span></div>}
            {selected.feedback && <div className="mt"><b>Principal feedback:</b> <span className="text-sm">{selected.feedback}</span></div>}

            {selected.status !== 'RESOLVED' && (
              <div className="mt">
                <div className="form-group">
                  <label>Resolution Summary</label>
                  <textarea className="textarea" rows={3} value={resolutionText}
                    onChange={(e) => setResolutionText(e.target.value)} placeholder="Describe how the grievance was resolved…" />
                </div>
                <div className="flex" style={{ gap: 8 }}>
                  {selected.status !== 'ESCALATED' && (
                    <button className="btn btn-ghost" style={{ color: '#f59e0b' }} onClick={() => runAction(selected.id, 'escalate')}>Escalate to Officer</button>
                  )}
                  <button className="btn btn-primary" onClick={() => runAction(selected.id, 'resolve', { resolution_summary: resolutionText })}>Resolve</button>
                </div>
              </div>
            )}
            {selected.status === 'RESOLVED' && !selected.feedback && (
              <div className="mt">
                <div className="form-group">
                  <label>Principal Feedback</label>
                  <textarea className="textarea" rows={2} value={feedbackText} onChange={(e) => setFeedbackText(e.target.value)} placeholder="Satisfaction / feedback from principal…" />
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => runAction(selected.id, 'feedback', { feedback: feedbackText })}>Record Feedback</button>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

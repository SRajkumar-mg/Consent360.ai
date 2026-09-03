import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'

interface RightsRequest {
  id: number
  tenant_id: number
  customer_id: number
  type: string
  status: string
  received_at: string
  acknowledged_at: string | null
  due_at: string | null
  closed_at: string | null
  identity_verified_at: string | null
  resolution: string
  created_at: string
}

const TYPE_LABEL: Record<string, string> = {
  ACCESS: 'Access', CORRECTION: 'Correction', ERASURE: 'Erasure', NOMINATION: 'Nomination',
}

const CLOSED = ['RESOLVED', 'CLOSED', 'DENIED']

function tone(status: string): string {
  if (status === 'RESOLVED' || status === 'CLOSED') return 'b-success'
  if (status === 'DENIED') return 'b-danger'
  if (status === 'RECEIVED') return 'b-warning'
  return 'b-info'
}

export function RightsRequestsPage() {
  const toast = useToast()
  const [requests, setRequests] = useState<RightsRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ type: '', status: '', overdue: '' })
  const [selected, setSelected] = useState<RightsRequest | null>(null)
  const [resolutionText, setResolutionText] = useState('')

  const load = async () => {
    const params: Record<string, string> = {}
    if (filter.type) params.type = filter.type
    if (filter.status) params.status = filter.status
    if (filter.overdue) params.overdue = filter.overdue
    try {
      const r = await api.get<RightsRequest[]>('/rights-requests', { params })
      setRequests(r.data)
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [filter])

  const runAction = async (id: number, endpoint: string, body?: Record<string, string>) => {
    try {
      await api.put(`/rights-requests/${id}/${endpoint}`, body)
      toast('success', endpoint === 'resolve' ? 'Request resolved' : `Request ${endpoint}d`)
      setSelected(null)
      setResolutionText('')
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const isOverdue = (r: RightsRequest) => {
    if (!r.due_at) return false
    return new Date(r.due_at) < new Date() && !CLOSED.includes(r.status)
  }

  if (loading) return <Spinner />

  const openAction = (endpoint: 'resolve' | 'deny') => {
    if (!selected) return
    runAction(selected.id, endpoint, endpoint === 'resolve' ? { resolution: resolutionText } : undefined)
  }

  return (
    <div>
      <PageHead title="Rights Requests" subtitle="Data subject rights request queue with SLA tracking" />

      <div className="card mb">
        <div className="card-body flex" style={{ gap: 8, flexWrap: 'wrap' }}>
          <select className="select" style={{ flex: 1, minWidth: 130 }} value={filter.type}
            onChange={(e) => setFilter({ ...filter, type: e.target.value })}>
            <option value="">All types</option>
            {Object.entries(TYPE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select className="select" style={{ flex: 1, minWidth: 130 }} value={filter.status}
            onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
            <option value="">All statuses</option>
            <option value="RECEIVED">Received</option>
            <option value="ACKNOWLEDGED">Acknowledged</option>
            <option value="IN_PROGRESS">In Progress</option>
            <option value="RESOLVED">Resolved</option>
            <option value="DENIED">Denied</option>
          </select>
          <select className="select" style={{ flex: 1, minWidth: 130 }} value={filter.overdue}
            onChange={(e) => setFilter({ ...filter, overdue: e.target.value })}>
            <option value="">Any SLA</option>
            <option value="true">Overdue</option>
            <option value="false">On time</option>
          </select>
        </div>
      </div>

      {requests.length === 0 ? (
        <div className="card"><div className="card-body">
          <EmptyState title="No rights requests" message="Received requests appear here for SLA tracking." />
        </div></div>
      ) : (
        <div className="card"><div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>ID</th><th>Type</th><th>Status</th><th>Customer</th><th>Received</th><th>Due</th><th>SLA</th><th></th></tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.id} style={isOverdue(r) ? { background: 'rgba(239,68,68,0.05)' } : undefined}>
                  <td className="mono">#{r.id}</td>
                  <td><span className="badge b-primary">{TYPE_LABEL[r.type] || r.type}</span></td>
                  <td><Badge status={r.status}><span className={`badge ${tone(r.status)}`}>{r.status.replace(/_/g, ' ')}</span></Badge></td>
                  <td className="mono">Customer #{r.customer_id}</td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(r.received_at)}</td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{r.due_at ? formatDateTime(r.due_at) : '—'}</td>
                  <td>
                    {isOverdue(r) ? <span style={{ color: '#ef4444', fontWeight: 600 }}>Overdue</span>
                      : <span className="text-xs text-muted">On time</span>}
                  </td>
                  <td>
                    <div className="flex" style={{ gap: 6 }}>
                      <button className="btn btn-sm" onClick={() => { setSelected({ ...r }); setResolutionText(r.resolution || '') }}>View</button>
                      {r.status === 'RECEIVED' && <button className="btn btn-sm btn-ghost" onClick={() => runAction(r.id, 'acknowledge')}>Ack</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div></div>
      )}

      <Modal open={!!selected} title={selected ? `Request #${selected.id} — ${TYPE_LABEL[selected.type] || selected.type}` : ''}
        onClose={() => setSelected(null)} wide
        footer={
          selected && !CLOSED.includes(selected.status) ? (
            <>
              <button className="btn" onClick={() => setSelected(null)}>Cancel</button>
              <button className="btn btn-ghost" style={{ color: '#ef4444' }} onClick={() => openAction('deny')}>Deny</button>
              <button className="btn btn-primary" onClick={() => openAction('resolve')}>Resolve</button>
            </>
          ) : (
            <button className="btn" onClick={() => setSelected(null)}>Close</button>
          )
        }>
        {selected && (
          <div>
            <div className="stat-grid mb" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>
              <div><div className="text-xs text-muted">Status</div><b>{selected.status.replace(/_/g, ' ')}</b></div>
              <div><div className="text-xs text-muted">Received</div><b>{formatDateTime(selected.received_at)}</b></div>
              <div><div className="text-xs text-muted">Due</div><b>{selected.due_at ? formatDateTime(selected.due_at) : '—'}</b></div>
            </div>
            {selected.acknowledged_at && <div className="text-sm muted">Acknowledged {formatDateTime(selected.acknowledged_at)}</div>}
            {selected.identity_verified_at && <div className="text-sm muted">Identity verified {formatDateTime(selected.identity_verified_at)}</div>}
            {selected.resolution && <div className="mt"><b>Resolution:</b> <span className="text-sm">{selected.resolution}</span></div>}
            {!CLOSED.includes(selected.status) && (
              <div className="form-group mt">
                <label>Resolution / Notes</label>
                <textarea className="textarea" rows={3} value={resolutionText}
                  onChange={(e) => setResolutionText(e.target.value)} placeholder="Describe the outcome or denial reason…" />
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

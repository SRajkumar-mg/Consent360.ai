import { useEffect, useState } from 'react'
import { customersApi, purposesApi, rightsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'
import { IconPlus } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Customer, ErasureJob, Objection, Purpose } from '../types'

export function RightsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('rights.manage')
  const [objections, setObjections] = useState<Objection[]>([])
  const [jobs, setJobs] = useState<ErasureJob[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ customer_id: 0, purpose_id: 0 })

  const load = async () => {
    const [o, j, c, p] = await Promise.all([
      rightsApi.listObjections(), rightsApi.listErasureJobs(),
      customersApi.list(), purposesApi.list(),
    ])
    setObjections(o.data); setJobs(j.data); setCustomers(c.data); setPurposes(p.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const submitObjection = async () => {
    try {
      await rightsApi.createObjection({ customer_id: form.customer_id, purpose_id: form.purpose_id, source: 'admin' })
      toast('success', 'Objection recorded')
      setCreating(false); setForm({ customer_id: 0, purpose_id: 0 })
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const cancelJob = async (id: number) => {
    try {
      await rightsApi.cancelErasureJob(id)
      toast('success', 'Erasure job cancelled')
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const customerName = (id: number) => customers.find((c) => c.id === id)?.name || `Customer #${id}`
  const purposeName = (id: number) => purposes.find((p) => p.id === id)?.code || `Purpose #${id}`

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Rights & Erasure"
        subtitle="Principal objections (R1-08) and retention/erasure jobs (R1-06)"
        actions={canManage ? (
          <button className="btn-add" onClick={() => setCreating(true)}>
            <IconPlus size={16} /> Record objection
          </button>
        ) : undefined}
      />

      <div className="card card-hover mb">
        <div className="card-header"><h3>Erasure jobs</h3><span className="chip">{jobs.length}</span></div>
        {jobs.length === 0 ? <div className="card-body"><div className="text-sm text-muted">No erasure jobs.</div></div> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>ID</th><th>Customer</th><th>Trigger</th><th>Status</th><th>Scheduled for</th><th>Notice sent</th><th></th></tr></thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="mono">{j.id}</td>
                    <td>{customerName(j.customer_id)}</td>
                    <td><Badge status={j.trigger} /></td>
                    <td><Badge status={j.status} /></td>
                    <td className="text-sm muted">{formatDateTime(j.scheduled_for)}</td>
                    <td className="text-sm muted">{formatDateTime(j.notice_sent_at)}</td>
                    <td>{canManage && ['PENDING', 'NOTICE_SENT'].includes(j.status) && (
                      <button className="btn btn-sm" onClick={() => cancelJob(j.id)}>Cancel</button>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card card-hover mb">
        <div className="card-header"><h3>Objections</h3><span className="chip">{objections.length}</span></div>
        {objections.length === 0 ? <div className="card-body"><EmptyState title="No objections" message="Objections to s.7(a) gateway processing appear here." /></div> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>ID</th><th>Customer</th><th>Purpose</th><th>Source</th><th>Status</th><th>Objected at</th></tr></thead>
              <tbody>
                {objections.map((ob) => (
                  <tr key={ob.id}>
                    <td className="mono">{ob.id}</td>
                    <td>{customerName(ob.customer_id)}</td>
                    <td className="mono">{purposeName(ob.purpose_id)}</td>
                    <td className="text-sm muted">{ob.source}</td>
                    <td><Badge status={ob.status} /></td>
                    <td className="text-sm muted">{formatDateTime(ob.objected_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal open={creating} title="Record objection" onClose={() => setCreating(false)} wide>
        <div className="form-row">
          <div className="form-group"><label>Customer</label>
            <select className="select" value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: Number(e.target.value) })}>
              <option value={0}>Select customer</option>
              {customers.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.external_id})</option>)}
            </select>
          </div>
          <div className="form-group"><label>Purpose</label>
            <select className="select" value={form.purpose_id} onChange={(e) => setForm({ ...form, purpose_id: Number(e.target.value) })}>
              <option value={0}>Select purpose</option>
              {purposes.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.code})</option>)}
            </select>
          </div>
        </div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={submitObjection} disabled={!form.customer_id || !form.purpose_id}>Record objection</button>
        </div>
      </Modal>
    </div>
  )
}
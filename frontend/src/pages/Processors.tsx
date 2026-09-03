import { useEffect, useMemo, useState } from 'react'
import { processorsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, Modal, PageHead, Spinner, StatCard, useToast, formatDateTime,
} from '../components/ui'
import {
  IconAlert, IconCheck, IconPlus, IconRefresh, IconShield,
} from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Processor, ProcessorAlert } from '../types'

interface ProcessorForm {
  tenant_id: number
  name: string
  type: string
  country: string
  contact: string
  contract_ref: string
  webhook_url: string
  webhook_secret: string
}

const EMPTY_FORM: ProcessorForm = {
  tenant_id: 1,
  name: '',
  type: 'PROCESSOR',
  country: '',
  contact: '',
  contract_ref: '',
  webhook_url: '',
  webhook_secret: '',
}

export function ProcessorsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('user.manage')

  const [processors, setProcessors] = useState<Processor[]>([])
  const [coverage, setCoverage] = useState<{ total_processors: number; covered_processors: number; coverage_percentage: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<ProcessorForm>(EMPTY_FORM)
  const [detail, setDetail] = useState<Processor | null>(null)
  const [alerts, setAlerts] = useState<ProcessorAlert[]>([])
  const [alertsLoading, setAlertsLoading] = useState(false)
  const [escalating, setEscalating] = useState(false)

  const load = async () => {
    const [p, c] = await Promise.all([
      processorsApi.list().catch(() => ({ data: [] as Processor[] })),
      processorsApi.contractCoverage().catch(() => ({ data: null })),
    ])
    setProcessors(p.data)
    if (c.data) setCoverage(c.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const activeCount = useMemo(() => processors.filter((p) => p.is_active).length, [processors])
  const pendingAlerts = useMemo(() => 0, [processors])

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setCreating(true)
  }

  const save = async () => {
    if (!form.name.trim()) {
      toast('warning', 'Name is required')
      return
    }
    try {
      await processorsApi.create({
        tenant_id: form.tenant_id,
        name: form.name.trim(),
        type: form.type,
        country: form.country || undefined,
        contact: form.contact || undefined,
        contract_ref: form.contract_ref || undefined,
        webhook_url: form.webhook_url || undefined,
        webhook_secret: form.webhook_secret || undefined,
      })
      toast('success', 'Processor created')
      setCreating(false)
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const openDetail = async (p: Processor) => {
    setDetail(p)
    setAlertsLoading(true)
    setAlerts([])
    try {
      const r = await processorsApi.listAlerts(p.id)
      setAlerts(r.data)
    } catch {
      setAlerts([])
    } finally {
      setAlertsLoading(false)
    }
  }

  const checkEscalations = async () => {
    setEscalating(true)
    try {
      const r = await processorsApi.checkEscalations()
      toast('success', `${r.data.escalated} alert(s) escalated`)
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setEscalating(false)
    }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Processor Register"
        subtitle="Data processor management, contract tracking, and compliance alerts"
        actions={
          canManage && (
            <button className="btn btn-primary" onClick={openCreate}>
              <IconPlus size={14} /> Add Processor
            </button>
          )
        }
      />

      <div className="stat-grid mb">
        <StatCard label="Total Processors" value={processors.length} icon={<IconShield size={20} />} tone="primary" sub="registered processors" />
        <StatCard label="Active" value={activeCount} icon={<IconCheck size={20} />} tone="success" sub="currently active" />
        <StatCard label="Contract Coverage" value={coverage ? `${Math.round(coverage.coverage_percentage)}%` : '—'} icon={<IconShield size={20} />} tone="info" sub={coverage ? `${coverage.covered_processors} of ${coverage.total_processors} covered` : 'loading...'} />
        <StatCard label="Pending Alerts" value={pendingAlerts} icon={<IconAlert size={20} />} tone="warning" sub="requires attention" />
      </div>

      {coverage && (
        <div className="card mb">
          <div className="card-header">
            <div>
              <h3 style={{ margin: 0 }}>Contract Coverage Report</h3>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                {coverage.covered_processors} of {coverage.total_processors} processors have active contracts
              </div>
            </div>
            <button className="btn btn-sm" onClick={checkEscalations} disabled={escalating}>
              <IconRefresh size={13} /> {escalating ? 'Checking...' : 'Check Escalations'}
            </button>
          </div>
          <div className="card-body">
            <div style={{ background: 'var(--bg-secondary)', borderRadius: 8, height: 24, overflow: 'hidden' }}>
              <div
                style={{
                  height: '100%',
                  width: `${Math.round(coverage.coverage_percentage)}%`,
                  background: coverage.coverage_percentage >= 80 ? 'var(--success)' : coverage.coverage_percentage >= 50 ? 'var(--warning)' : 'var(--danger)',
                  borderRadius: 8,
                  transition: 'width 0.4s ease',
                }}
              />
            </div>
            <div className="flex-between" style={{ marginTop: 6 }}>
              <span className="text-xs text-muted">{Math.round(coverage.coverage_percentage)}% covered</span>
              <span className="text-xs text-muted">{coverage.total_processors - coverage.covered_processors} uncovered</span>
            </div>
          </div>
        </div>
      )}

      {processors.length === 0 ? (
        <div className="card">
          <div className="card-body">
            <EmptyState title="No processors registered" message="Add a data processor to begin tracking contracts and compliance." />
          </div>
        </div>
      ) : (
        <div className="card card-hover">
          <div className="card-header">
            <span className="chip">{processors.length} processors</span>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Country</th>
                  <th>Contract Ref</th>
                  <th>Contract Period</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {processors.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <div className="flex" style={{ gap: 10 }}>
                        <span className="avatar-sm">{(p.name || '?').slice(0, 2).toUpperCase()}</span>
                        <div>
                          <div style={{ fontWeight: 600 }}>{p.name}</div>
                          <div className="mono text-xs" style={{ color: 'var(--text-muted)' }}>{p.contact || '—'}</div>
                        </div>
                      </div>
                    </td>
                    <td><span className="badge b-info">{p.type}</span></td>
                    <td>{p.country || '—'}</td>
                    <td className="mono">{p.contract_ref || '—'}</td>
                    <td className="text-sm">
                      {p.contract_start ? formatDateTime(p.contract_start) : '—'}
                      {p.contract_start && p.contract_end ? ' \u2192 ' : ''}
                      {p.contract_end ? formatDateTime(p.contract_end) : ''}
                    </td>
                    <td><Badge status={p.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{p.is_active ? 'Active' : 'Inactive'}</Badge></td>
                    <td>
                      <button className="btn btn-sm" onClick={() => openDetail(p)}>
                        <IconAlert size={13} /> Alerts
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Modal
        open={creating}
        title="Add Processor"
        onClose={() => setCreating(false)}
        footer={
          <>
            <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={save} disabled={!form.name.trim()}>Create Processor</button>
          </>
        }
      >
        <div className="form-group">
          <label>Name *</label>
          <input className="input" placeholder="Acme Data Services" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Type</label>
          <select className="select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            <option value="PROCESSOR">PROCESSOR</option>
            <option value="CONTROLLER">CONTROLLER</option>
          </select>
        </div>
        <div className="form-group">
          <label>Country</label>
          <input className="input" placeholder="GB" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Contact</label>
          <input className="input" placeholder="privacy@example.com" value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Contract Reference</label>
          <input className="input" placeholder="DPA-2024-001" value={form.contract_ref} onChange={(e) => setForm({ ...form, contract_ref: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Webhook URL</label>
          <input className="input" placeholder="https://..." value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Webhook Secret</label>
          <input className="input" type="password" placeholder="Secret key" value={form.webhook_secret} onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })} />
        </div>
      </Modal>

      <Modal
        open={!!detail}
        title={`${detail?.name ?? ''} - Alerts`}
        onClose={() => { setDetail(null); setAlerts([]) }}
        wide
        footer={
          <button className="btn" onClick={() => { setDetail(null); setAlerts([]) }}>Close</button>
        }
      >
        {alertsLoading ? (
          <Spinner />
        ) : alerts.length === 0 ? (
          <EmptyState title="No alerts" message="This processor has no pending alerts." />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Retries</th>
                  <th>Sent</th>
                  <th>Acknowledged</th>
                  <th>Escalated</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td><span className="badge b-warning">{a.alert_type}</span></td>
                    <td><Badge status={a.status}>{a.status}</Badge></td>
                    <td>{a.retry_count}</td>
                    <td className="text-sm">{formatDateTime(a.sent_at)}</td>
                    <td className="text-sm">{formatDateTime(a.acknowledged_at)}</td>
                    <td className="text-sm">{formatDateTime(a.escalated_at)}</td>
                    <td className="text-sm">{formatDateTime(a.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
    </div>
  )
}

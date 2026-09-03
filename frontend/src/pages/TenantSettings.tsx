import { useEffect, useState } from 'react'
import { tenantsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, Modal, PageHead, Spinner, useToast } from '../components/ui'
import { IconPlus, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Tenant } from '../types'

const EMPTY: Omit<Tenant, 'id'> = {
  code: '', name: '', dpo_name: '', dpo_contact: '',
  withdraw_url: '', rights_url: '', grievance_url: '', board_complaint_url: '',
  grievance_response_days: 30, default_language: 'en', environment: 'development',
}

export function TenantSettingsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('tenant.manage')
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Tenant | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<Omit<Tenant, 'id'>>(EMPTY)

  const load = async () => {
    const r = await tenantsApi.list()
    setTenants(r.data)
  }

  useEffect(() => {
    setLoading(true)
    tenantsApi.list()
      .then((r) => setTenants(r.data))
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  const openCreate = () => { setForm(EMPTY); setCreating(true); setEditing(null) }
  const openEdit = (t: Tenant) => {
    const { id, ...rest } = t
    setForm({ ...rest, code: t.code })
    setEditing(t)
    setCreating(false)
  }

  const save = async () => {
    try {
      if (editing) {
        await tenantsApi.update(editing.id, form)
        toast('success', `Tenant ${editing.code} updated`)
      } else {
        await tenantsApi.create(form)
        toast('success', `Tenant ${form.code} created`)
      }
      setCreating(false); setEditing(null)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Tenant Settings"
        subtitle="Per-tenant DPO contact, mandatory notice links and disclosure configuration (R1-01)"
        actions={canManage ? (
          <button className="btn-add" onClick={openCreate}>
            <IconPlus size={16} /> New tenant
          </button>
        ) : undefined}
      />

      <div className="stat-grid mb">
        <div className="stat-card"><div style={{ minWidth: 0 }}><div className="stat-label">Tenants</div><div className="stat-value">{tenants.length}</div></div></div>
      </div>

      {tenants.map((t) => (
        <div className="card card-hover mb" key={t.id}>
          <div className="card-header">
            <div>
              <h3>{t.name} <span className="text-xs text-muted mono">({t.code})</span></h3>
              <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                DPO: <b>{t.dpo_name}</b> · Response <b>{t.grievance_response_days} days</b> · Lang <b>{t.default_language}</b> · <IconShield size={12} style={{ verticalAlign: 'middle' }} /> {t.environment}
              </div>
            </div>
            <div className="flex">
              <Badge status="ACTIVE">{t.code.toUpperCase()}</Badge>
              {canManage && <button className="btn btn-sm" onClick={() => openEdit(t)}>Settings</button>}
            </div>
          </div>
          <div className="card-body">
            <div className="detail-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              <div className="detail-item"><span className="k">DPO contact</span><div className="v">{t.dpo_contact || '—'}</div></div>
              <div className="detail-item"><span className="k">Withdraw</span><div className="v text-xs">{t.withdraw_url || '—'}</div></div>
              <div className="detail-item"><span className="k">Rights</span><div className="v text-xs">{t.rights_url || '—'}</div></div>
              <div className="detail-item"><span className="k">Grievance</span><div className="v text-xs">{t.grievance_url || '—'}</div></div>
              <div className="detail-item"><span className="k">Board complaint</span><div className="v text-xs">{t.board_complaint_url || '—'}</div></div>
            </div>
          </div>
        </div>
      ))}

      <Modal open={creating || !!editing} title={editing ? `Edit tenant — ${editing.code}` : 'Create tenant'} onClose={() => { setCreating(false); setEditing(null) }} wide>
        <div className="form-row">
          <div className="form-group"><label>Code</label><input className="input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} disabled={!!editing} /></div>
          <div className="form-group"><label>Name</label><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        </div>
        <div className="form-row">
          <div className="form-group"><label>DPO name</label><input className="input" value={form.dpo_name} onChange={(e) => setForm({ ...form, dpo_name: e.target.value })} /></div>
          <div className="form-group"><label>DPO contact</label><input className="input" value={form.dpo_contact} onChange={(e) => setForm({ ...form, dpo_contact: e.target.value })} /></div>
        </div>
        <div className="form-row">
          <div className="form-group"><label>Grievance response days (≤90)</label><input className="input" type="number" min={1} max={90} value={form.grievance_response_days} onChange={(e) => setForm({ ...form, grievance_response_days: Number(e.target.value) })} /></div>
          <div className="form-group"><label>Default language</label><input className="input" value={form.default_language} onChange={(e) => setForm({ ...form, default_language: e.target.value })} /></div>
          <div className="form-group"><label>Environment</label>
            <select className="select" value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
              {['development', 'production', 'staging'].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </div>
        </div>
        <div className="form-group"><label>Withdraw URL</label><input className="input" value={form.withdraw_url} onChange={(e) => setForm({ ...form, withdraw_url: e.target.value })} /></div>
        <div className="form-group"><label>Rights URL</label><input className="input" value={form.rights_url} onChange={(e) => setForm({ ...form, rights_url: e.target.value })} /></div>
        <div className="form-group"><label>Grievance URL</label><input className="input" value={form.grievance_url} onChange={(e) => setForm({ ...form, grievance_url: e.target.value })} /></div>
        <div className="form-group"><label>Board complaint URL</label><input className="input" value={form.board_complaint_url} onChange={(e) => setForm({ ...form, board_complaint_url: e.target.value })} /></div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => { setCreating(false); setEditing(null) }}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>{editing ? 'Save changes' : 'Create tenant'}</button>
        </div>
      </Modal>
    </div>
  )
}
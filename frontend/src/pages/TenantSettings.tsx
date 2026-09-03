import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { getErrorMessage } from '../api/client'
import { Modal, PageHead, Spinner, useToast } from '../components/ui'

interface TenantSettings {
  id: number
  tenant_code: string
  dpo_name: string
  dpo_contact: string
  withdraw_url: string
  rights_url: string
  grievance_url: string
  board_complaint_url: string
  grievance_response_days: number
  default_language: string
}

const EMPTY_FORM = {
  tenant_code: 'default',
  dpo_name: '',
  dpo_contact: '',
  withdraw_url: '',
  rights_url: '',
  grievance_url: '',
  board_complaint_url: '',
  grievance_response_days: 30,
  default_language: 'en',
}

const LANGUAGES = [
  ['en', 'English'], ['hi', 'Hindi'], ['ta', 'Tamil'], ['te', 'Telugu'],
  ['kn', 'Kannada'], ['ml', 'Malayalam'], ['bn', 'Bengali'], ['mr', 'Marathi'],
  ['gu', 'Gujarati'], ['pa', 'Punjabi'], ['or', 'Odia'], ['as', 'Assamese'],
  ['ne', 'Nepali'], ['ur', 'Urdu'], ['sd', 'Sindhi'], ['sa', 'Sanskrit'],
]

export function TenantSettingsPage() {
  const toast = useToast()
  const [form, setForm] = useState(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try {
      const r = await api.get<TenantSettings[]>('/tenant-settings')
      if (r.data.length > 0) {
        const s = r.data[0]
        setForm({
          tenant_code: s.tenant_code,
          dpo_name: s.dpo_name,
          dpo_contact: s.dpo_contact,
          withdraw_url: s.withdraw_url,
          rights_url: s.rights_url,
          grievance_url: s.grievance_url,
          board_complaint_url: s.board_complaint_url,
          grievance_response_days: s.grievance_response_days,
          default_language: s.default_language,
        })
      }
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const save = async () => {
    setSaving(true)
    try {
      const existing = await api.get<TenantSettings[]>('/tenant-settings')
      const method = existing.data.length > 0 ? 'put' : 'post'
      await (api as any)[method]('/tenant-settings', form)
      toast('success', 'Tenant settings saved')
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spinner />

  const set = (field: string, value: string | number) => setForm((f) => ({ ...f, [field]: value }))

  return (
    <div>
      <PageHead title="Tenant Settings" subtitle="DPO contact, mandatory compliance links, and platform defaults" />

      <div className="card">
        <div className="card-header"><h3>DPO Information</h3></div>
        <div className="card-body">
          <div className="grid-2">
            <div className="form-group">
              <label>DPO Name</label>
              <input className="input" value={form.dpo_name} onChange={(e) => set('dpo_name', e.target.value)} placeholder="Data Protection Officer" />
            </div>
            <div className="form-group">
              <label>DPO Contact</label>
              <input className="input" value={form.dpo_contact} onChange={(e) => set('dpo_contact', e.target.value)} placeholder="dpo@example.com" />
            </div>
          </div>
        </div>
      </div>

      <div className="card mt">
        <div className="card-header"><h3>Mandatory Compliance Links</h3></div>
        <div className="card-body">
          <div className="grid-2">
            <div className="form-group">
              <label>Withdraw Consent URL</label>
              <input className="input" value={form.withdraw_url} onChange={(e) => set('withdraw_url', e.target.value)} placeholder="https://..." />
            </div>
            <div className="form-group">
              <label>Rights Requests URL</label>
              <input className="input" value={form.rights_url} onChange={(e) => set('rights_url', e.target.value)} placeholder="https://..." />
            </div>
            <div className="form-group">
              <label>Grievance URL</label>
              <input className="input" value={form.grievance_url} onChange={(e) => set('grievance_url', e.target.value)} placeholder="https://..." />
            </div>
            <div className="form-group">
              <label>Board Complaint URL</label>
              <input className="input" value={form.board_complaint_url} onChange={(e) => set('board_complaint_url', e.target.value)} placeholder="https://..." />
            </div>
          </div>
        </div>
      </div>

      <div className="card mt">
        <div className="card-header"><h3>Platform Defaults</h3></div>
        <div className="card-body">
          <div className="grid-2">
            <div className="form-group">
              <label>Grievance Response Days (max 90)</label>
              <input className="input" type="number" min={1} max={90}
                value={form.grievance_response_days}
                onChange={(e) => set('grievance_response_days', Math.min(90, Math.max(1, parseInt(e.target.value) || 30)))} />
            </div>
            <div className="form-group">
              <label>Default Language</label>
              <select className="select" value={form.default_language} onChange={(e) => set('default_language', e.target.value)}>
                {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
              </select>
            </div>
          </div>
          <div className="form-group" style={{ maxWidth: 320 }}>
            <label>Tenant Code</label>
            <input className="input" value={form.tenant_code} onChange={(e) => set('tenant_code', e.target.value)} placeholder="default" />
          </div>
        </div>
      </div>

      <div className="mt">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}

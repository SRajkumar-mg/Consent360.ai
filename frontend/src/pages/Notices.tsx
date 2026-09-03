import { useEffect, useState } from 'react'
import { noticesApi, purposesApi, tenantsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'
import { IconPlus } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Notice, Purpose, Tenant } from '../types'

export function NoticesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('notice.manage')
  const [notices, setNotices] = useState<Notice[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [versioning, setVersioning] = useState<Notice | null>(null)
  const [form, setForm] = useState({ tenant_id: 0, purpose_id: 0 })
  const [vf, setVf] = useState({
    version_number: 1, language: 'en', title: '', body: '',
    retention_text: '', services_enabled: '', is_current: true,
  })

  const load = async () => {
    const [n, p, t] = await Promise.all([noticesApi.list(), purposesApi.list(), tenantsApi.list()])
    setNotices(n.data); setPurposes(p.data); setTenants(t.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const createNotice = async () => {
    try {
      await noticesApi.create({ tenant_id: form.tenant_id, purpose_id: form.purpose_id })
      toast('success', 'Notice created')
      setCreating(false)
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const addVersion = async () => {
    if (!versioning) return
    try {
      await noticesApi.addVersion({ notice_id: versioning.id, ...vf })
      toast('success', `Notice version v${vf.version_number} published`)
      setVersioning(null)
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const purposeName = (id: number) => purposes.find((p) => p.id === id)?.name || `Purpose #${id}`
  const tenantName = (id: number) => tenants.find((t) => t.id === id)?.code || `Tenant #${id}`
  const currentVersion = (n: Notice) => [...n.versions].sort((a, b) => b.version_number - a.version_number).find((v) => v.status === 'PUBLISHED')

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Notice Management"
        subtitle="Principal-facing consent notices per purpose, with versioned publish workflow (R1-04)"
        actions={canManage ? (
          <button className="btn-add" onClick={() => { setCreating(true) }}>
            <IconPlus size={16} /> New notice
          </button>
        ) : undefined}
      />

      {notices.length === 0 ? (
        <EmptyState title="No notices yet" message="Create a notice to give a purpose a published, principal-facing consent notice." />
      ) : notices.map((n) => {
        const sorted = [...n.versions].sort((a, b) => b.version_number - a.version_number)
        const current = currentVersion(n)
        const published = sorted.filter((v) => v.status === 'PUBLISHED' || v.status === 'RETIRED')
        return (
          <div className="card card-hover mb" key={n.id}>
            <div className="card-header">
              <div>
                <h3>{purposeName(n.purpose_id)}</h3>
                <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                  Tenant <b>{tenantName(n.tenant_id)}</b> · {n.versions.length} versions
                </div>
              </div>
              <div className="flex">
                {current && <Badge status="ACTIVE">{current.language.toUpperCase()}</Badge>}
                {canManage && (
                  <button className="btn btn-sm" onClick={() => {
                    setVersioning(n)
                    setVf({
                      version_number: (current?.version_number ?? 0) + 1,
                      language: current?.language ?? 'en', title: '', body: '',
                      retention_text: '', services_enabled: '', is_current: true,
                    })
                  }}>Add version</button>
                )}
              </div>
            </div>
            <div className="card-body">
              {current ? (
                <div>
                  <div className="detail-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                    <div className="detail-item"><span className="k">Current v{current.version_number} · {current.language}</span><div className="v">{current.title || '—'}</div></div>
                    <div className="detail-item"><span className="k">Services enabled</span><div className="v text-sm">{current.services_enabled || '—'}</div></div>
                    <div className="detail-item"><span className="k">Retention text</span><div className="v text-sm">{current.retention_text || '—'}</div></div>
                    <div className="detail-item"><span className="k">Effective from</span><div className="v text-sm">{formatDateTime(current.effective_from)}</div></div>
                  </div>
                  {current.body && <div className="text-sm text-secondary mt" style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>{current.body}</div>}
                </div>
              ) : (
                <div className="text-sm text-muted">No published version for this notice yet.</div>
              )}
              {published.map((v) => (
                <div className="text-xs text-muted mt" key={v.id}>
                  v{v.version_number} · {v.language} · <Badge status={v.status || 'DRAFT'} /> · {v.title || '(untitled)'}
                </div>
              ))}
            </div>
          </div>
        )
      })}

      <Modal open={creating} title="Create notice" onClose={() => setCreating(false)} wide>
        <div className="form-row">
          <div className="form-group"><label>Tenant</label>
            <select className="select" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: Number(e.target.value) })}>
              <option value={0}>Select tenant</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.code}</option>)}
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
          <button className="btn btn-primary" onClick={createNotice} disabled={!form.tenant_id || !form.purpose_id}>Create notice</button>
        </div>
      </Modal>

      <Modal open={!!versioning} title={`Add notice version — v${vf.version_number}`} onClose={() => setVersioning(null)} wide>
        <div className="form-row">
          <div className="form-group"><label>Language</label><input className="input" value={vf.language} onChange={(e) => setVf({ ...vf, language: e.target.value })} /></div>
          <div className="form-group"><label>Title</label><input className="input" value={vf.title} onChange={(e) => setVf({ ...vf, title: e.target.value })} /></div>
        </div>
        <div className="form-group"><label>Services enabled</label><input className="input" value={vf.services_enabled} onChange={(e) => setVf({ ...vf, services_enabled: e.target.value })} /></div>
        <div className="form-group"><label>Retention text</label><input className="input" value={vf.retention_text} onChange={(e) => setVf({ ...vf, retention_text: e.target.value })} /></div>
        <div className="form-group"><label>Body</label><textarea className="textarea" rows={5} value={vf.body} onChange={(e) => setVf({ ...vf, body: e.target.value })} /></div>
        <div className="form-group">
          <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={vf.is_current} onChange={(e) => setVf({ ...vf, is_current: e.target.checked })} />
            Publish as current version (retires prior for this language)
          </label>
        </div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => setVersioning(null)}>Cancel</button>
          <button className="btn btn-primary" onClick={addVersion} disabled={!vf.title.trim()}>Add &amp; publish version</button>
        </div>
      </Modal>
    </div>
  )
}
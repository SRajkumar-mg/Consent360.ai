import { useEffect, useState } from 'react'
import { noticesApi, purposesApi, tenantsApi, legalDocsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'
import { IconPlus } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import { LANGUAGES } from '../languages'
import type { Notice, Purpose, Tenant, LegalDocument } from '../types'

export function NoticesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('notice.manage')
  const [notices, setNotices] = useState<Notice[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [legalDocs, setLegalDocs] = useState<LegalDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [versioning, setVersioning] = useState<Notice | null>(null)

  const [form, setForm] = useState({
    tenant_id: 0, purpose_id: 0,
    version_number: 1, language: 'en', title: '', body: '',
    data_items: '', services_enabled: '', retention_text: '', consent_text: '',
    terms_document_id: 0, privacy_document_id: 0,
    status: 'DRAFT',
  })

  const [vf, setVf] = useState({
    version_number: 1, language: 'en', title: '', body: '',
    retention_text: '', services_enabled: '', consent_text: '',
    terms_document_id: 0, privacy_document_id: 0, is_current: true,
  })

  const load = async () => {
    const [n, p, t, ld] = await Promise.all([
      noticesApi.list(), purposesApi.list(), tenantsApi.list(),
      legalDocsApi.list().catch(() => ({ data: [] })),
    ])
    setNotices(n.data); setPurposes(p.data); setTenants(t.data); setLegalDocs(ld.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const createNotice = async () => {
    try {
      const dataItems = form.data_items ? JSON.parse(form.data_items) : []
      await noticesApi.create({
        tenant_id: form.tenant_id,
        purpose_id: form.purpose_id,
        version_number: form.version_number,
        language: form.language,
        title: form.title,
        body: form.body,
        data_items: dataItems,
        services_enabled: form.services_enabled,
        retention_text: form.retention_text,
        consent_text: form.consent_text,
        terms_document_id: form.terms_document_id || undefined,
        privacy_document_id: form.privacy_document_id || undefined,
        status: form.status,
      })
      toast('success', 'Notice created with version')
      setCreating(false)
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const addVersion = async () => {
    if (!versioning) return
    try {
      await noticesApi.addVersion({
        notice_id: versioning.id, ...vf,
        terms_document_id: vf.terms_document_id || undefined,
        privacy_document_id: vf.privacy_document_id || undefined,
      })
      toast('success', `Notice version v${vf.version_number} published`)
      setVersioning(null)
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const purposeName = (id: number) => purposes.find((p) => p.id === id)?.name || `Purpose #${id}`
  const tenantName = (id: number) => tenants.find((t) => t.id === id)?.code || `Tenant #${id}`
  const docLabel = (id: number | null | undefined) => {
    if (!id) return null
    const doc = legalDocs.find((d) => d.id === id)
    return doc ? `${doc.title} v${doc.version}` : `Doc #${id}`
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Notice Management"
        subtitle="Versioned consent notices per purpose with legal document associations (R1-04 / R4-001)"
        actions={canManage ? (
          <button className="btn-add" onClick={() => {
            setForm({
              tenant_id: 0, purpose_id: 0,
              version_number: 1, language: 'en', title: '', body: '',
              data_items: '', services_enabled: '', retention_text: '', consent_text: '',
              terms_document_id: 0, privacy_document_id: 0, status: 'DRAFT',
            })
            setCreating(true)
          }}>
            <IconPlus size={16} /> New notice
          </button>
        ) : undefined}
      />

      {notices.length === 0 ? (
        <EmptyState title="No notices yet" message="Create a notice with all required fields including title, content, consent text, and legal document associations." />
      ) : notices.map((n) => (
        <div className="card card-hover mb" key={n.id}>
          <div className="card-header">
            <div>
              <h3>{n.title || purposeName(n.purpose_id)}</h3>
              <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                Tenant <b>{tenantName(n.tenant_id)}</b> · v{n.version_number} · {n.language.toUpperCase()}
              </div>
            </div>
            <div className="flex">
              <Badge status={n.status || 'DRAFT'}>{n.language.toUpperCase()}</Badge>
              {canManage && (
                <button className="btn btn-sm" onClick={() => {
                  setVersioning(n)
                  setVf({
                    version_number: n.version_number + 1,
                    language: n.language, title: '', body: '',
                    retention_text: '', services_enabled: '', consent_text: '',
                    terms_document_id: n.terms_document_id || 0,
                    privacy_document_id: n.privacy_document_id || 0, is_current: true,
                  })
                }}>Add version</button>
              )}
            </div>
          </div>
          <div className="card-body">
            <div className="detail-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              <div className="detail-item"><span className="k">Title</span><div className="v">{n.title || '—'}</div></div>
              <div className="detail-item"><span className="k">Consent text</span><div className="v text-sm">{n.consent_text ? `${n.consent_text.slice(0, 100)}...` : '—'}</div></div>
              <div className="detail-item"><span className="k">Services enabled</span><div className="v text-sm">{n.services_enabled || '—'}</div></div>
              <div className="detail-item"><span className="k">Retention</span><div className="v text-sm">{n.retention_text || '—'}</div></div>
              <div className="detail-item"><span className="k">Terms & Conditions</span><div className="v text-sm">{docLabel(n.terms_document_id) || '—'}</div></div>
              <div className="detail-item"><span className="k">Privacy Policy</span><div className="v text-sm">{docLabel(n.privacy_document_id) || '—'}</div></div>
              <div className="detail-item"><span className="k">DPO Name</span><div className="v text-sm">{n.dpo_name || '—'}</div></div>
              <div className="detail-item"><span className="k">Effective from</span><div className="v text-sm">{formatDateTime(n.effective_from)}</div></div>
            </div>
            {n.body && <div className="text-sm text-secondary mt" style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>{n.body.slice(0, 300)}{n.body.length > 300 ? '...' : ''}</div>}
            <div className="text-xs text-muted mt">
              Hash: <span className="mono">{n.content_hash?.slice(0, 16) || '—'}...</span>
            </div>
          </div>
        </div>
      ))}

      <Modal open={creating} title="Create notice with version" onClose={() => setCreating(false)} wide>
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
        <div className="form-row">
          <div className="form-group"><label>Version number</label><input className="input" type="number" min={1} value={form.version_number} onChange={(e) => setForm({ ...form, version_number: Number(e.target.value) })} /></div>
          <div className="form-group"><label>Language</label>
            <select className="select" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn} ({l.code})</option>)}
            </select>
          </div>
          <div className="form-group"><label>Status</label>
            <select className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              <option value="DRAFT">Draft</option>
              <option value="PUBLISHED">Published</option>
            </select>
          </div>
        </div>
        <div className="form-group"><label>Title *</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
        <div className="form-group"><label>Consent text</label><textarea className="textarea" rows={2} value={form.consent_text} onChange={(e) => setForm({ ...form, consent_text: e.target.value })} placeholder="Text shown to data principal for consent action" /></div>
        <div className="form-group"><label>Body / Description</label><textarea className="textarea" rows={3} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></div>
        <div className="form-group"><label>Services enabled</label><input className="input" value={form.services_enabled} onChange={(e) => setForm({ ...form, services_enabled: e.target.value })} placeholder="Feature/service names enabled by this consent" /></div>
        <div className="form-group"><label>Retention text</label><input className="input" value={form.retention_text} onChange={(e) => setForm({ ...form, retention_text: e.target.value })} placeholder="How long data is retained" /></div>
        <div className="form-group"><label>Data items (JSON array)</label><input className="input" value={form.data_items} onChange={(e) => setForm({ ...form, data_items: e.target.value })} placeholder='[{"category_id": 1, "description": "name"}]' /></div>
        <div className="form-row">
          <div className="form-group"><label>Terms & Conditions document</label>
            <select className="select" value={form.terms_document_id} onChange={(e) => setForm({ ...form, terms_document_id: Number(e.target.value) })}>
              <option value={0}>None</option>
              {legalDocs.filter((d) => d.document_type === 'TERMS_AND_CONDITIONS').map((d) => <option key={d.id} value={d.id}>{d.title} v{d.version}</option>)}
            </select>
          </div>
          <div className="form-group"><label>Privacy Policy document</label>
            <select className="select" value={form.privacy_document_id} onChange={(e) => setForm({ ...form, privacy_document_id: Number(e.target.value) })}>
              <option value={0}>None</option>
              {legalDocs.filter((d) => d.document_type === 'PRIVACY_POLICY').map((d) => <option key={d.id} value={d.id}>{d.title} v{d.version}</option>)}
            </select>
          </div>
        </div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={createNotice} disabled={!form.tenant_id || !form.purpose_id || !form.title.trim()}>Create notice</button>
        </div>
      </Modal>

      <Modal open={!!versioning} title={`Add notice version — v${vf.version_number}`} onClose={() => setVersioning(null)} wide>
        <div className="form-row">
          <div className="form-group"><label>Language</label>
            <select className="select" value={vf.language} onChange={(e) => setVf({ ...vf, language: e.target.value })}>
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn} ({l.code})</option>)}
            </select>
          </div>
          <div className="form-group"><label>Version number</label><input className="input" type="number" min={1} value={vf.version_number} onChange={(e) => setVf({ ...vf, version_number: Number(e.target.value) })} /></div>
        </div>
        <div className="form-group"><label>Title *</label><input className="input" value={vf.title} onChange={(e) => setVf({ ...vf, title: e.target.value })} /></div>
        <div className="form-group"><label>Consent text</label><textarea className="textarea" rows={2} value={vf.consent_text} onChange={(e) => setVf({ ...vf, consent_text: e.target.value })} /></div>
        <div className="form-group"><label>Body</label><textarea className="textarea" rows={4} value={vf.body} onChange={(e) => setVf({ ...vf, body: e.target.value })} /></div>
        <div className="form-group"><label>Services enabled</label><input className="input" value={vf.services_enabled} onChange={(e) => setVf({ ...vf, services_enabled: e.target.value })} /></div>
        <div className="form-group"><label>Retention text</label><input className="input" value={vf.retention_text} onChange={(e) => setVf({ ...vf, retention_text: e.target.value })} /></div>
        <div className="form-row">
          <div className="form-group"><label>Terms & Conditions</label>
            <select className="select" value={vf.terms_document_id} onChange={(e) => setVf({ ...vf, terms_document_id: Number(e.target.value) })}>
              <option value={0}>None</option>
              {legalDocs.filter((d) => d.document_type === 'TERMS_AND_CONDITIONS').map((d) => <option key={d.id} value={d.id}>{d.title} v{d.version}</option>)}
            </select>
          </div>
          <div className="form-group"><label>Privacy Policy</label>
            <select className="select" value={vf.privacy_document_id} onChange={(e) => setVf({ ...vf, privacy_document_id: Number(e.target.value) })}>
              <option value={0}>None</option>
              {legalDocs.filter((d) => d.document_type === 'PRIVACY_POLICY').map((d) => <option key={d.id} value={d.id}>{d.title} v{d.version}</option>)}
            </select>
          </div>
        </div>
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

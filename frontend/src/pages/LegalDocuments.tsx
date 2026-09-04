import { useEffect, useState } from 'react'
import { legalDocsApi, tenantsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, formatDateTime, useToast } from '../components/ui'
import { IconPlus } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import { LANGUAGES } from '../languages'
import type { LegalDocument, Tenant } from '../types'

export function LegalDocumentsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('notice.manage')
  const [docs, setDocs] = useState<LegalDocument[]>([])
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [viewing, setViewing] = useState<LegalDocument | null>(null)

  const [form, setForm] = useState({
    tenant_id: 0, document_type: 'TERMS_AND_CONDITIONS' as string,
    version: '', title: '', content: '', language: 'en', status: 'DRAFT',
  })

  const load = async () => {
    const [d, t] = await Promise.all([legalDocsApi.list(), tenantsApi.list()])
    setDocs(d.data); setTenants(t.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const createDoc = async () => {
    try {
      await legalDocsApi.create(form)
      toast('success', 'Legal document created')
      setCreating(false)
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const publishDoc = async (id: number) => {
    try {
      await legalDocsApi.publish(id)
      toast('success', 'Document published (previous version retired)')
      load()
    } catch (e) { toast('error', getErrorMessage(e)) }
  }

  const tenantName = (id: number) => tenants.find((t) => t.id === id)?.code || `Tenant #${id}`
  const typeLabel = (t: string) => t === 'TERMS_AND_CONDITIONS' ? 'Terms & Conditions' : 'Privacy Policy'

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Legal Documents"
        subtitle="Versioned Terms & Conditions and Privacy Policy management (R4-001)"
        actions={canManage ? (
          <button className="btn-add" onClick={() => {
            setForm({ tenant_id: 0, document_type: 'TERMS_AND_CONDITIONS', version: '', title: '', content: '', language: 'en', status: 'DRAFT' })
            setCreating(true)
          }}>
            <IconPlus size={16} /> New document
          </button>
        ) : undefined}
      />

      {docs.length === 0 ? (
        <EmptyState title="No legal documents" message="Create versioned Terms & Conditions or Privacy Policy documents." />
      ) : docs.map((d) => (
        <div className="card card-hover mb" key={d.id}>
          <div className="card-header">
            <div>
              <h3>{d.title}</h3>
              <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                {typeLabel(d.document_type)} · v{d.version} · {tenantName(d.tenant_id)} · {d.language.toUpperCase()}
              </div>
            </div>
            <div className="flex">
              <Badge status={d.status}>{d.status}</Badge>
              {canManage && d.status !== 'PUBLISHED' && (
                <button className="btn btn-sm btn-primary" onClick={() => publishDoc(d.id)}>Publish</button>
              )}
              <button className="btn btn-sm" onClick={() => setViewing(d)}>View</button>
            </div>
          </div>
          <div className="card-body">
            <div className="text-sm text-secondary">
              {d.content ? `${d.content.slice(0, 200)}${d.content.length > 200 ? '...' : ''}` : 'No content'}
            </div>
            <div className="text-xs text-muted mt">
              Hash: <span className="mono">{d.content_hash?.slice(0, 16) || '—'}...</span>
              {d.effective_from && <> · Effective: {formatDateTime(d.effective_from)}</>}
            </div>
          </div>
        </div>
      ))}

      <Modal open={creating} title="Create legal document" onClose={() => setCreating(false)} wide>
        <div className="form-row">
          <div className="form-group"><label>Tenant</label>
            <select className="select" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: Number(e.target.value) })}>
              <option value={0}>Select tenant</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.code}</option>)}
            </select>
          </div>
          <div className="form-group"><label>Document type</label>
            <select className="select" value={form.document_type} onChange={(e) => setForm({ ...form, document_type: e.target.value })}>
              <option value="TERMS_AND_CONDITIONS">Terms & Conditions</option>
              <option value="PRIVACY_POLICY">Privacy Policy</option>
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-group"><label>Version *</label><input className="input" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder="e.g. 1.0" /></div>
          <div className="form-group"><label>Language</label>
            <select className="select" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn}</option>)}
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
        <div className="form-group"><label>Content</label><textarea className="textarea" rows={10} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="Full document content..." /></div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={createDoc} disabled={!form.tenant_id || !form.version.trim() || !form.title.trim()}>Create document</button>
        </div>
      </Modal>

      <Modal open={!!viewing} title={viewing?.title || 'Legal Document'} onClose={() => setViewing(null)} wide>
        {viewing && (
          <div>
            <div className="detail-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
              <div className="detail-item"><span className="k">Type</span><div className="v">{typeLabel(viewing.document_type)}</div></div>
              <div className="detail-item"><span className="k">Version</span><div className="v">{viewing.version}</div></div>
              <div className="detail-item"><span className="k">Language</span><div className="v">{viewing.language}</div></div>
              <div className="detail-item"><span className="k">Status</span><div className="v"><Badge status={viewing.status}>{viewing.status}</Badge></div></div>
            </div>
            <div className="divider" />
            <div className="text-sm" style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto', padding: 10, background: 'var(--bg-secondary, #f9fafb)', borderRadius: 6 }}>
              {viewing.content || '(No content)'}
            </div>
            <div className="text-xs text-muted mt">
              Content hash: <span className="mono">{viewing.content_hash}</span>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

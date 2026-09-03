import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, TableSkeleton, formatDateTime, useToast } from '../components/ui'

interface Notice {
  id: number
  tenant_id: number | null
  purpose_id: number | null
  title: string
  body: string
  language: string
  status: string
  version: number
  retention_text: string
  checklist_passed: boolean
  checklist_reviewer: string
  published_at: string | null
  created_at: string
}

interface PurposeOption { id: number; name: string }
interface Purpose { id: number; name: string; code: string }

const NOTICE_LANGUAGES = [
  ['en', 'English'], ['hi', 'Hindi'], ['ta', 'Tamil'], ['te', 'Telugu'],
  ['kn', 'Kannada'], ['ml', 'Malayalam'], ['bn', 'Bengali'],
]

const EMPTY = {
  title: '', body: '', language: 'en', status: 'DRAFT', purpose_id: null,
  retention_text: '', checklist_passed: false,
}

export function NoticesPage() {
  const toast = useToast()
  const [notices, setNotices] = useState<Notice[]>([])
  const [purposes, setPurposes] = useState<PurposeOption[]>([])
  const [loading, setLoading] = useState(true)

  const [editing, setEditing] = useState<Partial<Notice> | null>(null)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<Notice | null>(null)

  const load = async () => {
    try {
      const [n, p] = await Promise.all([api.get<Notice[]>('/notices'), api.get<Purpose[]>('/purposes')])
      setNotices(n.data)
      setPurposes(p.data.map((x: Purpose) => ({ id: x.id, name: x.name })))
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const createNew = () => { setEditing({ ...EMPTY }) }
  const closeEditor = () => { setEditing(null) }

  const save = async () => {
    if (!editing) return
    setSaving(true)
    try {
      if (editing.id) {
        await api.put(`/notices/${editing.id}`, editing)
      } else {
        await api.post('/notices', editing)
      }
      toast('success', editing.id ? 'Notice updated' : 'Notice created')
      closeEditor()
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const publish = async (n: Notice) => {
    try {
      await api.post(`/notices/${n.id}/publish`)
      toast('success', `Notice published (v${n.version + 1})`)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const doPreview = async (n: Notice) => {
    try {
      const r = await api.post<Notice>(`/notices/${n.id}/preview`)
      setPreview(r.data)
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  if (editing) {
    const set = (field: string, value: unknown) => setEditing((e) => (e ? { ...e, [field]: value } : e))
    return (
      <div>
        <PageHead title={editing.id ? 'Edit Notice' : 'New Notice'} subtitle="Create and manage consent notices" />
        <div className="card">
          <div className="card-body">
            <div className="grid-2">
              <div className="form-group">
                <label>Title</label>
                <input className="input" value={editing.title || ''} onChange={(e) => set('title', e.target.value)} placeholder="Notice title" />
              </div>
              <div className="form-group">
                <label>Language</label>
                <select className="select" value={editing.language || 'en'} onChange={(e) => set('language', e.target.value)}>
                  {NOTICE_LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                </select>
              </div>
            </div>
            <div className="form-group">
              <label>Purpose</label>
              <select className="select" value={editing.purpose_id ?? ''} onChange={(e) => set('purpose_id', e.target.value ? Number(e.target.value) : null)}>
                <option value="">Select purpose…</option>
                {purposes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label>Notice Body</label>
              <textarea className="textarea" rows={10} value={editing.body || ''} onChange={(e) => set('body', e.target.value)}
                placeholder="Full notice text shown to the principal. Include what data is collected, why, how it is processed, and rights." />
            </div>
            <div className="form-group">
              <label>Retention / Storage Text</label>
              <input className="input" value={editing.retention_text || ''} onChange={(e) => set('retention_text', e.target.value)} placeholder="How long data will be retained" />
            </div>
            <label className="flex text-sm" style={{ gap: 8, cursor: 'pointer', marginBottom: 16 }}>
              <input type="checkbox" checked={editing.checklist_passed || false} onChange={(e) => set('checklist_passed', e.target.checked)} />
              Dark-pattern checklist passed
            </label>
            <div className="flex" style={{ gap: 8 }}>
              <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save Notice'}</button>
              <button className="btn" onClick={closeEditor}>Cancel</button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHead title="Notices" subtitle="Multilingual consent notices with draft-to-publish workflow"
        actions={<button className="btn btn-primary" onClick={createNew}>Create Notice</button>} />

      {notices.length === 0 ? (
        <div className="card"><div className="card-body">
          <EmptyState title="No notices yet" message="Create a notice to display duty-to-inform text to principals." />
        </div></div>
      ) : (
        <div className="card"><div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Title</th><th>Language</th><th>Status</th><th>Version</th><th>Checklist</th><th>Created</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {notices.map((n) => (
                <tr key={n.id}>
                  <td><b>{n.title || '(untitled)'}</b></td>
                  <td><span className="badge b-info">{n.language.toUpperCase()}</span></td>
                  <td><Badge status={n.status}>{n.status}</Badge></td>
                  <td>v{n.version}</td>
                  <td>{n.checklist_passed ? <span className="badge b-success">Checklist ✓</span> : <span className="text-xs text-muted">Not reviewed</span>}</td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(n.created_at)}</td>
                  <td>
                    <div className="flex" style={{ gap: 6 }}>
                      <button className="btn btn-sm" onClick={() => setEditing({ ...n })}>Edit</button>
                      <button className="btn btn-sm btn-ghost" onClick={() => doPreview(n)}>Preview</button>
                      {n.status === 'DRAFT' && <button className="btn btn-sm btn-primary" onClick={() => publish(n)}>Publish</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div></div>
      )}

      <Modal open={!!preview} title="Notice Preview" onClose={() => setPreview(null)}
        footer={<button className="btn" onClick={() => setPreview(null)}>Close</button>}>
        {preview && (
          <div>
            <div className="text-xs text-muted" style={{ textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 }}>
              {preview.language.toUpperCase()} · v{preview.version}
            </div>
            <h4 style={{ marginBottom: 8 }}>{preview.title}</h4>
            <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, fontSize: 14 }}>{preview.body}</p>
            {preview.retention_text && <p className="text-sm muted" style={{ marginTop: 12 }}><b>Retention:</b> {preview.retention_text}</p>}
          </div>
        )}
      </Modal>
    </div>
  )
}

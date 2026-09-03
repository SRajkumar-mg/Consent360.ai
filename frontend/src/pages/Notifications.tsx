import { useEffect, useState } from 'react'
import { notificationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, StatCard, useToast, formatDateTime } from '../components/ui'
import { IconCheck, IconClock, IconNotification, IconPlus, IconRefresh } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { Notification, NotificationTemplate } from '../types'

type Tab = 'pending' | 'templates'

export function NotificationsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const isAdmin = hasPermission('user.manage')

  const [tab, setTab] = useState<Tab>('pending')
  const [pending, setPending] = useState<Notification[]>([])
  const [templates, setTemplates] = useState<NotificationTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [retrying, setRetrying] = useState(false)

  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({
    tenant_id: 1,
    event_type: '',
    channel: 'EMAIL',
    language: 'en',
    subject: '',
    body: '',
  })

  const loadPending = () => notificationsApi.listPending().then((r) => setPending(r.data))
  const loadTemplates = () => notificationsApi.listTemplates().then((r) => setTemplates(r.data))

  useEffect(() => {
    setLoading(true)
    Promise.all([loadPending(), loadTemplates()])
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  const retryAll = async () => {
    setRetrying(true)
    try {
      const res = await notificationsApi.retry()
      toast('success', `${res.data.retried} notification${res.data.retried === 1 ? '' : 's'} retried`)
      await loadPending()
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setRetrying(false)
    }
  }

  const createTemplate = async () => {
    if (!form.event_type.trim() || !form.body.trim()) return
    try {
      await notificationsApi.createTemplate(form)
      toast('success', 'Template created')
      setShowCreate(false)
      setForm({ tenant_id: 1, event_type: '', channel: 'EMAIL', language: 'en', subject: '', body: '' })
      await loadTemplates()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const totalPending = pending.length
  const failed = pending.filter((n) => n.retry_count > 0).length
  const delivered = pending.filter((n) => n.status === 'DELIVERED').length

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title="Notifications"
        subtitle="Notification templates, delivery status, and retry management"
      />

      <div className="tabs" style={{ marginBottom: 20 }}>
        <button className={`tab ${tab === 'pending' ? 'active' : ''}`} onClick={() => setTab('pending')}>
          Pending Notifications
        </button>
        <button className={`tab ${tab === 'templates' ? 'active' : ''}`} onClick={() => setTab('templates')}>
          Templates
        </button>
      </div>

      {tab === 'pending' && (
        <>
          <div className="stat-grid mb">
            <StatCard label="Total Pending" value={totalPending} icon={<IconNotification size={20} />} tone="primary" sub="notifications awaiting delivery" />
            <StatCard label="Failed" value={failed} icon={<IconClock size={20} />} tone="danger" sub="with retry attempts" />
            <StatCard label="Delivered" value={delivered} icon={<IconCheck size={20} />} tone="success" sub="successfully sent" />
          </div>

          <div className="card card-hover">
            <div className="card-header">
              <h3>Pending Notifications</h3>
              {totalPending > 0 && (
                <button className="btn btn-primary btn-sm" onClick={retryAll} disabled={retrying}>
                  <IconRefresh size={13} /> Retry All
                </button>
              )}
            </div>
            {pending.length === 0 ? (
              <EmptyState message="No pending notifications" icon={<IconNotification size={26} />} />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Event Type</th>
                      <th>Channel</th>
                      <th>Reference</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pending.map((n) => (
                      <tr key={n.id}>
                        <td>{n.event_type}</td>
                        <td><Badge status={n.channel}>{n.channel}</Badge></td>
                        <td className="mono text-xs">{n.reference_type}/{n.reference_id}</td>
                        <td><Badge status={n.status}>{n.status}</Badge></td>
                        <td className="text-xs">{formatDateTime(n.created_at)}</td>
                        <td>
                          {n.retry_count > 0 && (
                            <span className="text-xs text-muted">{n.retry_count} retries</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {tab === 'templates' && (
        <div className="card card-hover">
          <div className="card-header">
            <h3>Notification Templates</h3>
            {isAdmin && (
              <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                <IconPlus size={13} /> Add Template
              </button>
            )}
          </div>
          {templates.length === 0 ? (
            <EmptyState message="No templates configured" icon={<IconNotification size={26} />} />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Event Type</th>
                    <th>Channel</th>
                    <th>Language</th>
                    <th>Subject</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {templates.map((t) => (
                    <tr key={t.id}>
                      <td>{t.event_type}</td>
                      <td><Badge status={t.channel}>{t.channel}</Badge></td>
                      <td>{t.language}</td>
                      <td>{t.subject || '—'}</td>
                      <td><Badge status={t.is_active ? 'ACTIVE' : 'INACTIVE'}>{t.is_active ? 'Active' : 'Inactive'}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <Modal open={showCreate} title="Create Notification Template" onClose={() => setShowCreate(false)}
        footer={<>
          <button className="btn" onClick={() => setShowCreate(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={createTemplate} disabled={!form.event_type.trim() || !form.body.trim()}>Create Template</button>
        </>}>
        <div className="form-group">
          <label>Event Type</label>
          <input className="input" value={form.event_type} onChange={(e) => setForm({ ...form, event_type: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Channel</label>
          <select className="select" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            <option value="EMAIL">Email</option>
            <option value="SMS">SMS</option>
            <option value="IN_APP">In-App</option>
          </select>
        </div>
        <div className="form-group">
          <label>Language</label>
          <input className="input" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Subject</label>
          <input className="input" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Body</label>
          <textarea className="textarea" value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
        </div>
      </Modal>
    </div>
  )
}

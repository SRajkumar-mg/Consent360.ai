/**
 * R2-08: the delivery and evidence records (`/records`).
 *
 * Three tables that answer three different questions and are deliberately not
 * merged:
 *
 *  - **Notification log** — did the message we owed a principal actually reach
 *    them? Delivery and acknowledgement are separate columns because they are
 *    separate facts; "sent" is not "received".
 *  - **Templates** — what would we send, in which language. The backend
 *    constrains `language` to the same 23 codes the console supports, so a
 *    template can never be authored for a language nothing can render.
 *  - **Consent receipts** — the ISO/IEC TS 27560-shaped artefact issued to the
 *    principal for each consent action, with its own signature. `valid` is
 *    re-derived on read, so a receipt whose payload no longer matches its
 *    signature is shown as invalid rather than merely listed.
 */
import { useCallback, useEffect, useState } from 'react'
import { notificationsApi, receiptsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { IconAlert, IconCheck, IconInbox, IconPlus, IconSearch } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import { LANGUAGES } from '../languages'
import type { ConsentReceipt, NotificationMetrics, NotificationRow, NotificationTemplate } from '../types'

type Tab = 'log' | 'templates' | 'receipts'

// Exactly app/models/entities.py::NOTIFICATION_EVENTS and the channel CHECK
// constraint. The template API validates both against these patterns.
const EVENT_TYPES = [
  'CONSENT_ACKNOWLEDGEMENT', 'WITHDRAWAL_CONFIRMATION', 'RENEWAL_REMINDER', 'PURPOSE_CHANGE_RECONSENT',
  'ERASURE_WARNING_48H', 'BREACH_NOTICE', 'REQUEST_STATUS', 'GRIEVANCE_STATUS', 'LEGACY_NOTICE',
  'PROCESSOR_ESCALATION', 'GRIEVANCE_ESCALATION',
]
const CHANNELS = ['EMAIL', 'SMS', 'IN_APP']
/** notifications.status CHECK constraint. */
const NOTIFICATION_STATUSES = ['PENDING', 'SENT', 'DELIVERED', 'FAILED', 'ACKNOWLEDGED']

export function RecordsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canReadLog = hasPermission('audit.view')
  const canManageTemplates = hasPermission('consent.manage')
  const canReadReceipts = hasPermission('consent.view')

  const [tab, setTab] = useState<Tab>('log')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [rows, setRows] = useState<NotificationRow[]>([])
  const [metrics, setMetrics] = useState<NotificationMetrics | null>(null)
  const [templates, setTemplates] = useState<NotificationTemplate[]>([])
  const [receipts, setReceipts] = useState<ConsentReceipt[]>([])

  const [logFilter, setLogFilter] = useState({ event_type: '', status: '', customer_external_id: '' })
  const [receiptFilter, setReceiptFilter] = useState('')
  const [receiptDetail, setReceiptDetail] = useState<ConsentReceipt | null>(null)

  const [templateForm, setTemplateForm] = useState<null | {
    id: number | null; event_type: string; channel: string; language: string
    subject: string; body_template: string; is_active: boolean
  }>(null)

  const load = useCallback(async () => {
    const tasks: Promise<unknown>[] = []
    if (canReadLog) {
      tasks.push(
        notificationsApi.list({
          limit: 200,
          event_type: logFilter.event_type || undefined,
          status: logFilter.status || undefined,
          customer_external_id: logFilter.customer_external_id || undefined,
        }).then((r) => setRows(r.data)),
        notificationsApi.metrics().then((r) => setMetrics(r.data)).catch(() => setMetrics(null)),
        notificationsApi.templates().then((r) => setTemplates(r.data)).catch(() => setTemplates([])),
      )
    }
    if (canReadReceipts) {
      tasks.push(
        receiptsApi.list(receiptFilter ? { customer_external_id: receiptFilter } : {})
          .then((r) => setReceipts(r.data)).catch(() => setReceipts([])),
      )
    }
    await Promise.all(tasks)
  }, [canReadLog, canReadReceipts, logFilter, receiptFilter])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  const run = async (fn: () => Promise<unknown>, message: string): Promise<boolean> => {
    setBusy(true); setRefusal('')
    try { await fn(); toast('success', message); await load(); return true }
    catch (e) { setRefusal(getErrorMessage(e)); return false }
    finally { setBusy(false) }
  }

  if (loading) return <Spinner />

  const invalidReceipts = receipts.filter((r) => r.valid === false).length

  return (
    <div>
      <PageHeading
        title="Delivery & receipts"
        subtitle="What was sent to principals, whether it arrived, the templates behind it, and the signed consent receipts issued in return"
      />

      <RefusalNotice title="The notifications API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      {metrics && (
        <div className="metric-grid mb">
          <MetricCard label="Attempted" value={metrics.notifications_total_attempted} tone="primary" icon={<IconInbox size={20} />}
            sub="messages this platform has tried to deliver" />
          <MetricCard label="Delivery rate" value={formatPct(metrics.notification_delivery_rate_pct)}
            tone={metrics.notification_delivery_rate_pct == null ? 'slate' : metrics.notification_delivery_rate_pct >= 95 ? 'success' : 'warning'}
            icon={<IconCheck size={20} />}
            sub={`${metrics.notifications_delivered} delivered · ${metrics.notifications_failed} failed`} />
          <MetricCard label="Acknowledgement rate" value={formatPct(metrics.notification_ack_rate_pct)}
            tone="info" icon={<IconCheck size={20} />}
            sub={`${metrics.notifications_acknowledged} acknowledged — delivery is not receipt`} />
          <MetricCard label="Receipts with a broken signature" value={invalidReceipts}
            tone={invalidReceipts ? 'danger' : 'success'} icon={<IconAlert size={20} />}
            sub={`${receipts.length} receipt(s) loaded`} />
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'log', label: 'Notification log', count: canReadLog ? rows.length : null },
          { id: 'templates', label: 'Templates', count: canReadLog ? templates.length : null },
          { id: 'receipts', label: 'Consent receipts', count: canReadReceipts ? receipts.length : null },
        ]}
      />

      {tab === 'log' && (
        !canReadLog ? <ReadOnlyBanner permission="audit.view" what="Reading the notification log" /> : (
          <div className="card">
            <div className="card-header">
              <h3>Notification log</h3>
              <div className="flex" style={{ gap: 8 }}>
                <select className="input" style={{ width: 'auto' }} value={logFilter.event_type}
                  onChange={(e) => setLogFilter({ ...logFilter, event_type: e.target.value })} aria-label="Event type">
                  <option value="">All events</option>
                  {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <select className="input" style={{ width: 'auto' }} value={logFilter.status}
                  onChange={(e) => setLogFilter({ ...logFilter, status: e.target.value })} aria-label="Status">
                  <option value="">All statuses</option>
                  {NOTIFICATION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
            {rows.length === 0 ? (
              <div className="card-body"><EmptyState message="No notification matches this filter." /></div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Created</th><th>Event</th><th>Principal</th><th>Channel</th><th>Lang</th><th>Subject</th><th>Status</th><th>Sent</th><th>Delivered</th><th>Acknowledged</th></tr></thead>
                  <tbody>
                    {rows.map((n) => (
                      <tr key={n.id}>
                        <td className="text-xs">{formatDateTime(n.created_at)}</td>
                        <td className="text-xs">{n.event_type}</td>
                        <td className="text-xs">{n.customer_id ? `#${n.customer_id}` : '—'}<div className="text-muted mono">{n.source_app}</div></td>
                        <td>{n.channel}</td>
                        <td className="mono text-xs">{n.language}</td>
                        <td className="text-sm">{n.subject}</td>
                        <td>
                          <Badge status={['DELIVERED', 'ACKNOWLEDGED'].includes(n.status) ? 'ACTIVE' : n.status === 'FAILED' ? 'DENIED' : 'PENDING'}>{n.status}</Badge>
                          {n.retry_count > 0 && <div className="text-xs text-muted">{n.retry_count} retries</div>}
                        </td>
                        <td className="text-xs">{formatDateTime(n.sent_at)}</td>
                        <td className="text-xs">{formatDateTime(n.delivered_at)}</td>
                        <td className="text-xs">{n.acknowledged_at ? formatDateTime(n.acknowledged_at) : <span className="text-muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      )}

      {tab === 'templates' && (
        !canReadLog ? <ReadOnlyBanner permission="audit.view" what="Reading notification templates" /> : (
          <div className="card">
            <div className="card-header">
              <h3>Notification templates</h3>
              {canManageTemplates && (
                <button className="btn btn-primary btn-sm" onClick={() => setTemplateForm({
                  id: null, event_type: EVENT_TYPES[0], channel: 'EMAIL', language: 'en',
                  subject: '', body_template: '', is_active: true,
                })}><IconPlus size={13} /> New template</button>
              )}
            </div>
            <div className="card-body">
              <p className="text-sm text-secondary">
                A template is looked up by event, channel and language. Where none exists for the principal&rsquo;s
                language the platform falls back to a module-level default rather than sending nothing — so an empty
                list here does not mean nothing is being sent.
              </p>
            </div>
            {templates.length === 0 ? (
              <div className="card-body">
                <EmptyState title="No template has been authored"
                  message="Every notification currently uses the built-in default copy for its event type." />
              </div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Event</th><th>Channel</th><th>Language</th><th>Subject</th><th>State</th><th>Updated</th><th /></tr></thead>
                  <tbody>
                    {templates.map((t) => (
                      <tr key={t.id}>
                        <td className="text-xs">{t.event_type}</td>
                        <td>{t.channel}</td>
                        <td>{LANGUAGES.find((l) => l.code === t.language)?.nameEn || t.language}</td>
                        <td className="text-sm">{t.subject}</td>
                        <td><Badge status={t.is_active ? 'ACTIVE' : 'EXPIRED'}>{t.is_active ? 'Active' : 'Inactive'}</Badge></td>
                        <td className="text-xs">{formatDateTime(t.updated_at)}<div className="text-muted">{t.created_by}</div></td>
                        <td>
                          {canManageTemplates && (
                            <button className="btn btn-sm" onClick={() => setTemplateForm({
                              id: t.id, event_type: t.event_type, channel: t.channel, language: t.language,
                              subject: t.subject, body_template: t.body_template, is_active: t.is_active,
                            })}>Edit</button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      )}

      {tab === 'receipts' && (
        !canReadReceipts ? <ReadOnlyBanner permission="consent.view" what="Reading consent receipts" /> : (
          <div className="card">
            <div className="card-header">
              <h3>Consent receipts</h3>
              <div className="search-row">
                <IconSearch size={15} />
                <input className="input search-input" placeholder="Filter by principal external id"
                  value={receiptFilter} onChange={(e) => setReceiptFilter(e.target.value)} />
              </div>
            </div>
            <div className="card-body">
              <p className="text-sm text-secondary">
                One signed artefact per consent action, shaped on ISO/IEC TS 27560:2023. <b>Signature</b> is
                re-derived when the row is read: a receipt whose stored payload no longer matches its signature is
                evidence that something changed after issue, which is why it is called out rather than hidden.
              </p>
            </div>
            {receipts.length === 0 ? (
              <div className="card-body"><EmptyState message="No receipt matches this filter." /></div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Receipt</th><th>Action</th><th>Consent</th><th>Principal</th><th>Source</th><th>Notice</th><th>Issued</th><th>Signature</th><th /></tr></thead>
                  <tbody>
                    {receipts.slice(0, 200).map((r) => (
                      <tr key={r.receipt_ref}>
                        <td className="mono text-xs">{r.receipt_ref}</td>
                        <td><Badge status={r.action === 'WITHDRAWN' ? 'DENIED' : 'ACTIVE'}>{r.action}</Badge></td>
                        <td>#{r.consent_id} v{r.consent_version}</td>
                        <td>#{r.customer_id}</td>
                        <td className="mono text-xs">{r.source_app}</td>
                        <td className="text-xs">{r.notice_version_id ? `notice v#${r.notice_version_id}` : <span className="text-muted">none pinned</span>}</td>
                        <td className="text-xs">{formatDateTime(r.issued_at)}</td>
                        <td>{r.valid === false
                          ? <span className="text-danger">INVALID</span>
                          : <span className="text-success">valid</span>}</td>
                        <td><button className="btn btn-sm" onClick={() => setReceiptDetail(r)}>Open</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {receipts.length > 200 && (
              <div className="card-body text-sm text-muted">
                Showing the first 200 of {receipts.length}. Filter by principal to narrow it.
              </div>
            )}
          </div>
        )
      )}

      <FooterRow left={metrics ? `${metrics.notifications_delivered} of ${metrics.notifications_total_attempted} notifications delivered` : ''} />

      <Modal open={!!receiptDetail} wide title={receiptDetail ? `Receipt ${receiptDetail.receipt_ref}` : ''} onClose={() => setReceiptDetail(null)}>
        {receiptDetail && (
          <>
            {receiptDetail.valid === false && (
              <div className="alert alert-error mb">
                <b>The signature does not match this payload.</b> The receipt was altered after it was issued, or the
                signing key changed. Either way it can no longer be relied on as evidence of what the principal was told.
              </div>
            )}
            <dl className="detail-grid mb">
              <div className="detail-item"><dt>Action</dt><dd>{receiptDetail.action}</dd></div>
              <div className="detail-item"><dt>Consent</dt><dd>#{receiptDetail.consent_id} v{receiptDetail.consent_version}</dd></div>
              <div className="detail-item"><dt>Issued</dt><dd>{formatDateTime(receiptDetail.issued_at)}</dd></div>
              <div className="detail-item"><dt>Payload hash</dt><dd className="mono text-xs">{receiptDetail.payload_hash}</dd></div>
            </dl>
            <h4 className="kpi-section-title">Receipt payload</h4>
            <pre className="code-block" style={{ maxHeight: 420, overflow: 'auto' }}>
              {JSON.stringify(receiptDetail.payload, null, 2)}
            </pre>
          </>
        )}
      </Modal>

      <Modal open={!!templateForm} wide title={templateForm?.id ? 'Edit template' : 'New notification template'}
        onClose={() => setTemplateForm(null)}
        footer={
          <>
            <button className="btn" onClick={() => setTemplateForm(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !templateForm?.body_template} onClick={async () => {
              if (!templateForm) return
              const ok = templateForm.id
                ? await run(() => notificationsApi.updateTemplate(templateForm.id as number, {
                  subject: templateForm.subject, body_template: templateForm.body_template, is_active: templateForm.is_active,
                }), 'Template updated.')
                : await run(() => notificationsApi.createTemplate({
                  event_type: templateForm.event_type, channel: templateForm.channel, language: templateForm.language,
                  subject: templateForm.subject, body_template: templateForm.body_template, is_active: templateForm.is_active,
                }), 'Template created.')
              if (ok) setTemplateForm(null)
            }}>Save</button>
          </>
        }>
        {templateForm && (
          <>
            <div className="form-row">
              <div className="form-group"><label>Event type</label>
                <select className="select" value={templateForm.event_type} disabled={!!templateForm.id}
                  onChange={(e) => setTemplateForm({ ...templateForm, event_type: e.target.value })}>
                  {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select></div>
              <div className="form-group"><label>Channel</label>
                <select className="select" value={templateForm.channel} disabled={!!templateForm.id}
                  onChange={(e) => setTemplateForm({ ...templateForm, channel: e.target.value })}>
                  {CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
                </select></div>
              <div className="form-group"><label>Language</label>
                <select className="select" value={templateForm.language} disabled={!!templateForm.id}
                  onChange={(e) => setTemplateForm({ ...templateForm, language: e.target.value })}>
                  {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn} — {l.nameNative}</option>)}
                </select></div>
            </div>
            <div className="form-group"><label>Subject</label>
              <input className="input" value={templateForm.subject} onChange={(e) => setTemplateForm({ ...templateForm, subject: e.target.value })} /></div>
            <div className="form-group"><label>Body template</label>
              <textarea className="textarea" rows={10} value={templateForm.body_template}
                onChange={(e) => setTemplateForm({ ...templateForm, body_template: e.target.value })} /></div>
            <div className="form-group">
              <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={templateForm.is_active}
                  onChange={(e) => setTemplateForm({ ...templateForm, is_active: e.target.checked })} />
                <span>Active</span>
              </label>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}

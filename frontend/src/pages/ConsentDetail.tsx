import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { consentsApi, sharingEventsApi } from '../api'
import { Badge, Spinner, formatDateTime, daysUntil } from '../components/ui'
import type { ConsentDetail, ConsentEvidence, ConsentHistory, SharingEvent } from '../types'

function timelineTone(h: ConsentHistory) {
  const to = h.to_status
  if (to === 'WITHDRAWN' || to === 'DENIED') return 'danger'
  if (to === 'EXPIRED') return 'muted'
  if (to === 'ACTIVE' || to === 'GRANTED' || to === 'RENEWED') return 'success'
  if (to === 'UPDATED') return 'warn'
  return ''
}

export function ConsentDetailPage() {
  const { consentId } = useParams()
  const [data, setData] = useState<ConsentDetail | null>(null)
  const [error, setError] = useState('')
  const [sharing, setSharing] = useState<SharingEvent[]>([])

  useEffect(() => {
    consentsApi.detail(Number(consentId)).then((r) => {
      setData(r.data)
      const id = r.data.consent.id
      if (id) sharingEventsApi.list(id).then((s) => setSharing(s.data)).catch(() => setSharing([]))
    }).catch(() => setError('Consent not found'))
  }, [consentId])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <Spinner />

  const c = data.consent

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="text-sm text-muted mb" style={{ marginBottom: 4 }}><Link to={`/customers/${c.customer_external_id}`}>← Back to consent dashboard</Link></div>
          <h1>Consent Details</h1>
          <div className="sub">
            <span className="mono">{c.purpose_code}</span> · {c.purpose_name} · {c.data_category_name} · {c.processing_activity_name}
          </div>
        </div>
        <Badge status={c.status} />
      </div>

      {c.re_consent_required && (
        <div className="alert alert-warning mb">
          <b>Re-consent required</b> — a material change was published to this purpose{' '}
          {c.re_consent_requested_at ? `on ${formatDateTime(c.re_consent_requested_at)}` : ''}. Processing is blocked
          until fresh consent is captured.
        </div>
      )}

      <div className="grid-2 mb">
        <div className="card">
          <div className="card-header"><h3>Consent Record</h3></div>
          <div className="card-body">
            <div className="detail-grid">
              <div className="detail-item"><span className="k">Purpose</span><div className="v">{c.purpose_name}</div></div>
              <div className="detail-item"><span className="k">Purpose version</span><div className="v">v{c.purpose_version}</div></div>
              <div className="detail-item"><span className="k">Data category</span><div className="v">{c.data_category_name}</div></div>
              <div className="detail-item"><span className="k">Processing activity</span><div className="v">{c.processing_activity_name}</div></div>
              <div className="detail-item"><span className="k">Consent version</span><div className="v">v{c.consent_version}</div></div>
              <div className="detail-item"><span className="k">Status</span><div className="v"><Badge status={c.status} /></div></div>
              <div className="detail-item"><span className="k">Granted at</span><div className="v">{formatDateTime(c.granted_at)}</div></div>
              <div className="detail-item"><span className="k">Expires at</span><div className="v">{formatDateTime(c.expires_at)}{c.expires_at && <span className="text-xs text-muted"> ({daysUntil(c.expires_at)} days)</span>}</div></div>
              <div className="detail-item"><span className="k">Withdrawn at</span><div className="v">{formatDateTime(c.withdrawn_at)}</div></div>
              <div className="detail-item"><span className="k">Denied at</span><div className="v">{formatDateTime(c.denied_at)}</div></div>
              <div className="detail-item"><span className="k">Collection method</span><div className="v">{c.collection_method}</div></div>
              <div className="detail-item"><span className="k">Source app</span><div className="v">{c.source_app || '—'}</div></div>
              <div className="detail-item"><span className="k">Applicable policy</span><div className="v">{c.policy_code ? `${c.policy_code} v${c.policy_version}` : '—'}</div></div>
              <div className="detail-item"><span className="k">Created</span><div className="v">{formatDateTime(c.created_at)}</div></div>
            </div>
            {c.consent_text && (
              <>
                <div className="divider" />
                <div className="detail-item"><span className="k">Consent text</span>
                  <div className="v" style={{ fontStyle: 'italic', color: 'var(--text-secondary)', marginTop: 6 }}>“{c.consent_text}”</div>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header"><h3>Consent Timeline</h3></div>
          <div className="card-body">
            <div className="timeline">
              {[...data.history].reverse().map((h) => (
                <div key={h.id} className={`timeline-item ${timelineTone(h)}`}>
                  <div className="flex-between">
                    <b style={{ fontSize: 13 }}>{h.action.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (x) => x.toUpperCase())}</b>
                    <span className="text-xs text-muted">{formatDateTime(h.created_at)}</span>
                  </div>
                  <div className="text-sm text-secondary mt-sm">
                    {h.from_status ? <Badge status={h.from_status} /> : <span className="muted">—</span>}
                    <span style={{ margin: '0 6px' }}>→</span>
                    <Badge status={h.to_status ?? h.from_status ?? '—'} />
                  </div>
                  {h.reason && <div className="text-xs text-muted mt-sm">{h.reason}</div>}
                  <div className="text-xs text-muted mt-sm">by {h.actor_username} · v{h.consent_version}{h.request_id ? ` · req ${h.request_id}` : ''}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>Consent Evidence</h3></div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Evidence Ref</th><th>Collected At</th><th>By</th><th>Method</th><th>Source</th><th>Purpose Ver</th><th>Policy Ver</th></tr></thead>
            <tbody>
              {data.evidence.length === 0 ? (
                <tr><td colSpan={7} className="empty">No evidence recorded yet — evidence is created when consent is granted or renewed</td></tr>
              ) : data.evidence.map((e: ConsentEvidence) => (
                <tr key={e.id}>
                  <td className="mono">{e.evidence_ref}</td>
                  <td>{formatDateTime(e.collected_at)}</td>
                  <td>{e.collected_by}</td>
                  <td>{e.collection_method}</td>
                  <td>{e.source_app}</td>
                  <td>v{e.purpose_version}</td>
                  <td>{e.policy_version ? `v${e.policy_version}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-2 mb" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-header"><h3>Consent Receipts</h3></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Receipt No</th><th>Method</th><th>Issued at</th><th>Payload hash</th></tr></thead>
              <tbody>
                {!data.receipts || data.receipts.length === 0 ? (
                  <tr><td colSpan={4} className="empty">No receipts — a receipt is generated on every grant/renewal</td></tr>
                ) : data.receipts.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.receipt_number}</td>
                    <td>{r.method}</td>
                    <td className="text-sm muted">{formatDateTime(r.issued_at)}</td>
                    <td className="mono text-xs">{r.payload_hash.slice(0, 16)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><h3>Data Sharing Events</h3></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Type</th><th>Processor</th><th>Occurred at</th></tr></thead>
              <tbody>
                {sharing.length === 0 ? (
                  <tr><td colSpan={3} className="empty">No sharing events logged for this consent</td></tr>
                ) : sharing.map((s) => (
                  <tr key={s.id}>
                    <td><Badge status={s.event_type} /></td>
                    <td className="mono">{s.processor_id}</td>
                    <td className="text-sm muted">{formatDateTime(s.occurred_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

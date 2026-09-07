import { useEffect, useState } from 'react'
import { auditApi, customersApi } from '../api'
import { Badge, EmptyState, FooterRow, Modal, PageHeading, TableSkeleton, formatDate, formatDateTime } from '../components/ui'
import { IconInbox } from '../components/icons'
import type { AuditEvent, Customer } from '../types'

export function AuditExplorerPage() {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [customers, setCustomers] = useState<Customer[]>([])
  const [selected, setSelected] = useState<AuditEvent | null>(null)

  const [filters, setFilters] = useState({
    customer_name: '',
    purpose_code: '',
    date_from: '',
    date_to: '',
  })

  const applyFilters = () => {
    setLoading(true)
    const params: Record<string, string | undefined> = {
      limit: '200',
      customer_name: filters.customer_name || undefined,
      purpose_code: filters.purpose_code || undefined,
      date_from: filters.date_from ? new Date(filters.date_from).toISOString() : undefined,
      date_to: filters.date_to ? new Date(filters.date_to).toISOString() : undefined,
    }
    auditApi.list(params).then((r) => setEvents(r.data)).catch(() => setEvents([])).finally(() => setLoading(false))
  }

  useEffect(() => {
    customersApi.list().then((r) => setCustomers(r.data)).catch(() => setCustomers([]))
    applyFilters()
  }, [])

  const hasFilters = Object.values(filters).some((v) => v !== '')

  return (
    <div>
      <PageHeading title="Audit Explorer" subtitle="Append-only audit trail of all important consent platform actions." />

      <div className="card mb">
        <div className="card-header">
          <b>Filter Audit Logs</b>
          {hasFilters && <button className="btn btn-sm btn-ghost" onClick={() => { setFilters({ customer_name: '', purpose_code: '', date_from: '', date_to: '' }); setTimeout(applyFilters, 0) }}>Clear</button>}
        </div>
        <div className="card-body">
          <div className="filter-grid">
            <div className="form-group"><label>Customer Name</label>
              <select className="select" value={filters.customer_name} onChange={(e) => setFilters({ ...filters, customer_name: e.target.value })}>
                <option value="">All customers</option>
                {customers.map((c) => <option key={c.id} value={c.name}>{c.name} ({c.external_id})</option>)}
              </select>
            </div>
            <div className="form-group"><label>Purpose</label><input className="input" placeholder="analytics" value={filters.purpose_code} onChange={(e) => setFilters({ ...filters, purpose_code: e.target.value })} /></div>
            <div className="form-group"><label>From Date</label><input className="input" type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></div>
            <div className="form-group"><label>To Date</label><input className="input" type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <button className="btn btn-primary" onClick={applyFilters}>Apply filters</button>
          </div>
        </div>
      </div>

      <div className="card card-hover">
        <div className="results-head"><b>{events.length} events</b><span className="muted">matching current filters</span></div>
        {loading ? <TableSkeleton rows={7} cols={6} /> : events.length === 0 ? (
          <EmptyState icon={<IconInbox size={26} />} title="No events found" message="No audit events match the filters" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>When</th><th>Event</th><th>Customer ID</th><th>From → To</th><th>Version</th><th>Request ID</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                    <td className="muted">{formatDateTime(e.created_at)}</td>
                    <td><Badge status={e.event.replace(/^(CONSENT|PURPOSE|POLICY|DECISION|DATA_CATEGORY|ACTIVITY|USER|CUSTOMER|CONTEXT)_/, '')} /></td>
                    <td className="mono">{e.customer_external_id || '—'}</td>
                    <td className="text-sm">
                      {e.old_status ? <Badge status={e.old_status} /> : null}
                      {e.old_status && e.new_status ? <span className="muted"> → </span> : null}
                      {e.new_status ? <Badge status={e.new_status} /> : null}
                      {!e.old_status && !e.new_status ? <span className="muted">—</span> : null}
                    </td>
                    <td className="muted">{e.consent_version || e.policy_version ? <>{e.consent_version ? `v${e.consent_version}` : ''}{e.policy_version ? ` p${e.policy_version}` : ''}</> : '—'}</td>
                    <td className="mono text-xs">{e.request_id?.slice(0, 13) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <FooterRow left={`Directory created: ${events.length ? formatDate(events[events.length - 1].created_at) : '—'}`} />

      <Modal open={!!selected} title="Audit Event Detail" onClose={() => setSelected(null)} wide>
        {selected && (
          <div>
            <div className="mb" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Badge status={selected.event.replace(/^(CONSENT|PURPOSE|POLICY|DECISION|DATA_CATEGORY|ACTIVITY|USER|CUSTOMER|CONTEXT)_/, '')} />
              <span className="text-sm text-muted">{formatDateTime(selected.created_at)}</span>
            </div>
            <div className="detail-grid">
              <div className="detail-item"><span className="k">Event</span><div className="v">{selected.event}</div></div>
              <div className="detail-item"><span className="k">Actor</span><div className="v">{selected.actor_username} <span className="text-xs text-muted">({selected.actor_role})</span></div></div>
              <div className="detail-item"><span className="k">Customer</span><div className="v mono">{selected.customer_external_id || '—'}</div></div>
              <div className="detail-item"><span className="k">Purpose</span><div className="v">{selected.purpose_code || '—'}</div></div>
              <div className="detail-item"><span className="k">Consent ID</span><div className="v mono">{selected.consent_id || '—'}</div></div>
              <div className="detail-item"><span className="k">Policy</span><div className="v">{selected.policy_code ? `${selected.policy_code} v${selected.policy_version}` : '—'}</div></div>
              <div className="detail-item"><span className="k">Status transition</span><div className="v">{selected.old_status ? `${selected.old_status} → ${selected.new_status}` : '—'}</div></div>
              <div className="detail-item"><span className="k">Decision</span><div className="v">{selected.decision || '—'}</div></div>
              <div className="detail-item"><span className="k">Source app</span><div className="v">{selected.source_app || '—'}</div></div>
              <div className="detail-item"><span className="k">Request ID</span><div className="v mono text-xs">{selected.request_id || '—'}</div></div>
            </div>
            {selected.reason && (
              <>
                <div className="divider" />
                <div className="detail-item"><span className="k">Reason</span><div className="v" style={{ marginTop: 4 }}>{selected.reason}</div></div>
              </>
            )}
            {selected.details && Object.keys(selected.details).length > 0 && (
              <>
                <div className="divider" />
                <div className="detail-item"><span className="k">Metadata</span>
                  <code className="block mt-sm">{JSON.stringify(selected.details, null, 2)}</code>
                </div>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
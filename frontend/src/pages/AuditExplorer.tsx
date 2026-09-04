import { useEffect, useRef, useState } from 'react'
import { auditApi, auditChainApi, customersApi, purposesApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, Modal, PageHead, Spinner, TableSkeleton, formatDateTime, useToast } from '../components/ui'
import { IconChevron, IconDownload, IconFilter, IconInbox, IconSearch, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { AuditChainResult, AuditEvent, Customer, Purpose } from '../types'

export function AuditExplorerPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canExport = hasPermission('audit.export')

  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [customers, setCustomers] = useState<Customer[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(true)

  const [filters, setFilters] = useState({
    customer_name: '',
    purpose_code: '',
    date_from: '',
    date_to: '',
  })

  // Chain verification state
  const [checking, setChecking] = useState(false)
  const [chain, setChain] = useState<AuditChainResult | null>(null)

  // Export state
  const [exporting, setExporting] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!exportOpen) return
    const handler = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) setExportOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [exportOpen])

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
    purposesApi.list().then((r) => setPurposes(r.data)).catch(() => setPurposes([]))
    applyFilters()
  }, [])

  const hasFilters = Object.values(filters).some((v) => v !== '')

  const verify = async () => {
    setChecking(true)
    try {
      const r = await auditChainApi.verify()
      setChain(r.data)
      toast(r.data.verified ? 'success' : 'error',
        r.data.verified ? `Chain intact — ${r.data.checked} entries verified` : `Chain broken at entry #${r.data.first_broken}`)
    } catch (e) { toast('error', getErrorMessage(e)) }
    finally { setChecking(false) }
  }

  const doExportFormat = async (format: string) => {
    setExporting(true)
    try {
      const r = await auditApi.exportFormats({
        format,
        customer_name: filters.customer_name || undefined,
        purpose_code: filters.purpose_code || undefined,
        date_from: filters.date_from ? new Date(filters.date_from).toISOString() : undefined,
        date_to: filters.date_to ? new Date(filters.date_to).toISOString() : undefined,
      })
      const extMap: Record<string, string> = { csv: 'csv', excel: 'xlsx', pdf: 'pdf', json: 'json' }
      const mimeMap: Record<string, string> = {
        csv: 'text/csv', excel: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        pdf: 'application/pdf', json: 'application/json',
      }
      const blob = new Blob([r.data], { type: mimeMap[format] || 'application/octet-stream' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit-export-${new Date().toISOString().slice(0, 10)}.${extMap[format] || format}`
      a.click()
      URL.revokeObjectURL(url)
      toast('success', `Exported ${events.length} records as ${format.toUpperCase()}`)
    } catch (e) { toast('error', getErrorMessage(e)) }
    finally { setExporting(false) }
  }

  return (
    <div>
      <PageHead title="Audit Explorer" subtitle="Append-only audit trail of all important consent platform actions" />

      {canExport && (
        <div className="card card-hover mb">
          <div className="card-header">
            <div>
              <h3>Export audit records</h3>
              <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                Export {events.length} filtered records with content hash for integrity verification (R1-02 / R1-11)
              </div>
            </div>
            <div ref={exportRef} style={{ position: 'relative' }}>
              <button
                className="btn btn-primary"
                onClick={() => setExportOpen(!exportOpen)}
                disabled={exporting || events.length === 0}
              >
                <IconDownload size={16} /> {exporting ? 'Exporting…' : 'Export'}
              </button>
              {exportOpen && !exporting && (
                <div style={{
                  position: 'absolute', top: '100%', right: 0, marginTop: 4,
                  background: 'var(--card-bg, #fff)', border: '1px solid var(--border)',
                  borderRadius: 8, minWidth: 180, zIndex: 50, boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
                  overflow: 'hidden',
                }}>
                  {([
                    { fmt: 'csv', label: 'CSV', ext: '.csv', desc: 'Comma-separated values' },
                    { fmt: 'excel', label: 'Excel', ext: '.xlsx', desc: 'Spreadsheet with formatting' },
                    { fmt: 'pdf', label: 'PDF', ext: '.pdf', desc: 'Formatted audit report' },
                    { fmt: 'json', label: 'JSON', ext: '.json', desc: 'Structured data with manifest' },
                  ] as const).map((opt) => (
                    <button
                      key={opt.fmt}
                      style={{
                        display: 'block', width: '100%', padding: '10px 14px',
                        background: 'transparent', border: 'none', cursor: 'pointer',
                        textAlign: 'left', fontSize: 13, color: 'var(--text)',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover-bg, #f3f4f6)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                      onClick={() => { setExportOpen(false); doExportFormat(opt.fmt) }}
                    >
                      <div style={{ fontWeight: 600 }}>{opt.label} <span className="text-muted" style={{ fontWeight: 400 }}>{opt.ext}</span></div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>{opt.desc}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="card mb card-hover">
        <div className="card-header" style={{ cursor: 'pointer' }} onClick={() => setFiltersOpen(!filtersOpen)}>
          <div className="flex"><IconFilter size={15} style={{ color: 'var(--text-muted)' }} /><b>Filters</b>
            <IconChevron size={15} style={{ color: 'var(--text-muted)', transform: filtersOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
          </div>
          {hasFilters && <button className="btn btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); setFilters({ customer_name: '', purpose_code: '', date_from: '', date_to: '' }); setTimeout(applyFilters, 0) }}>Clear</button>}
        </div>
        {filtersOpen && (
          <div className="card-body">
            <div className="grid-3">
              <div className="form-group"><label>Customer name</label>
                <select className="select" value={filters.customer_name} onChange={(e) => setFilters({ ...filters, customer_name: e.target.value })}>
                  <option value="">All customers</option>
                  {customers.map((c) => <option key={c.id} value={c.name}>{c.name} ({c.external_id})</option>)}
                </select>
              </div>
              <div className="form-group"><label>Purpose</label>
                <select className="select" value={filters.purpose_code} onChange={(e) => setFilters({ ...filters, purpose_code: e.target.value })}>
                  <option value="">All purposes</option>
                  {purposes.map((p) => <option key={p.id} value={p.code}>{p.name} ({p.code})</option>)}
                </select>
              </div>
              <div className="form-group"><label>From date</label><input className="input" type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></div>
              <div className="form-group"><label>To date</label><input className="input" type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></div>
              <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button className="btn btn-primary" style={{ width: '100%' }} onClick={applyFilters}><IconSearch size={14} /> Apply filters</button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="card card-hover mb">
        <div className="card-header">
          <span className="chip">{events.length} events</span>
        </div>
        {loading ? <TableSkeleton rows={7} cols={6} /> : events.length === 0 ? (
          <EmptyState icon={<IconInbox size={26} />} title="No events found" message="No audit events match the filters" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>When</th><th>Event</th><th>Customer</th><th>From → To</th><th>Version</th><th>Request ID</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                    <td className="muted">{formatDateTime(e.created_at)}</td>
                    <td><Badge status={e.event.replace(/^(CONSENT|PURPOSE|POLICY|DECISION|DATA_CATEGORY|ACTIVITY|USER|CUSTOMER|CONTEXT)_/, '')} /></td>
                    <td className="mono">{e.customer_external_id || '—'}</td>
                    <td className="text-sm">
                      {e.old_status ? <Badge status={e.old_status} /> : null}
                      {e.old_status && e.new_status ? ' → ' : null}
                      {e.new_status ? <Badge status={e.new_status} /> : null}
                      {!e.old_status && !e.new_status ? <span className="muted">—</span> : null}
                    </td>
                    <td className="muted">{e.consent_version ? `c${e.consent_version}` : ''}{e.policy_version ? ` p${e.policy_version}` : ''}</td>
                    <td className="mono text-xs">{e.request_id?.slice(0, 12) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card card-hover mb">
        <div className="card-header">
          <div>
            <h3>Chain integrity</h3>
            <div className="text-xs text-muted" style={{ marginTop: 3 }}>
              {chain
                ? <span className="mono">{chain.verified ? '✓ VERIFIED' : '✗ BROKEN'}</span>
                : 'Run a check to verify every audit row hashes to its predecessor'}
            </div>
          </div>
          <Badge status={chain ? (chain.verified ? 'ACTIVE' : 'EXPIRED') : 'REQUESTED'}>
            {chain ? (chain.verified ? 'Intact' : 'Tampered') : 'Not checked'}
          </Badge>
        </div>
        <div className="card-body">
          {chain ? (
            <div className="detail-grid">
              <div className="detail-item"><span className="k">Verified</span><div className="v">{chain.verified ? 'Yes' : 'No'}</div></div>
              <div className="detail-item"><span className="k">Entries checked</span><div className="v">{chain.checked}</div></div>
              <div className="detail-item"><span className="k">First broken entry</span><div className="v">{chain.first_broken || '—'}</div></div>
            </div>
          ) : null}
          <div className="mt" style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-primary" onClick={verify} disabled={checking}>
              <IconShield size={16} /> {checking ? 'Verifying…' : 'Verify chain'}
            </button>
          </div>
        </div>
      </div>

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

import { useState } from 'react'
import { auditChainApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, PageHead, Spinner, useToast } from '../components/ui'
import { IconDownload, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { AuditChainResult } from '../types'

export function AuditChainPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canExport = hasPermission('audit.export')
  const [checking, setChecking] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [chain, setChain] = useState<AuditChainResult | null>(null)
  const [range, setRange] = useState({ date_from: '', date_to: '' })

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

  const doExport = async () => {
    setExporting(true)
    try {
      const r = await auditChainApi.export({
        date_from: range.date_from ? new Date(range.date_from).toISOString() : undefined,
        date_to: range.date_to ? new Date(range.date_to).toISOString() : undefined,
      })
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit-ledger-${new Date().toISOString().slice(0, 10)}.ndjson`
      a.click()
      URL.revokeObjectURL(url)
      toast('success', 'Audit ledger exported (JSON Lines + chain marker)')
    } catch (e) { toast('error', getErrorMessage(e)) }
    finally { setExporting(false) }
  }

  return (
    <div>
      <PageHead
        title="Audit Ledger"
        subtitle="Tamper-evident hash chain verification and signed export for regulator handoff (R1-02 / R1-11)"
      />

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

      {canExport && (
        <div className="card card-hover mb">
          <div className="card-header">
            <div>
              <h3>Export ledger</h3>
              <div className="text-xs text-muted" style={{ marginTop: 3 }}>JSON Lines with trailing chain marker</div>
            </div>
          </div>
          <div className="card-body">
            <div className="grid-3">
              <div className="form-group"><label>From date</label><input className="input" type="date" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} /></div>
              <div className="form-group"><label>To date</label><input className="input" type="date" value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></div>
              <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button className="btn btn-primary" style={{ width: '100%' }} onClick={doExport} disabled={exporting}>
                  <IconDownload size={16} /> {exporting ? 'Exporting…' : 'Export .ndjson'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
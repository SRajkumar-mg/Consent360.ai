import { useEffect, useState } from 'react'
import { reportsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, PageHead, Spinner, StatCard, useToast } from '../components/ui'
import { IconCheck, IconShield } from '../components/icons'
import type { ComplianceEvidencePack, DecisionsReport, LawfulGatewayReport } from '../types'

const BASIS_LABELS: Record<string, string> = {
  CONSENT: 'Consent (s.4)',
  S7_A: 'S7(a) — has not objected',
  S7_B: 'S7(b) — Employment',
  S7_C: 'S7(c) — Prepare/negotiate contract',
  S7_D: 'S7(d) — Deemed consent (user sought)',
  S7_E: 'S7(e) — Benefit/state processing',
  S7_F: 'S7(f) — Legal proceedings',
  S7_G: 'S7(g) — Locate you / emergency',
  S7_H: 'S7(h) — Offences / security',
  S7_I: 'S7(i) — Other prescribed legitimate use',
}

export function ReportsPage() {
  const toast = useToast()
  const [gateway, setGateway] = useState<LawfulGatewayReport | null>(null)
  const [decisions, setDecisions] = useState<DecisionsReport | null>(null)
  const [pack, setPack] = useState<ComplianceEvidencePack | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      reportsApi.lawfulGateway(),
      reportsApi.decisions({}),
      reportsApi.complianceEvidencePack(),
    ]).then(([g, d, p]) => {
      setGateway(g.data); setDecisions(d.data); setPack(p.data)
    }).catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Spinner />

  const basisRows = gateway?.gateway ? Object.entries(gateway.gateway) : []
  const totalBasis = basisRows.reduce((n, [, arr]) => n + arr.length, 0)
  const consentCount = basisRows.filter(([k]) => k === 'CONSENT').reduce((n, [, a]) => n + a.length, 0)
  const nonConsent = totalBasis - consentCount

  return (
    <div>
      <PageHead title="Reports" subtitle="Lawful-basis gateway coverage, decision-engine latency/outcomes and regulator evidence pack" />

      <div className="stat-grid mb">
        <StatCard label="Activities" value={totalBasis} icon={<IconShield size={20} />} tone="primary" sub="Total purposes with a lawful basis" />
        <StatCard label="Consent-based" value={consentCount} icon={<IconCheck size={20} />} tone="info" sub="require explicit consent record" />
        <StatCard label="S7 non-consent" value={nonConsent} icon={<IconShield size={20} />} tone="purple" sub="lawful-use gateways" />
        <StatCard label="Decisions logged" value={decisions?.total_decisions ?? 0} icon={<IconCheck size={20} />} tone="success" sub={`p95 ${decisions?.p95_latency_ms ?? '—'} ms`} />
      </div>

      <div className="card card-hover mb">
        <div className="card-header"><h3>Lawful-basis gateway (records of processing — KPI K-10)</h3></div>
        <div className="card-body">
          {basisRows.length === 0 ? <div className="text-muted text-sm">No gateway data.</div> : basisRows.map(([basis, rows]) => (
            <div className="mb" key={basis}>
              <div className="text-sm" style={{ fontWeight: 600 }}>{BASIS_LABELS[basis] || basis}</div>
              <div className="flex mt-sm" style={{ flexWrap: 'wrap', gap: 8 }}>
                {rows.map((r) => (
                  <span key={r.code} className="badge b-primary">{r.name} {r.requires_consent ? '' : '(no-consent)'}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card card-hover mb">
        <div className="card-header"><h3>Decision outcomes (consent_decision_logs — KPI K-12)</h3></div>
        <div className="card-body">
          <div className="stat-grid mb">
            <StatCard label="Average latency" value={`${decisions?.avg_latency_ms ?? '—'} ms`} icon={<IconCheck size={20} />} tone="info" sub={`from ${decisions?.sample_count ?? 0} samples`} />
            <StatCard label="p95 latency" value={`${decisions?.p95_latency_ms ?? '—'} ms`} icon={<IconShield size={20} />} tone="primary" sub="95th percentile" />
          </div>
          <div className="flex" style={{ flexWrap: 'wrap', gap: 8 }}>
            {decisions?.decision_breakdown && Object.entries(decisions.decision_breakdown).map(([k, v]) => (
              <span key={k} className="badge b-purple">{k}: {v}</span>
            ))}
          </div>
        </div>
      </div>

      <div className="card card-hover mb">
        <div className="card-header"><h3>Compliance evidence pack (regulator submission)</h3></div>
        <div className="card-body">
          <div className="detail-grid">
            <div className="detail-item"><span className="k">Active purposes</span><div className="v">{pack?.purposes ?? 0}</div></div>
            <div className="detail-item"><span className="k">Active policies</span><div className="v">{pack?.policies_active ?? 0}</div></div>
            <div className="detail-item"><span className="k">Active consents</span><div className="v">{pack?.active_consents ?? 0}</div></div>
            <div className="detail-item"><span className="k">Evidence rows</span><div className="v">{pack?.evidence_rows ?? 0}</div></div>
            <div className="detail-item"><span className="k">Audit rows</span><div className="v">{pack?.audit_rows ?? 0}</div></div>
            <div className="detail-item"><span className="k">Ledger chain</span><div className="v">{(pack?.ledger_chain as { verified?: boolean })?.verified !== undefined
              ? ((pack?.ledger_chain as { verified?: boolean })?.verified ? 'Intact' : 'Broken')
              : '—'}</div></div>
          </div>
        </div>
      </div>
    </div>
  )
}
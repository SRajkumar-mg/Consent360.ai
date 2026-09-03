import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { dashboardApi, type KpiPoint, type KpiValue } from '../api'
import { Badge, PageHead, Skeleton, StatCard, formatDate } from '../components/ui'
import { IconAlert, IconCheck, IconClock, IconHistory, IconShield, IconUsers, IconX } from '../components/icons'
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { DashboardResponse } from '../types'

const PIE_COLORS = ['#16a34a', '#4f6ef7', '#d97706', '#dc2626', '#64748b', '#0284c7', '#7c3aed']

function ChartTip({ active, payload, label }: { active?: boolean; payload?: Array<{ name?: string; value?: number | string; color?: string; payload?: { fill?: string; color?: string } }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tip">
      {label != null && <div className="chart-tip-title">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="chart-tip-row">
          <span className="chart-tip-dot" style={{ background: p.color || p.payload?.fill || p.payload?.color || '#4f6ef7' }} />
          <span>{p.name}</span>
          <b style={{ marginLeft: 'auto', paddingLeft: 10 }}>{p.value}</b>
        </div>
      ))}
    </div>
  )
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [error, setError] = useState('')
  const [kpis, setKpis] = useState<Record<string, KpiValue> | null>(null)
  const [trend, setTrend] = useState<KpiPoint[]>([])

  useEffect(() => {
    dashboardApi.get().then((r) => setData(r.data)).catch(() => setError('Failed to load dashboard'))
  }, [])

  useEffect(() => {
    dashboardApi.kpis().then((r) => setKpis(r.data.kpis)).catch(() => setKpis(null))
    dashboardApi.kpiTrends('K-11', 90).then((r) => setTrend(r.data.points)).catch(() => setTrend([]))
  }, [])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <DashboardSkeleton />

  const m = data.metrics
  const pieData = data.status_distribution
    .filter((s) => s.count > 0)
    .map((s, i) => ({
      name: s.status.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()),
      value: s.count,
      color: PIE_COLORS[i % PIE_COLORS.length],
    }))

  return (
    <div>
      <PageHead title="Dashboard" subtitle="Real-time overview of consent posture across the organization" />

      <div className="card mb" style={{ background: 'linear-gradient(120deg, #131f42 0%, #1c2b5e 55%, #2a2a6b 100%)', borderColor: '#131f42' }}>
        <div className="card-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap', color: '#fff' }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.2, color: '#8aa2ff', marginBottom: 6 }}>Consent posture</div>
            <div style={{ fontSize: 22, fontWeight: 750, letterSpacing: '-0.4px' }}>
              {m.total_consents} consent records <span style={{ color: '#8aa2ff' }}>·</span> {m.active_consents} active
            </div>
            <div style={{ fontSize: 13, color: '#b9c4da', marginTop: 6 }}>
              across <b style={{ color: '#e6ebf8' }}>{m.total_purposes}</b> purposes and <b style={{ color: '#e6ebf8' }}>{m.total_policies}</b> policies
            </div>
          </div>
          <div className="flex" style={{ gap: 10, flexWrap: 'wrap' }}>
            <span className="chip" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.18)', color: '#e6ebf8' }}>
              <IconAlert size={13} style={{ color: '#fbbf24' }} /> {m.expiring_soon} expiring ≤ 30d
            </span>
            <span className="chip" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.18)', color: '#e6ebf8' }}>
              <IconUsers size={13} /> {m.total_customers} customers
            </span>
            <span className="chip" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.18)', color: '#e6ebf8' }}>
              <IconShield size={13} /> {m.denied_consents} denied
            </span>
          </div>
        </div>
      </div>

      <div className="stat-grid mb">
        <StatCard label="Total Customers" value={m.total_customers} icon={<IconUsers size={20} />} tone="primary" sub="in consent directory" />
        <StatCard label="Active Consents" value={m.active_consents} icon={<IconCheck size={20} />} tone="success" sub={`of ${m.total_consents} total records`} />
        <StatCard label="Granted" value={m.granted_consents} icon={<IconShield size={20} />} tone="info" sub="consent granted on record" />
        <StatCard label="Pending" value={m.pending_consents} icon={<IconClock size={20} />} tone="warning" sub={`${m.expiring_soon} expiring ≤ 30 days`} />
        <StatCard label="Withdrawn" value={m.withdrawn_consents} icon={<IconX size={20} />} tone="danger" sub="withdrawn by principal or admin" />
        <StatCard label="Expired" value={m.expired_consents} icon={<IconHistory size={20} />} tone="slate" sub="past validity period" />
      </div>

      {kpis && (
        <div className="card card-hover mb">
          <div className="card-header">
            <h3>Compliance KPIs (Phase 1)</h3>
            <span className="text-sm muted">Derived from live data · benchmark KPIs K-11 — K-20</span>
          </div>
          <div className="card-body">
            <div className="stat-grid">
              {Object.entries(kpis).map(([id, k]) => (
                <div key={id} className="card" style={{ padding: 16 }}>
                  <div className="text-sm muted" style={{ fontWeight: 600 }}>{id} · {k.name}</div>
                  <div style={{ fontSize: 24, fontWeight: 750, marginTop: 6 }}>{k.value}</div>
                  <div className="text-xs muted">{k.metric}</div>
                </div>
              ))}
            </div>
            {trend.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="text-sm muted" style={{ fontWeight: 600, marginBottom: 8 }}>K-11 consent coverage trend (90d)</div>
                <ResponsiveContainer width="100%" height={160}>
                  <LineChart data={trend}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef1f6" />
                    <XAxis dataKey="period" stroke="#8a97b3" fontSize={10} />
                    <YAxis allowDecimals={false} stroke="#8a97b3" fontSize={10} />
                    <Tooltip content={<ChartTip />} />
                    <Line type="monotone" dataKey="value" name="Coverage" stroke="#4f6ef7" strokeWidth={2} dot={{ r: 2 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="grid-2 mb">
        <div className="card card-hover">
          <div className="card-header"><h3>Consent Status Distribution</h3></div>
          <div className="card-body">
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={62} outerRadius={96} paddingAngle={2} strokeWidth={0}>
                    {pieData.map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip content={<ChartTip />} />
                </PieChart>
              </ResponsiveContainer>
            ) : <div className="empty">No consent data yet</div>}
            <div className="flex" style={{ flexWrap: 'wrap', gap: 10, justifyContent: 'center', marginTop: 8 }}>
              {pieData.map((e) => (
                <span key={e.name} className="flex text-sm" style={{ color: 'var(--text-secondary)' }}>
                  <span style={{ width: 10, height: 10, borderRadius: 3, background: e.color, display: 'inline-block' }} />
                  {e.name} <b>&nbsp;({e.value})</b>
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="card card-hover">
          <div className="card-header"><h3>Active Consents by Purpose</h3></div>
          <div className="card-body">
            {data.purpose_distribution.length > 0 ? (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={data.purpose_distribution} layout="vertical" margin={{ left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#eef1f6" />
                  <XAxis type="number" allowDecimals={false} stroke="#8a97b3" fontSize={11} />
                  <YAxis type="category" dataKey="purpose_name" width={130} stroke="#8a97b3" fontSize={11} />
                  <Tooltip content={<ChartTip />} cursor={{ fill: 'rgba(79,110,247,0.06)' }} />
                  <Bar dataKey="active" name="Active" fill="#4f6ef7" radius={[0, 4, 4, 0]} barSize={14} />
                  <Bar dataKey="total" name="Total" fill="#d7defa" radius={[0, 4, 4, 0]} barSize={14} />
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="empty">No purpose data yet</div>}
          </div>
        </div>
      </div>

      <div className="grid-2 mb">
        <div className="card card-hover">
          <div className="card-header">
            <h3>Recent Consent Activity</h3>
            <Link to="/audit" className="text-sm">View all →</Link>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Event</th><th>Customer</th><th>Purpose</th><th>When</th></tr></thead>
              <tbody>
                {data.recent_consent_activity.map((e) => (
                  <tr key={e.id}>
                    <td><Badge status={e.event.replace('CONSENT_', '')} /></td>
                    <td className="mono">{e.customer_external_id || '—'}</td>
                    <td>{e.purpose_code || '—'}</td>
                    <td className="muted">{formatDate(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-hover">
          <div className="card-header"><h3>Expiring Consents</h3></div>
          <div className="card-body" style={{ padding: 0 }}>
            {data.expiring_consents.length === 0 ? (
              <div className="empty">Nothing expiring in the next 30 days</div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Customer</th><th>Purpose</th><th>Expires</th></tr></thead>
                  <tbody>
                    {data.expiring_consents.map((c) => (
                      <tr key={c.id}>
                        <td className="mono">{c.customer_external_id}</td>
                        <td>{c.purpose_name}</td>
                        <td>
                          <span className="flex"><IconClock size={13} style={{ color: 'var(--warning)' }} />{formatDate(c.expires_at)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="card card-hover">
        <div className="card-header"><h3>Recent Audit Events</h3><Link to="/audit" className="text-sm">Audit explorer →</Link></div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Event</th><th>Actor</th><th>Customer</th><th>Decision</th><th>Source</th><th>When</th></tr></thead>
            <tbody>
              {data.recent_audit_events.map((e) => (
                <tr key={e.id}>
                  <td><Badge status={e.event.replace(/_(CREATED|UPDATED|VIEWED|EVALUATED)$/, '')} /></td>
                  <td>{e.actor_username}</td>
                  <td className="mono">{e.customer_external_id || '—'}</td>
                  <td>{e.decision ? <Badge status={e.decision} /> : <span className="muted">—</span>}</td>
                  <td>{e.source_app || '—'}</td>
                  <td className="muted">{formatDate(e.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function DashboardSkeleton() {
  return (
    <div>
      <PageHead title="Dashboard" subtitle="Real-time overview of consent posture across the organization" />
      <div className="card mb" style={{ padding: 22 }}>
        <Skeleton width={140} height={13} />
        <Skeleton width={260} height={22} style={{ marginTop: 10 }} />
        <Skeleton width={200} height={13} style={{ marginTop: 8 }} />
      </div>
      <div className="stat-grid mb">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="card" style={{ padding: 18 }}>
            <Skeleton width="55%" height={12} />
            <Skeleton width="38%" height={26} style={{ marginTop: 10 }} />
            <Skeleton width="70%" height={10} style={{ marginTop: 8 }} />
          </div>
        ))}
      </div>
      <div className="grid-2 mb">
        <div className="card"><div className="card-body"><Skeleton height={260} /></div></div>
        <div className="card"><div className="card-body"><Skeleton height={260} /></div></div>
      </div>
    </div>
  )
}
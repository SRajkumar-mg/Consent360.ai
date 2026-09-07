import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { dashboardApi } from '../api'
import {
  Badge, FooterRow, MetricCard, PageHeading, Skeleton, daysUntil, formatDate, formatDateTime,
} from '../components/ui'
import { IconCheck, IconClock, IconHistory, IconShield, IconUsers, IconX } from '../components/icons'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { DashboardResponse } from '../types'

const STATUS_COLORS: Record<string, string> = {
  Active: '#4f46e5',
  Granted: '#16a34a',
  Expired: '#94a3b8',
  Withdrawn: '#dc2626',
  Pending: '#d97706',
}

function colorForStatus(name: string): string {
  return STATUS_COLORS[name] || '#0284c7'
}

function expiringLabel(n: number | null): string {
  if (n == null) return 'Expiring —'
  if (n <= 0) return 'Expiring today'
  if (n === 1) return 'Expiring in 1 day'
  return `Expiring in ${n} days`
}

function ChartTip({ active, payload, label }: { active?: boolean; payload?: Array<{ name?: string; value?: number | string; color?: string; payload?: { fill?: string; color?: string } }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tip">
      {label != null && <div className="chart-tip-title">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="chart-tip-row">
          <span className="chart-tip-dot" style={{ background: p.color || p.payload?.fill || p.payload?.color || 'var(--primary)' }} />
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

  useEffect(() => {
    dashboardApi.get().then((r) => setData(r.data)).catch(() => setError('Failed to load dashboard'))
  }, [])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <DashboardSkeleton />

  const m = data.metrics
  const pieData = data.status_distribution
    .filter((s) => s.count > 0)
    .map((s) => {
      const name = s.status.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
      return { name, value: s.count, color: colorForStatus(name) }
    })
  const total = pieData.reduce((sum, p) => sum + p.value, 0)

  const events = data.recent_audit_events
  const oldest = events[events.length - 1]

  return (
    <div>
      <PageHeading title="Dashboard" subtitle="Real-time overview of consent posture across the organization" />

      <div className="posture-banner">
        <div className="posture-left">
          <span className="posture-pill"><span className="badge-dot" /> Consent Posture Active</span>
          <div className="posture-text">{m.total_consents} consent records • {m.active_consents} active in production environments</div>
        </div>
        <div className="posture-metrics">
          <div><b>{m.expiring_soon}</b><span>expiring ≤ 30d</span></div>
          <div><b>{m.total_customers}</b><span>customers</span></div>
          <div><b>{m.denied_consents}</b><span>denied</span></div>
        </div>
      </div>

      <div className="metric-grid mb">
        <MetricCard label="Total Customers" value={m.total_customers} icon={<IconUsers size={20} />} tone="primary" sub="in consent directory" />
        <MetricCard label="Active Consents" value={m.active_consents} icon={<IconCheck size={20} />} tone="success" sub={`of ${m.total_consents} total records`} />
        <MetricCard label="Granted" value={m.granted_consents} icon={<IconShield size={20} />} tone="info" sub="consent granted on record" />
        <MetricCard label="Pending" value={m.pending_consents} icon={<IconClock size={20} />} tone="warning" sub={`${m.expiring_soon} expiring ≤ 30 days`} />
        <MetricCard label="Withdrawn" value={m.withdrawn_consents} icon={<IconX size={20} />} tone="danger" sub="withdrawn by principal or admin" />
        <MetricCard label="Expired" value={m.expired_consents} icon={<IconHistory size={20} />} tone="slate" sub="past validity period" />
      </div>

      <div className="grid-2 mb">
        <div className="card card-hover">
          <div className="card-header"><h3>Consent Status Distribution</h3></div>
          <div className="card-body">
            {pieData.length > 0 ? (
              <div className="flex" style={{ gap: 24, alignItems: 'center' }}>
                <div className="donut-wrap" style={{ height: 240, flex: '0 0 240px' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={64} outerRadius={90} paddingAngle={2} strokeWidth={0}>
                        {pieData.map((e, i) => <Cell key={i} fill={e.color} />)}
                      </Pie>
                      <Tooltip content={<ChartTip />} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="donut-center"><b>{total}</b><span>Total</span></div>
                </div>
                <ul className="legend-list" style={{ flex: 1 }}>
                  {pieData.map((e) => (
                    <li key={e.name}>
                      <span style={{ width: 10, height: 10, borderRadius: 3, background: e.color, display: 'inline-block' }} />
                      {e.name}
                      <b>{e.value}</b>
                    </li>
                  ))}
                </ul>
              </div>
            ) : <div className="empty">No consent data yet</div>}
          </div>
        </div>

        <div className="card card-hover">
          <div className="card-header"><h3>Active Consents By Purpose</h3></div>
          <div className="card-body">
            {data.purpose_distribution.length > 0 ? (
              <div className="purpose-bars">
                {data.purpose_distribution.map((p) => (
                  <div className="row" key={p.purpose_code}>
                    <span>{p.purpose_name}</span>
                    <div className="progress">
                      <div className="progress-bar" style={{ width: `${p.total > 0 ? Math.round((p.active / p.total) * 100) : 0}%`, background: 'var(--primary)' }} />
                    </div>
                    <b style={{ textAlign: 'right' }}>{p.active}</b>
                  </div>
                ))}
              </div>
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
          {data.expiring_consents.length === 0 ? (
            <div className="empty">Nothing expiring in the next 30 days</div>
          ) : (
            <div className="expiring-list">
              {data.expiring_consents.map((c) => (
                <div className="item" key={c.id}>
                  <div>
                    <div className="mono">{c.customer_external_id}</div>
                    <div className="text-sm" style={{ color: 'var(--text-muted)' }}>{c.purpose_name}</div>
                  </div>
                  <span className="pill-warning">{expiringLabel(daysUntil(c.expires_at))}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card card-hover">
        <div className="card-header"><h3>Recent Audit Events</h3><Link to="/audit" className="text-sm">Open audit explorer →</Link></div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Event Type</th><th>Actor</th><th>Description</th><th>Outcome</th><th>Timestamp</th></tr></thead>
            <tbody>
              {data.recent_audit_events.map((e) => (
                <tr key={e.id}>
                  <td><Badge status={e.event.replace(/_(CREATED|UPDATED|VIEWED|EVALUATED)$/, '')} /></td>
                  <td>{e.actor_username}</td>
                  <td>{e.reason || '—'}</td>
                  <td><Badge status={e.decision || 'SUCCESS'} /></td>
                  <td className="muted">{formatDateTime(e.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <FooterRow left={`Directory created: ${formatDate(oldest?.created_at)}`} />
    </div>
  )
}

function DashboardSkeleton() {
  return (
    <div>
      <PageHeading title="Dashboard" subtitle="Real-time overview of consent posture across the organization" />
      <div className="posture-banner">
        <Skeleton width={260} height={16} />
        <Skeleton width={180} height={16} />
      </div>
      <div className="metric-grid mb">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="metric-card">
            <div className="metric-card-body">
              <Skeleton width="55%" height={12} />
              <Skeleton width="38%" height={26} style={{ marginTop: 10 }} />
              <Skeleton width="70%" height={10} style={{ marginTop: 8 }} />
            </div>
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

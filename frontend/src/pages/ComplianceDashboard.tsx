/**
 * R2-07: the compliance KPI dashboard.
 *
 * The whole design problem on this page is one sentence: a KPI that is zero
 * and a KPI that is unknown must never look alike. A blank tile, a dash, or a
 * "0%" where the honest answer is "this platform cannot measure that yet" is
 * a misrepresentation to whoever is reading the page to decide whether the
 * organisation is compliant.
 *
 * So nothing here renders a bare number. Every tile carries a status chip and
 * every non-live tile carries the reason in the tile itself, not behind a
 * hover:
 *
 *   Live             a real figure over a real denominator
 *   Partial          computed, but over less than the KPI's full definition
 *   No data yet      instrumented and queried; the sample was empty
 *   Not instrumented no data source exists in this build
 *
 * "No data yet" and "Not instrumented" tiles are drawn differently from each
 * other as well as from live ones - the first is a live metric waiting for
 * traffic, the second is a gap in the platform - because conflating those two
 * is how a dashboard ends up quietly claiming coverage it does not have.
 *
 * The backend sends `display` pre-formatted so the number and its unit cannot
 * drift apart between the tile, the drill-down and the trend, and sends
 * `computed_by` so a reader can check that this page agrees with the screen
 * that owns each figure rather than having recomputed it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { kpiApi, organizationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import { useAuth } from '../context/AuthContext'
import {
  EmptyState, FooterRow, MetricCard, Modal, PageHeading, Skeleton, formatDateTime, useToast,
} from '../components/ui'
import { IconAlert, IconCheck, IconClock, IconDownload, IconInbox } from '../components/icons'
import type { Kpi, KpiCatalogue, KpiStatus, KpiTrend, Organization } from '../types'

const STATUS_LABEL: Record<KpiStatus, string> = {
  LIVE: 'Live',
  PARTIAL: 'Partial',
  NO_DATA: 'No data yet',
  UNAVAILABLE: 'Not instrumented',
}

const STATUS_HINT: Record<KpiStatus, string> = {
  LIVE: 'Computed from real data over a real denominator.',
  PARTIAL: 'Computed, but over less than this KPI’s full definition — see coverage.',
  NO_DATA: 'Instrumented and queried; nothing fell in this period. The value is unknown, not zero.',
  UNAVAILABLE: 'This build has no data source for this KPI. Its absence is not a finding of zero.',
}

const STATUS_COLOR: Record<KpiStatus, string> = {
  LIVE: '#16a34a',
  PARTIAL: '#d97706',
  NO_DATA: '#0284c7',
  UNAVAILABLE: '#94a3b8',
}

const PERIODS = [
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
  { days: 365, label: 'Last 12 months' },
]

function StatusChip({ status }: { status: KpiStatus }) {
  return (
    <span className={`kpi-chip kpi-chip-${status.toLowerCase()}`} title={STATUS_HINT[status]}>
      <span className="kpi-chip-dot" style={{ background: STATUS_COLOR[status] }} />
      {STATUS_LABEL[status]}
    </span>
  )
}

function KpiTile({ kpi, onOpen }: { kpi: Kpi; onOpen: (kpi: Kpi) => void }) {
  const answered = kpi.status === 'LIVE' || kpi.status === 'PARTIAL'
  return (
    <button
      type="button"
      className={`kpi-tile kpi-tile-${kpi.status.toLowerCase()}`}
      onClick={() => onOpen(kpi)}
      aria-label={`${kpi.id} ${kpi.name}. ${STATUS_LABEL[kpi.status]}. ${
        answered ? `Value ${kpi.display}.` : 'No value.'
      } Open drill-down.`}
    >
      <div className="kpi-tile-head">
        <span className="kpi-tile-id">{kpi.id}</span>
        <StatusChip status={kpi.status} />
      </div>
      <div className="kpi-tile-name">{kpi.name}</div>
      <div className={`kpi-tile-value${answered ? '' : ' kpi-tile-value-unknown'}`}>
        {answered ? kpi.display : STATUS_LABEL[kpi.status]}
      </div>
      {answered && kpi.denominator != null && (
        <div className="kpi-tile-sub">
          {kpi.numerator ?? '—'} of {kpi.denominator}
        </div>
      )}
      {!answered && kpi.reason && <div className="kpi-tile-reason">{kpi.reason}</div>}
      {kpi.status === 'PARTIAL' && kpi.coverage && (
        <div className="kpi-tile-reason">Partial: {kpi.coverage}</div>
      )}
      <div className="kpi-tile-foot">
        <span>{kpi.statutory_ref}</span>
        <span>Target: {kpi.target}</span>
      </div>
    </button>
  )
}

/**
 * Suffix for a KPI's unit. The tile and the drill-down headline get their
 * formatting from the backend's `display`, but the chart axis builds its own
 * labels from raw numbers - so without this a percent series and a count
 * series plot identically and a reader has no way to tell 24 decisions from
 * 24%. The number and its unit must not come apart anywhere on this page.
 */
function unitSuffix(unit: string): string {
  if (unit === 'percent') return '%'
  if (unit === 'seconds') return 's'
  if (unit === 'hours') return ' h'
  if (unit === 'days') return ' d'
  return ''
}

function TrendChart({ trend }: { trend: KpiTrend }) {
  if (trend.status !== 'LIVE' || trend.points.length === 0) {
    return (
      <div className="alert alert-info" style={{ marginBottom: 16 }}>
        <b>No trend series.</b> {trend.reason || 'No observation falls in this period.'}
      </div>
    )
  }
  const suffix = unitSuffix(trend.unit)
  const data = trend.points.map((p) => ({ period: p.period, value: p.value }))
  return (
    <div style={{ height: 220, marginBottom: 20 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="period" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} />
          <YAxis
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} axisLine={false}
            tickFormatter={(v: number) => `${v}${suffix}`}
            domain={trend.unit === 'percent' ? [0, 100] : undefined}
          />
          <Tooltip
            contentStyle={{
              borderRadius: 10, border: '1px solid var(--border)', fontSize: 12,
            }}
            formatter={(v: number | string) => [`${v}${suffix}`, trend.name]}
          />
          <Line
            type="monotone" dataKey="value" stroke="var(--primary)" strokeWidth={2}
            dot={{ r: 3 }} connectNulls={false} name={trend.name}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function DrillDown({
  kpi, trend, loading, onClose,
}: {
  kpi: Kpi | null
  trend: KpiTrend | null
  loading: boolean
  onClose: () => void
}) {
  if (!kpi) return null
  const answered = kpi.status === 'LIVE' || kpi.status === 'PARTIAL'
  return (
    <Modal open wide title={`${kpi.id} · ${kpi.name}`} onClose={onClose}>
      <div className="kpi-drill-head">
        <div>
          <div className={`kpi-drill-value${answered ? '' : ' kpi-tile-value-unknown'}`}>
            {answered ? kpi.display : STATUS_LABEL[kpi.status]}
          </div>
          <div className="text-sm text-muted">
            {answered && kpi.denominator != null
              ? `${kpi.numerator ?? '—'} of ${kpi.denominator}`
              : STATUS_HINT[kpi.status]}
          </div>
        </div>
        <StatusChip status={kpi.status} />
      </div>

      {!answered && kpi.reason && (
        <div className="alert alert-info" style={{ marginBottom: 16 }}>
          <b>Why there is no number.</b> {kpi.reason}
          {kpi.unblocked_by && (
            <div style={{ marginTop: 6 }}>
              <b>Unblocked by:</b> {kpi.unblocked_by}
            </div>
          )}
        </div>
      )}
      {kpi.status === 'PARTIAL' && kpi.coverage && (
        <div className="alert alert-info" style={{ marginBottom: 16 }}>
          <b>What this figure covers.</b> {kpi.coverage}
        </div>
      )}

      <dl className="detail-grid" style={{ marginBottom: 20 }}>
        <div className="detail-item">
          <dt>Formula</dt>
          <dd>{kpi.formula}</dd>
        </div>
        <div className="detail-item">
          <dt>Statutory basis</dt>
          <dd>{kpi.statutory_ref}</dd>
        </div>
        <div className="detail-item">
          <dt>Target</dt>
          <dd>{kpi.target}</dd>
        </div>
        <div className="detail-item">
          <dt>Computed by</dt>
          <dd className="mono text-xs">{kpi.computed_by || 'not computed in this build'}</dd>
        </div>
        {kpi.sample_size != null && (
          <div className="detail-item">
            <dt>Sample size</dt>
            <dd>{kpi.sample_size}</dd>
          </div>
        )}
      </dl>

      <h4 className="kpi-section-title">Trend</h4>
      {loading && <Skeleton height={200} />}
      {!loading && !kpi.supports_trend && (
        <div className="alert alert-info" style={{ marginBottom: 16 }}>
          <b>No trend series.</b> This KPI is computed point-in-time by the module that owns
          it; plotting one here would mean recomputing it a second way.
        </div>
      )}
      {!loading && kpi.supports_trend && trend && <TrendChart trend={trend} />}

      <h4 className="kpi-section-title">Breakdown</h4>
      {kpi.breakdown.length === 0 ? (
        <EmptyState
          icon={<IconInbox size={26} />}
          title="No breakdown"
          message={
            kpi.supports_breakdown
              ? 'This KPI supports a breakdown but the sample for this period is empty.'
              : 'This KPI has no dimension to break down by.'
          }
        />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{kpi.breakdown[0]?.dimension || 'Dimension'}</th>
                <th style={{ textAlign: 'right' }}>Value</th>
                <th style={{ textAlign: 'right' }}>Numerator</th>
                <th style={{ textAlign: 'right' }}>Denominator</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {kpi.breakdown.map((row, index) => (
                <tr key={`${row.dimension}-${row.label}-${index}`}>
                  <td>{row.label}</td>
                  <td style={{ textAlign: 'right' }}>
                    {row.value == null
                      ? '—'
                      : row.unit === 'percent'
                        ? `${row.value.toFixed(1)}%`
                        : row.value.toLocaleString()}
                  </td>
                  <td style={{ textAlign: 'right' }}>{row.numerator ?? '—'}</td>
                  <td style={{ textAlign: 'right' }}>{row.denominator ?? '—'}</td>
                  <td><StatusChip status={row.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {Object.keys(kpi.detail).length > 0 && (
        <>
          <h4 className="kpi-section-title">Raw figures from the owning module</h4>
          <pre className="code-block" style={{ maxHeight: 240, overflow: 'auto' }}>
            {JSON.stringify(kpi.detail, null, 2)}
          </pre>
        </>
      )}
    </Modal>
  )
}

export function ComplianceDashboardPage() {
  const { hasPermission } = useAuth()
  const toast = useToast()

  const [catalogue, setCatalogue] = useState<KpiCatalogue | null>(null)
  const [error, setError] = useState('')
  const [periodDays, setPeriodDays] = useState(30)
  const [sourceApp, setSourceApp] = useState('')
  const [statusFilter, setStatusFilter] = useState<KpiStatus | 'ALL'>('ALL')
  const [tenants, setTenants] = useState<Organization[]>([])

  const [selected, setSelected] = useState<Kpi | null>(null)
  const [trend, setTrend] = useState<KpiTrend | null>(null)
  const [trendLoading, setTrendLoading] = useState(false)

  const [packAllowed, setPackAllowed] = useState<boolean | null>(null)
  const [packReason, setPackReason] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  const load = useCallback(() => {
    kpiApi
      .catalogue({ period_days: periodDays, source_app: sourceApp || undefined })
      .then((r) => {
        setCatalogue(r.data)
        setError('')
      })
      .catch((e) => setError(getErrorMessage(e)))
  }, [periodDays, sourceApp])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    kpiApi
      .evidencePackAvailability()
      .then((r) => {
        setPackAllowed(r.data.allowed)
        setPackReason(r.data.reason)
      })
      .catch(() => setPackAllowed(false))
    // A tenant picker is a convenience, not a permission: a role that cannot
    // list organizations simply does not get one.
    organizationsApi
      .list()
      .then((r) => setTenants(r.data))
      .catch(() => setTenants([]))
  }, [])

  const openKpi = (kpi: Kpi) => {
    setSelected(kpi)
    setTrend(null)
    if (!kpi.supports_trend) return
    setTrendLoading(true)
    kpiApi
      .trend(kpi.id, { period_days: periodDays, source_app: sourceApp || undefined })
      .then((r) => setTrend(r.data))
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setTrendLoading(false))
  }

  const downloadPack = () => {
    setDownloading(true)
    const end = new Date()
    const start = new Date(end.getTime() - periodDays * 86400000)
    kpiApi
      .evidencePack({
        period_start: start.toISOString(),
        period_end: end.toISOString(),
        tenant_code: sourceApp || undefined,
      })
      .then((r) => {
        const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `evidence-pack-${sourceApp || 'all-tenants'}-${end
          .toISOString()
          .slice(0, 10)}.json`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        URL.revokeObjectURL(url)
        toast('success', 'Evidence pack downloaded. Its manifest hash is inside, under integrity_proofs.')
      })
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setDownloading(false))
  }

  const visible = useMemo(() => {
    if (!catalogue) return []
    return statusFilter === 'ALL'
      ? catalogue.kpis
      : catalogue.kpis.filter((k) => k.status === statusFilter)
  }, [catalogue, statusFilter])

  const byDomain = useMemo(() => {
    const map = new Map<string, Kpi[]>()
    for (const kpi of visible) {
      const list = map.get(kpi.domain) || []
      list.push(kpi)
      map.set(kpi.domain, list)
    }
    return map
  }, [visible])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!catalogue) {
    return (
      <div>
        <PageHeading title="Compliance KPIs" subtitle="Loading the KPI catalogue…" />
        <div className="metric-grid mb">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} height={96} />)}
        </div>
        <Skeleton height={320} />
      </div>
    )
  }

  const c = catalogue.counts
  const answered = c.live + c.partial

  return (
    <div>
      <PageHeading
        title="Compliance KPIs"
        subtitle={`The DPDP KPI catalogue — ${c.total} metrics, ${answered} carrying a figure, for ${catalogue.scope_label}`}
        actions={
          <div className="flex" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select
              className="input"
              style={{ width: 'auto' }}
              value={periodDays}
              onChange={(e) => setPeriodDays(Number(e.target.value))}
              aria-label="Reporting period"
            >
              {PERIODS.map((p) => <option key={p.days} value={p.days}>{p.label}</option>)}
            </select>
            {!catalogue.scope_locked && tenants.length > 0 && (
              <select
                className="input"
                style={{ width: 'auto' }}
                value={sourceApp}
                onChange={(e) => setSourceApp(e.target.value)}
                aria-label="Tenant"
              >
                <option value="">All tenants</option>
                {tenants.map((t) => <option key={t.id} value={t.code}>{t.name}</option>)}
              </select>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={downloadPack}
              disabled={packAllowed !== true || downloading}
              title={packReason || 'Download the regulator evidence pack for this period'}
            >
              <IconDownload size={16} />
              {downloading ? 'Preparing…' : 'Evidence pack'}
            </button>
          </div>
        }
      />

      {packAllowed === false && packReason && (
        <div className="alert alert-info mb">{packReason}</div>
      )}

      {/*
        The honesty band. This is not decoration: it is the legend that makes
        the difference between "0" and "we cannot say" readable at a glance,
        and it doubles as the status filter.
      */}
      <div className="metric-grid mb">
        <MetricCard
          label="Live" value={c.live} tone="success" icon={<IconCheck size={20} />}
          sub="real figure, real denominator"
        />
        <MetricCard
          label="Partial" value={c.partial} tone="warning" icon={<IconAlert size={20} />}
          sub="computed over part of the definition"
        />
        <MetricCard
          label="No data yet" value={c.no_data} tone="info" icon={<IconClock size={20} />}
          sub="instrumented; sample empty — not zero"
        />
        <MetricCard
          label="Not instrumented" value={c.unavailable} tone="slate" icon={<IconInbox size={20} />}
          sub="no data source in this build"
        />
      </div>

      <div className="card mb">
        <div className="card-body">
          <p className="text-sm text-secondary" style={{ margin: 0 }}>
            <b>How to read this page.</b> {catalogue.honesty_note}
          </p>
          <div className="filter-chips" style={{ marginTop: 12 }}>
            {(['ALL', 'LIVE', 'PARTIAL', 'NO_DATA', 'UNAVAILABLE'] as const).map((value) => (
              <button
                key={value}
                type="button"
                className={`filter-chip${statusFilter === value ? ' active' : ''}`}
                onClick={() => setStatusFilter(value)}
                aria-pressed={statusFilter === value}
              >
                {value === 'ALL' ? `All ${c.total}` : `${STATUS_LABEL[value]} ${c[
                  value.toLowerCase() as 'live' | 'partial' | 'no_data' | 'unavailable'
                ]}`}
              </button>
            ))}
          </div>
        </div>
      </div>

      {catalogue.domains
        .filter((domain) => (byDomain.get(domain) || []).length > 0)
        .map((domain) => (
          <div className="card card-hover mb" key={domain}>
            <div className="card-header">
              <h3>{domain}</h3>
              <span className="text-sm text-muted">
                {(byDomain.get(domain) || []).length} metric
                {(byDomain.get(domain) || []).length === 1 ? '' : 's'}
              </span>
            </div>
            <div className="card-body">
              <div className="kpi-grid">
                {(byDomain.get(domain) || []).map((kpi) => (
                  <KpiTile key={kpi.id} kpi={kpi} onOpen={openKpi} />
                ))}
              </div>
            </div>
          </div>
        ))}

      {visible.length === 0 && (
        <EmptyState
          icon={<IconInbox size={30} />}
          title="Nothing matches this filter"
          message="No KPI in the catalogue currently has that status."
        />
      )}

      <DrillDown
        kpi={selected}
        trend={trend}
        loading={trendLoading}
        onClose={() => { setSelected(null); setTrend(null) }}
      />

      <FooterRow
        left={
          <span>
            Generated {formatDateTime(catalogue.generated_at)} · period{' '}
            {catalogue.period_days} days · scope {catalogue.scope_label}
            {catalogue.scope_locked && ' (locked to your role)'}
            {hasPermission('audit.export') ? '' : ' · evidence pack requires audit.export'}
          </span>
        }
      />
    </div>
  )
}

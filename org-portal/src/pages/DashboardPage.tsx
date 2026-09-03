import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { orgApi, getErrorMessage } from '../api'

interface StatusRow {
  status: string
  count: number
}

interface UserRow {
  username: string
  fullName: string
  role: string
  lastLogin: string
}

interface ActivityRow {
  time: string
  actor: string
  action: string
  detail: string
}

interface DashData {
  orgName: string
  totalCustomers: number
  totalConsents: number
  activeConsents: number
  statuses: StatusRow[]
  users: UserRow[]
  activity: ActivityRow[]
}

function firstDefined(...values: unknown[]): unknown {
  for (const v of values) {
    if (v !== undefined && v !== null && v !== '') return v
  }
  return undefined
}

function toNum(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function toStr(v: unknown): string {
  if (v === undefined || v === null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return ''
}

function toArray(v: unknown): any[] {
  return Array.isArray(v) ? v : []
}

function fmtDate(v: unknown): string {
  const s = toStr(v)
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function normalize(data: any): DashData {
  const org = data?.organization ?? {}
  const stats = data?.stats ?? data?.statistics ?? data ?? {}

  const users: UserRow[] = toArray(firstDefined(data?.users, data?.members)).map((u) => ({
    username: toStr(firstDefined(u?.username, u?.email)),
    fullName:
      toStr(firstDefined(u?.full_name, u?.fullName, u?.name)) ||
      toStr(firstDefined(u?.username, u?.email)),
    role: toStr(firstDefined(u?.role, u?.role_name)) || 'member',
    lastLogin: fmtDate(firstDefined(u?.last_login, u?.last_login_at, u?.lastLogin)),
  }))

  let statuses: StatusRow[] = []
  const rawStatuses = firstDefined(
    data?.consent_summary,
    data?.consent_status_summary,
    data?.status_summary,
    data?.consents_by_status,
    stats?.consent_statuses
  )
  if (Array.isArray(rawStatuses)) {
    statuses = rawStatuses.map((s) =>
      typeof s === 'string'
        ? { status: s, count: 0 }
        : {
            status: toStr(firstDefined(s?.status, s?.state, s?.name)),
            count: toNum(firstDefined(s?.count, s?.total, s?.value)),
          }
    )
  } else if (rawStatuses && typeof rawStatuses === 'object') {
    statuses = Object.entries(rawStatuses as Record<string, unknown>).map(([status, count]) => ({
      status,
      count: toNum(count),
    }))
  }
  statuses = statuses.filter((s) => s.status)

  const activity: ActivityRow[] = toArray(
    firstDefined(data?.recent_activity, data?.activity, data?.audit_log, data?.audit_logs)
  ).map((a) => ({
    time: fmtDate(firstDefined(a?.timestamp, a?.created_at, a?.at, a?.time, a?.date)),
    actor: toStr(firstDefined(a?.actor, a?.user, a?.username, a?.actor_username, a?.performed_by)) || 'system',
    action: toStr(firstDefined(a?.action, a?.event, a?.type, a?.activity_type)),
    detail: toStr(firstDefined(a?.detail, a?.details, a?.description, a?.resource, a?.target)),
  }))

  return {
    orgName:
      toStr(firstDefined(org?.name, org?.title, data?.organization_name, data?.org_name)) ||
      'Organization',
    totalCustomers: toNum(firstDefined(stats?.total_customers, stats?.customers, data?.total_customers)),
    totalConsents: toNum(firstDefined(stats?.total_consents, stats?.consents, data?.total_consents)),
    activeConsents: toNum(firstDefined(stats?.active_consents, data?.active_consents)),
    statuses,
    users,
    activity,
  }
}

function statusClass(status: string): string {
  const s = status.toLowerCase()
  if (s.includes('grant') || s.includes('active') || s.includes('approv')) return 'badge badge-green'
  if (s.includes('pending') || s.includes('review')) return 'badge badge-amber'
  if (s.includes('revok') || s.includes('deni') || s.includes('reject') || s.includes('withdraw') || s.includes('expire'))
    return 'badge badge-red'
  return 'badge badge-gray'
}

function readStoredUser(): { username?: string; full_name?: string; name?: string; role?: string } {
  try {
    return JSON.parse(localStorage.getItem('org_user') || '{}')
  } catch {
    return {}
  }
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<DashData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const user = readStoredUser()
  const displayName =
    user.full_name || user.name || user.username || 'User'
  const role = user.role || ''

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError('')
      try {
        const res = await orgApi.getDashboard()
        if (!cancelled) setData(normalize(res.data))
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  function signOut() {
    localStorage.removeItem('org_access_token')
    localStorage.removeItem('org_user')
    localStorage.removeItem('org_organization')
    navigate('/login')
  }

  return (
    <div className="dashboard-page">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2.5l7.5 3v5.2c0 4.8-3.2 9-7.5 10.8C7.7 19.7 4.5 15.5 4.5 10.7V5.5l7.5-3z" />
            </svg>
          </span>
          <span className="brand-name">{data?.orgName ?? 'Organization Portal'}</span>
          <span className="readonly-pill">Read-only</span>
        </div>
        <div className="topbar-user">
          <span className="user-name">{displayName}</span>
          {role && <span className={`role-badge role-${role.toLowerCase()}`}>{role}</span>}
          <button type="button" className="btn btn-outline" onClick={signOut}>
            Sign Out
          </button>
        </div>
      </header>

      <main className="dashboard-main">
        <section className="welcome">
          <h1>Welcome back, {displayName}</h1>
          <p>
            Here is an overview of your organization&apos;s consent data.
            {role ? ` You are signed in as ${role}.` : ''}
          </p>
        </section>

        {loading && <div className="state-card">Loading dashboard…</div>}

        {!loading && error && (
          <div className="state-card state-error">
            <p>{error}</p>
            <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
              Retry
            </button>
          </div>
        )}

        {!loading && !error && data && (
          <>
            <section className="stats-grid">
              <div className="stat-card stat-blue">
                <span className="stat-label">Total Customers</span>
                <span className="stat-value">{data.totalCustomers.toLocaleString()}</span>
              </div>
              <div className="stat-card stat-violet">
                <span className="stat-label">Total Consents</span>
                <span className="stat-value">{data.totalConsents.toLocaleString()}</span>
              </div>
              <div className="stat-card stat-green">
                <span className="stat-label">Active Consents</span>
                <span className="stat-value">{data.activeConsents.toLocaleString()}</span>
              </div>
            </section>

            <section className="dash-columns">
              <div className="panel">
                <div className="panel-header">
                  <h2>Users</h2>
                  <span className="panel-count">{data.users.length}</span>
                </div>
                {data.users.length === 0 ? (
                  <p className="empty-text">No users found.</p>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Username</th>
                        <th>Full Name</th>
                        <th>Role</th>
                        <th>Last Login</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.users.map((u, i) => (
                        <tr key={`${u.username}-${i}`}>
                          <td className="cell-strong">{u.username}</td>
                          <td>{u.fullName}</td>
                          <td>
                            <span className={`role-badge role-${u.role.toLowerCase()}`}>{u.role}</span>
                          </td>
                          <td className="cell-muted">{u.lastLogin}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div className="dash-right">
                <div className="panel">
                  <div className="panel-header">
                    <h2>Consent Status Summary</h2>
                  </div>
                  {data.statuses.length === 0 ? (
                    <p className="empty-text">No consent data available.</p>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Status</th>
                          <th className="cell-right">Count</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.statuses.map((s, i) => (
                          <tr key={`${s.status}-${i}`}>
                            <td>
                              <span className={statusClass(s.status)}>{s.status}</span>
                            </td>
                            <td className="cell-strong cell-right">{s.count.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>

                <div className="panel">
                  <div className="panel-header">
                    <h2>Recent Activity</h2>
                    <span className="panel-count">{data.activity.length}</span>
                  </div>
                  {data.activity.length === 0 ? (
                    <p className="empty-text">No recent activity.</p>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>User</th>
                          <th>Action</th>
                          <th>Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.activity.map((a, i) => (
                          <tr key={i}>
                            <td className="cell-muted cell-nowrap">{a.time}</td>
                            <td className="cell-strong">{a.actor}</td>
                            <td>{a.action}</td>
                            <td className="cell-muted">{a.detail || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

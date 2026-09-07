import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { customersApi } from '../api'
import { Avatar, Badge, FooterRow, MaskedValue, MetricCard, PageHeading, TableSkeleton, formatDate } from '../components/ui'
import { IconShield, IconUsers } from '../components/icons'
import type { Customer } from '../types'

export function CustomersPage() {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    customersApi.list(search, 200).then((r) => setCustomers(r.data)).finally(() => setLoading(false))
  }, [search])

  const sources = useMemo(() => {
    const s = new Set(customers.map((c) => c.source_app || 'unknown'))
    return s.size
  }, [customers])

  const activeCount = useMemo(() => customers.filter((c) => c.status === 'ACTIVE').length, [customers])

  return (
    <div>
      <PageHeading title="Customers" subtitle="Customer references synchronized from business applications — email and phone are masked for privacy" />

      <div className="metric-grid mb">
        <MetricCard label="Customers" value={customers.length} icon={<IconUsers size={20} />} tone="primary" sub={search ? 'matching current search' : 'in consent directory'} />
        <MetricCard label="Active" value={activeCount} icon={<IconShield size={20} />} tone="success" sub="enabled customer profiles" />
        <MetricCard label="Source Apps" value={sources} icon={<IconUsers size={20} />} tone="purple" sub="connected business applications" />
      </div>

      <div className="search-row">
        <input className="input search-input" placeholder="Search customers…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="chip">{customers.length} customers</span>
      </div>

      <div className="card card-hover">
        {loading ? <TableSkeleton rows={6} cols={6} /> : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Customer</th><th>Email</th><th>Phone</th><th>Status</th><th>Source</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {customers.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <div className="flex" style={{ gap: 10 }}>
                        <Avatar name={c.name} size={32} />
                        <div>
                          <div style={{ fontWeight: 600 }}>{c.name}</div>
                          <div className="mono text-xs" style={{ color: 'var(--text-muted)' }}>{c.external_id}</div>
                        </div>
                      </div>
                    </td>
                    <td>{c.email ? <MaskedValue type="email" value={c.email} /> : '—'}</td>
                    <td>{c.phone ? <MaskedValue type="phone" value={c.phone} /> : '—'}</td>
                    <td><Badge status={c.status} /></td>
                    <td><span className="badge b-primary">{c.source_app || 'unknown'}</span></td>
                    <td>
                      <Link to={`/customers/${c.external_id}`} className="btn btn-sm btn-primary">
                        <IconShield size={13} /> Manage Consent
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <FooterRow left={`Directory created: ${customers[0] ? formatDate(customers[0].created_at) : '—'}`} />
    </div>
  )
}

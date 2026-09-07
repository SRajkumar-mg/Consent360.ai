import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { usePageTitle, useBreadcrumb } from '../hooks/usePageTitle'
import { Consent360Logo } from './Logo'
import { Chatbot } from './Chatbot'
import { Breadcrumb, Avatar } from './ui'
import {
  IconAdmin, IconAlert, IconApi, IconAudit, IconCustomers, IconDashboard, IconInbox, IconLink,
  IconLogout, IconPolicy, IconPurpose, IconRefresh, IconShield, IconTrash, IconUsers,
} from './icons'

interface NavEntry {
  label: string
  icon: typeof IconDashboard
  path: string
  exact?: boolean
  /** The permission that makes this screen worth showing. A screen is listed
   *  when its *read* permission is held; its write actions gate themselves. */
  perm?: string
}

/**
 * R2-08: the sidebar is grouped, because it now carries four distinct kinds of
 * work and a flat list of twelve entries stops being navigable. Each group is
 * hidden entirely when the role holds none of its permissions, so a viewer does
 * not see an empty "Compliance operations" heading.
 */
const NAV_GROUPS: Array<{ group: string; items: NavEntry[] }> = [
  {
    group: 'Platform',
    items: [
      { label: 'Overview', icon: IconDashboard, path: '/dashboard', exact: true, perm: 'dashboard.view' },
      { label: 'Compliance KPIs', icon: IconShield, path: '/compliance', perm: 'dashboard.view' },
      { label: 'Customers', icon: IconCustomers, path: '/customers', perm: 'customer.view' },
      { label: 'Audit Explorer', icon: IconAudit, path: '/audit', perm: 'audit.view' },
    ],
  },
  {
    group: 'Notice & consent',
    items: [
      { label: 'Consent Purposes', icon: IconPurpose, path: '/purposes', perm: 'purpose.view' },
      { label: 'Notices', icon: IconPolicy, path: '/notices', perm: 'purpose.view' },
      { label: 'Policies', icon: IconPolicy, path: '/policies', perm: 'policy.view' },
      { label: 'Re-consent', icon: IconRefresh, path: '/re-consent', perm: 'policy.view' },
    ],
  },
  {
    group: 'Rights & obligations',
    items: [
      { label: 'Rights & Grievances', icon: IconUsers, path: '/grievances', perm: 'grievance.view' },
      { label: 'Breach Register', icon: IconAlert, path: '/breaches', perm: 'breach.view' },
      { label: 'Retention & Erasure', icon: IconTrash, path: '/erasure', perm: 'erasure.view' },
    ],
  },
  {
    group: 'Third parties & delivery',
    items: [
      { label: 'Processors', icon: IconLink, path: '/processors', perm: 'policy.view' },
      { label: 'Consent Managers', icon: IconShield, path: '/consent-managers', perm: 'audit.view' },
      { label: 'Delivery & Receipts', icon: IconInbox, path: '/records', perm: 'audit.view' },
    ],
  },
]

function NavItem({ item, pathname }: { item: NavEntry; pathname: string }) {
  const active = item.exact ? pathname === item.path : pathname.startsWith(item.path)
  const Icon = item.icon
  return (
    <Link to={item.path} className={`nav-item ${active ? 'active' : ''}`}>
      <Icon />
      <span>{item.label}</span>
    </Link>
  )
}

export function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout, hasPermission } = useAuth()
  usePageTitle()
  const crumbs = useBreadcrumb()
  const { pathname } = useLocation()
  const navigate = useNavigate()

  const isOrgAdmin = ['jobhub_admin', 'codex_admin', 'skilllearn_admin'].includes(user?.username || '')
  const orgLabel = isOrgAdmin ? (user?.username?.replace('_admin', '').toUpperCase() || 'Organization') : ''

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const visibleGroups = NAV_GROUPS
    .map((g) => ({ ...g, items: g.items.filter((item) => !item.perm || hasPermission(item.perm)) }))
    .filter((g) => g.items.length > 0)

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-header">
          <span className="brand-mark"><Consent360Logo size={22} /></span>
          <div>
            <div className="brand-name">Consent360</div>
            <div className="brand-sub">Privacy Platform</div>
          </div>
        </div>
        <nav className="sidebar-nav">
          {visibleGroups.map((g, i) => (
            <div key={g.group}>
              <div className="nav-group">
                {i === 0 && isOrgAdmin ? `${orgLabel} Admin` : g.group}
              </div>
              {g.items.map((item) => <NavItem key={item.path} item={item} pathname={pathname} />)}
            </div>
          ))}
          {hasPermission('user.manage') && (
            <>
              <div className="nav-group">Administration</div>
              <Link to="/administration" className={`nav-item ${pathname.startsWith('/administration') ? 'active' : ''}`}><IconAdmin /> <span>Administration</span></Link>
              <Link to="/api-reference" className={`nav-item ${pathname.startsWith('/api-reference') ? 'active' : ''}`}><IconApi /> <span>API &amp; SDKs</span></Link>
              <Link to="/organizations" className={`nav-item ${pathname.startsWith('/organizations') ? 'active' : ''}`}><IconShield /> <span>Organizations</span></Link>
            </>
          )}
        </nav>
        <div className="sidebar-footer">
          <Avatar name={user?.full_name || user?.username || '?'} size={32} />
          <div className="sidebar-footer-text">
            <div className="sidebar-user">{user?.username}</div>
            <div className="sidebar-role">Signed in as {user?.role_name?.replace(/_/g, ' ')}</div>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <Breadcrumb items={crumbs} />
          <div className="topbar-user">
            <div className="topbar-user-text">
              <div className="topbar-name">{user?.full_name}</div>
              <div className="topbar-email">{user?.email}</div>
            </div>
            <Avatar name={user?.full_name || user?.username || '?'} size={32} />
            <button className="btn btn-ghost" style={{ padding: 7 }} title="Logout" onClick={handleLogout}><IconLogout size={16} /></button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
      <Chatbot />
    </div>
  )
}

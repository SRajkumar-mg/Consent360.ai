import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { usePageTitle } from '../hooks/usePageTitle'
import { Consent360Logo } from './Logo'
import { Chatbot } from './Chatbot'
import {
  IconAdmin, IconApi, IconAudit, IconBreach, IconCustomers, IconDashboard,
  IconGlobe, IconKey, IconLogout, IconNotification, IconPolicy, IconProcessor, IconPurpose,
  IconShield, IconUsers,
} from './icons'

const NAV = [
  { label: 'Overview', icon: IconDashboard, path: '/dashboard', exact: true, perm: 'dashboard.view' },
  { label: 'Customers', icon: IconCustomers, path: '/customers', perm: 'customer.view' },
  { label: 'Consent Purposes', icon: IconPurpose, path: '/purposes', perm: 'purpose.view' },
  { label: 'Policies', icon: IconPolicy, path: '/policies', perm: 'policy.view' },
  { label: 'Audit Explorer', icon: IconAudit, path: '/audit', exact: true, perm: 'audit.view' },

  { label: 'Notices', icon: IconGlobe, path: '/notices', perm: 'notice.manage' },
  { label: 'Legal Documents', icon: IconPolicy, path: '/legal-documents', perm: 'notice.manage' },
  { label: 'Tenant Settings', icon: IconUsers, path: '/tenants/settings', perm: 'tenant.manage' },
  { label: 'Rights & Erasure', icon: IconShield, path: '/rights', perm: 'rights.manage' },
  { label: 'Reports', icon: IconPurpose, path: '/reports', perm: 'reports.view' },
  { label: 'Breach Register', icon: IconBreach, path: '/breaches', perm: 'user.manage' },
  { label: 'Processors', icon: IconProcessor, path: '/processors', perm: 'user.manage' },
  { label: 'Notifications', icon: IconNotification, path: '/notifications', perm: 'user.manage' },
]

function NavItem({ item, pathname }: { item: (typeof NAV)[number]; pathname: string }) {
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
  const title = usePageTitle()
  const { pathname } = useLocation()
  const navigate = useNavigate()

  const isOrgAdmin = ['jobhub_admin', 'codex_admin', 'skilllearn_admin'].includes(user?.username || '')
  const orgLabel = isOrgAdmin ? (user?.username?.replace('_admin', '').toUpperCase() || 'Organization') : ''

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="logo-badge"><Consent360Logo size={18} /></div>
          <div>
            Consent<span style={{ color: 'var(--primary-accent)' }}>360</span>
          </div>
        </div>
        <nav className="sidebar-nav">
          {isOrgAdmin && (
            <div className="nav-group" style={{ padding: '4px 12px 6px' }}>
              {orgLabel} Admin
            </div>
          )}
          {!isOrgAdmin && <div className="nav-group">Platform</div>}
          {NAV.filter(item => !item.perm || hasPermission(item.perm)).map((item) => (
            <NavItem key={item.path} item={item} pathname={pathname} />
          ))}
          {hasPermission('user.manage') && (
            <>
              <div className="nav-group">Administration</div>
              <Link to="/administration" className={`nav-item ${pathname.startsWith('/administration') ? 'active' : ''}`}>
                <IconAdmin /> <span>Administration</span>
              </Link>
              <Link to="/tenants" className={`nav-item ${pathname === '/tenants' ? 'active' : ''}`}>
                <IconKey /> <span>Tenants &amp; API Keys</span>
              </Link>
              <Link to="/api-reference" className={`nav-item ${pathname.startsWith('/api-reference') ? 'active' : ''}`}>
                <IconApi /> <span>API &amp; SDKs</span>
              </Link>
              <Link to="/organizations" className={`nav-item ${pathname.startsWith('/organizations') ? 'active' : ''}`}>
                <IconShield /> <span>Organizations</span>
              </Link>
            </>
          )}
        </nav>
        <div className="sidebar-footer">
          <div style={{ marginBottom: 6 }}>Signed in as {user?.username}</div>
          <div>{user?.role_name?.replace(/_/g, ' ')}</div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-title">{title}</div>
          <div className="topbar-user">
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{user?.full_name}</div>
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{user?.role_name?.replace(/_/g, ' ')}</div>
            </div>
            <div className="avatar">{(user?.full_name || '?').slice(0, 2).toUpperCase()}</div>
            <button className="btn btn-ghost" style={{ padding: 7 }} title="Logout" onClick={handleLogout}>
              <IconLogout size={16} />
            </button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
      <Chatbot />
    </div>
  )
}

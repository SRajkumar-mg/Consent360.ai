import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { ToastProvider } from './components/ui'
import { Layout } from './components/Layout'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/Login'
import { DashboardPage } from './pages/Dashboard'
import { CustomersPage } from './pages/Customers'
import { CustomerConsentPage } from './pages/CustomerConsent'
import { ConsentDetailPage } from './pages/ConsentDetail'
import { AuditExplorerPage } from './pages/AuditExplorer'
import { PurposesPage } from './pages/Purposes'
import { PoliciesPage } from './pages/Policies'
import { AdministrationPage } from './pages/Administration'
import { OrganizationsPage } from './pages/Organizations'
import { ApiReferencePage } from './pages/ApiReference'
import { ContextLandingPage } from './pages/ContextLanding'
import { TenantSettingsPage } from './pages/TenantSettings'
import { NoticesPage } from './pages/Notices'
import { LegalDocumentsPage } from './pages/LegalDocuments'

import { ReportsPage } from './pages/Reports'
import { RightsPage } from './pages/Rights'
import { BreachesPage } from './pages/Breaches'
import { ProcessorsPage } from './pages/Processors'
import { NotificationsPage } from './pages/Notifications'
import { TenantsPage } from './pages/Tenants'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="center-load"><div className="spinner" /></div>
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

/**
 * Route-level permission gate. The backend is authoritative (every
 * protected endpoint enforces its own permission independently) - this only
 * prevents a logged-in-but-unpermitted user from reaching a page whose data
 * would otherwise fail to load, showing a clear access-denied state instead
 * of a broken page or relying solely on the nav item being hidden.
 */
function RequirePermission({ perm, children }: { perm: string; children: React.ReactNode }) {
  const { user, hasPermission } = useAuth()
  if (!hasPermission(perm)) {
    // data_principal / guardian accounts hold no staff-console permissions by
    // design (own-data scope only) - point them at the customer portal they
    // actually belong in, rather than leaving them on a dead-end wall.
    const isExternalUser = user?.role_name === 'data_principal' || user?.role_name === 'guardian'
    return (
      <div className="center-load">
        <div>
          <h2>Access denied</h2>
          <p>You don't have the <code>{perm}</code> permission required to view this page.</p>
          {isExternalUser && (
            <p>
              Looking to manage your own consent? Use the{' '}
              <a href="/portal/login">customer self-service portal</a> instead.
            </p>
          )}
        </div>
      </div>
    )
  }
  return <>{children}</>
}

function Shell({ children, perm }: { children: React.ReactNode; perm?: string }) {
  return (
    <RequireAuth>
      <Layout>
        {perm ? <RequirePermission perm={perm}>{children}</RequirePermission> : children}
      </Layout>
    </RequireAuth>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/dashboard" element={<Shell perm="dashboard.view"><DashboardPage /></Shell>} />
            <Route path="/customers" element={<Shell perm="customer.view"><CustomersPage /></Shell>} />
            <Route path="/customers/:customerId" element={<Shell perm="customer.view"><CustomerConsentPage /></Shell>} />
            <Route path="/consent/:consentId" element={<Shell perm="consent.view"><ConsentDetailPage /></Shell>} />
            <Route path="/consent/context/:token" element={<Shell><ContextLandingPage /></Shell>} />
            <Route path="/audit" element={<Shell perm="audit.view"><AuditExplorerPage /></Shell>} />
            <Route path="/purposes" element={<Shell perm="purpose.view"><PurposesPage /></Shell>} />
            <Route path="/notices" element={<Shell perm="notice.manage"><NoticesPage /></Shell>} />
        <Route path="/legal-documents" element={<Shell perm="notice.manage"><LegalDocumentsPage /></Shell>} />
            <Route path="/tenants" element={<Shell perm="user.manage"><TenantsPage /></Shell>} />
            <Route path="/tenants/settings" element={<Shell perm="tenant.manage"><TenantSettingsPage /></Shell>} />
            <Route path="/reports" element={<Shell perm="reports.view"><ReportsPage /></Shell>} />
            <Route path="/rights" element={<Shell perm="rights.manage"><RightsPage /></Shell>} />
            <Route path="/policies" element={<Shell perm="policy.view"><PoliciesPage /></Shell>} />
            <Route path="/administration" element={<Shell perm="user.manage"><AdministrationPage /></Shell>} />
            <Route path="/organizations" element={<Shell perm="user.manage"><OrganizationsPage /></Shell>} />
            <Route path="/api-reference" element={<Shell perm="user.manage"><ApiReferencePage /></Shell>} />
            <Route path="/breaches" element={<Shell perm="user.manage"><BreachesPage /></Shell>} />
            <Route path="/processors" element={<Shell perm="user.manage"><ProcessorsPage /></Shell>} />
            <Route path="/notifications" element={<Shell perm="user.manage"><NotificationsPage /></Shell>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  )
}

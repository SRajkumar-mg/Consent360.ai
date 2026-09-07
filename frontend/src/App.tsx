import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { ToastProvider } from './components/ui'
import { Layout } from './components/Layout'
import { LandingPage } from './pages/LandingPage'
import { LoginPage } from './pages/Login'
import { DashboardPage } from './pages/Dashboard'
import { ComplianceDashboardPage } from './pages/ComplianceDashboard'
import { CustomersPage } from './pages/Customers'
import { CustomerConsentPage } from './pages/CustomerConsent'
import { ConsentDetailPage } from './pages/ConsentDetail'
import { AuditExplorerPage } from './pages/AuditExplorer'
import { PurposesPage } from './pages/Purposes'
import { PoliciesPage } from './pages/Policies'
import { NoticesPage } from './pages/Notices'
import { GrievancesPage } from './pages/Grievances'
import { BreachesPage, BreachDetailPage } from './pages/Breaches'
import { ErasurePage } from './pages/Erasure'
import { ProcessorsPage } from './pages/Processors'
import { ReConsentPage } from './pages/ReConsent'
import { ConsentManagersPage } from './pages/ConsentManagers'
import { RecordsPage } from './pages/Records'
import { AdministrationPage } from './pages/Administration'
import { OrganizationsPage } from './pages/Organizations'
import { ApiReferencePage } from './pages/ApiReference'
import { ContextLandingPage } from './pages/ContextLanding'
import { PublicPortalPage } from './pages/PublicPortal'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="center-load"><div className="spinner" /></div>
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}

/**
 * R2-08: a screen reached by typing its URL must refuse for a stated reason.
 * The sidebar already hides what a role cannot read, but a bookmarked or shared
 * link bypasses the sidebar, and every one of these pages would otherwise render
 * a page of failed requests that reads as "the feature is broken" rather than
 * "your role does not have this".
 */
function RequirePermission({ perm, children }: { perm: string; children: React.ReactNode }) {
  const { hasPermission } = useAuth()
  if (hasPermission(perm)) return <>{children}</>
  return (
    <div>
      <div className="page-heading"><div><h1>Not available to your role</h1></div></div>
      <div className="alert alert-info">
        This screen requires the <span className="mono">{perm}</span> permission. Your role does not have it, so the
        page is not shown — nothing here is broken. An administrator can grant it on the Administration screen, and
        newly added permissions only take effect once the roles have been re-seeded from{' '}
        <span className="mono">app/core/rbac.py</span>.
      </div>
    </div>
  )
}

function Shell({ children, perm }: { children: React.ReactNode; perm?: string }) {
  return (
    <RequireAuth>
      <Layout>{perm ? <RequirePermission perm={perm}>{children}</RequirePermission> : children}</Layout>
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
            {/* Public, unauthenticated data-principal self-service route - deliberately
                outside RequireAuth/Shell. The context token lives only in this page's
                component state (via useSearchParams), never in localStorage/sessionStorage,
                and its calls never go through the staff `api` client in api/client.ts. */}
            <Route path="/portal/consent" element={<PublicPortalPage />} />
            <Route path="/dashboard" element={<Shell><DashboardPage /></Shell>} />
            <Route path="/compliance" element={<Shell perm="dashboard.view"><ComplianceDashboardPage /></Shell>} />
            <Route path="/customers" element={<Shell perm="customer.view"><CustomersPage /></Shell>} />
            <Route path="/customers/:customerId" element={<Shell perm="customer.view"><CustomerConsentPage /></Shell>} />
            <Route path="/consent/:consentId" element={<Shell perm="consent.view"><ConsentDetailPage /></Shell>} />
            <Route path="/consent/context/:token" element={<Shell><ContextLandingPage /></Shell>} />
            <Route path="/audit" element={<Shell perm="audit.view"><AuditExplorerPage /></Shell>} />
            <Route path="/purposes" element={<Shell perm="purpose.view"><PurposesPage /></Shell>} />
            <Route path="/notices" element={<Shell perm="purpose.view"><NoticesPage /></Shell>} />
            <Route path="/policies" element={<Shell perm="policy.view"><PoliciesPage /></Shell>} />
            <Route path="/re-consent" element={<Shell perm="policy.view"><ReConsentPage /></Shell>} />
            <Route path="/grievances" element={<Shell perm="grievance.view"><GrievancesPage /></Shell>} />
            <Route path="/breaches" element={<Shell perm="breach.view"><BreachesPage /></Shell>} />
            <Route path="/breaches/:breachRef" element={<Shell perm="breach.view"><BreachDetailPage /></Shell>} />
            <Route path="/erasure" element={<Shell perm="erasure.view"><ErasurePage /></Shell>} />
            <Route path="/processors" element={<Shell perm="policy.view"><ProcessorsPage /></Shell>} />
            <Route path="/consent-managers" element={<Shell perm="audit.view"><ConsentManagersPage /></Shell>} />
            <Route path="/records" element={<Shell perm="audit.view"><RecordsPage /></Shell>} />
            <Route path="/administration" element={<Shell perm="user.manage"><AdministrationPage /></Shell>} />
            <Route path="/organizations" element={<Shell perm="user.manage"><OrganizationsPage /></Shell>} />
            <Route path="/api-reference" element={<Shell><ApiReferencePage /></Shell>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  )
}

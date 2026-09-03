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

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <Layout>{children}</Layout>
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
            <Route path="/dashboard" element={<Shell><DashboardPage /></Shell>} />
            <Route path="/customers" element={<Shell><CustomersPage /></Shell>} />
            <Route path="/customers/:customerId" element={<Shell><CustomerConsentPage /></Shell>} />
            <Route path="/consent/:consentId" element={<Shell><ConsentDetailPage /></Shell>} />
            <Route path="/consent/context/:token" element={<Shell><ContextLandingPage /></Shell>} />
            <Route path="/audit" element={<Shell><AuditExplorerPage /></Shell>} />
            <Route path="/purposes" element={<Shell><PurposesPage /></Shell>} />
            <Route path="/policies" element={<Shell><PoliciesPage /></Shell>} />
            <Route path="/administration" element={<Shell><AdministrationPage /></Shell>} />
            <Route path="/tenants" element={<Shell><TenantsPage /></Shell>} />
            <Route path="/organizations" element={<Shell><OrganizationsPage /></Shell>} />
            <Route path="/api-reference" element={<Shell><ApiReferencePage /></Shell>} />
            <Route path="/breaches" element={<Shell><BreachesPage /></Shell>} />
            <Route path="/processors" element={<Shell><ProcessorsPage /></Shell>} />
            <Route path="/notifications" element={<Shell><NotificationsPage /></Shell>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  )
}

import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

const titles: Record<string, string> = {
  '/': 'Home',
  '/dashboard': 'Dashboard',
  '/compliance': 'Compliance KPIs',
  '/customers': 'Customers',
  '/audit': 'Audit Explorer',
  '/purposes': 'Consent Purposes',
  '/notices': 'Notices',
  '/policies': 'Consent Policies',
  '/re-consent': 'Re-consent & Change Log',
  '/grievances': 'Rights & Grievances',
  '/breaches': 'Breach Register',
  '/erasure': 'Retention & Erasure',
  '/processors': 'Processors & Contracts',
  '/consent-managers': 'Consent Managers',
  '/records': 'Delivery & Receipts',
  '/administration': 'Administration',
  '/organizations': 'Organizations',
  '/api-reference': 'API & SDKs',
}

function matchTitle(pathname: string): string | null {
  if (pathname.startsWith('/customers/')) return 'Customer Consent'
  if (pathname.startsWith('/audit/')) return 'Audit Event'
  if (pathname.startsWith('/breaches/')) return 'Breach'
  if (pathname.startsWith('/consent/context/')) return 'Customer Consent Dashboard'
  if (pathname.startsWith('/consent/')) return 'Consent Record'
  return null
}

export function usePageTitle() {
  const { pathname } = useLocation()
  const [title, setTitle] = useState('')

  useEffect(() => {
    setTitle(matchTitle(pathname) ?? titles[pathname] ?? 'Consent Management Platform')
  }, [pathname])

  useEffect(() => {
    document.title = `${title} · Consent Management Platform`
  }, [title])

  return title
}

export function useBreadcrumb(): Array<{ label: string; to?: string }> {
  const { pathname } = useLocation()
  if (pathname.startsWith('/customers/')) return [{ label: '← Customers', to: '/customers' }, { label: 'Customer Consent' }]
  if (pathname.startsWith('/breaches/')) return [{ label: '← Breach register', to: '/breaches' }, { label: 'Breach' }]
  if (pathname.startsWith('/consent/')) return [{ label: 'Customers', to: '/customers' }, { label: 'Consent Record' }]
  const title = titles[pathname] ?? 'Consent Management Platform'
  return [{ label: 'Platform', to: '/dashboard' }, { label: title }]
}

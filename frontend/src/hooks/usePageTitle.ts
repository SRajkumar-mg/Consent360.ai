import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

const titles: Record<string, string> = {
  '/': 'Home',
  '/dashboard': 'Dashboard',
  '/customers': 'Customers',
  '/audit': 'Audit Explorer',
  '/purposes': 'Consent Purposes',
  '/policies': 'Consent Policies',
  '/administration': 'Administration',
  '/tenants': 'Tenants & API Keys',
  '/organizations': 'Organizations',
  '/api-reference': 'API & SDKs',
  '/breaches': 'Breach Register',
  '/processors': 'Processor Register',
  '/notifications': 'Notifications',
}

function matchTitle(pathname: string): string | null {
  if (pathname.startsWith('/customers/')) return 'Customer Consent'
  if (pathname.startsWith('/audit/')) return 'Audit Event'
  if (pathname.startsWith('/consent/context/')) return 'Customer Consent Dashboard'
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

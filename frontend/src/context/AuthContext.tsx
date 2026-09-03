import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { authApi } from '../api'
import type { User } from '../types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (username: string, password: string) => Promise<User>
  logout: () => void
  hasPermission: (permission: string) => boolean
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('cmp_access_token')
    if (!token) {
      setLoading(false)
      return
    }
    authApi
      .me()
      .then((res) => setUser(res.data))
      .catch(() => {
        localStorage.removeItem('cmp_access_token')
        localStorage.removeItem('cmp_refresh_token')
      })
      .finally(() => setLoading(false))
  }, [])

  const login = async (username: string, password: string) => {
    const res = await authApi.login(username, password)
    localStorage.setItem('cmp_access_token', res.data.access_token)
    localStorage.setItem('cmp_refresh_token', res.data.refresh_token)
    setUser(res.data.user)
    return res.data.user
  }

  const logout = () => {
    authApi.logout().catch(() => undefined)
    localStorage.removeItem('cmp_access_token')
    localStorage.removeItem('cmp_refresh_token')
    setUser(null)
  }

  const hasPermission = (permission: string) => {
    if (!user) return false
    return user.role_permissions.includes(permission)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, hasPermission }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

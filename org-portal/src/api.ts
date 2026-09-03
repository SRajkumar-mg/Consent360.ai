import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('org_access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('org_access_token')
      localStorage.removeItem('org_user')
      localStorage.removeItem('org_organization')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export const orgApi = {
  login: (data: { username: string; password: string }) =>
    api.post('/organizations/auth/login', data),
  getDashboard: () => api.get('/organizations/portal/dashboard'),
}

export function getErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) return err.response?.data?.detail || err.message
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}

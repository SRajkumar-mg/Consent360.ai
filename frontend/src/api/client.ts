import axios from 'axios'

const baseURL = import.meta.env.VITE_API_URL || '/api'

export const api = axios.create({ baseURL })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('cmp_access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config
    const isPortal = original.url?.startsWith('/portal')
    if (error.response?.status === 401 && !original._retry && !original.url.includes('/auth/login') && !isPortal) {
      original._retry = true
      const refreshToken = localStorage.getItem('cmp_refresh_token')
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${baseURL}/auth/refresh`, { refresh_token: refreshToken })
          localStorage.setItem('cmp_access_token', data.access_token)
          localStorage.setItem('cmp_refresh_token', data.refresh_token)
          original.headers.Authorization = `Bearer ${data.access_token}`
          return api(original)
        } catch {
          localStorage.removeItem('cmp_access_token')
          localStorage.removeItem('cmp_refresh_token')
          window.location.href = '/login'
        }
      }
    }
    return Promise.reject(error)
  },
)

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d: { msg?: string }) => d.msg || 'Validation error').join('; ')
    }
    return error.message
  }
  return 'An unexpected error occurred'
}

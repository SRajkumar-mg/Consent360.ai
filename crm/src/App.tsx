import { Navigate, Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { UsersPage } from './pages/UsersPage'
import { CookiePolicy } from './pages/CookiePolicy'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/users" element={<UsersPage />} />
      <Route path="/cookie-policy" element={<CookiePolicy />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
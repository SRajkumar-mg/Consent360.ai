import { Routes, Route, Navigate } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { PlatformPage } from './pages/PlatformPage'
import { CookiePolicyPage } from './pages/CookiePolicyPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/learn" element={<PlatformPage />} />
      <Route path="/cookie-policy" element={<CookiePolicyPage />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}

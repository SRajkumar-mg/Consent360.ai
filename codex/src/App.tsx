import { Routes, Route, Navigate } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { PlatformPage } from './pages/PlatformPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/platform" element={<PlatformPage />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}

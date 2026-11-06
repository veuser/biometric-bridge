import { useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api, setUnauthorizedHandler } from './api/client'
import type { CurrentUser } from './api/types'
import Layout from './components/Layout'
import { Loading } from './components/Ui'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import DevicesPage from './pages/DevicesPage'
import HrmsPage from './pages/HrmsPage'
import SyncPage from './pages/SyncPage'
import EnrollmentPage from './pages/EnrollmentPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'
import UsersPage from './pages/UsersPage'
import LogsPage from './pages/LogsPage'

export default function App() {
  const navigate = useNavigate()

  useEffect(() => {
    // Any 401 anywhere in the app returns the operator to sign-in rather than
    // leaving a page half-rendered with an error.
    setUnauthorizedHandler(() => navigate('/login', { replace: true }))
  }, [navigate])

  const { data: user, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<CurrentUser>('/api/auth/me'),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="container py-5">
        <Loading label="Starting..." />
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Layout user={user}>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/devices" element={<DevicesPage />} />
        <Route path="/hrms" element={<HrmsPage />} />
        <Route path="/sync" element={<SyncPage />} />
        <Route path="/enrollment" element={<EnrollmentPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

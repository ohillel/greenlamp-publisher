import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import Login from './pages/Login'
import ClientsPage from './pages/ClientsPage'
import ClientPage from './pages/ClientPage'

function RootRedirect() {
  const { role, loading } = useAuth()
  if (loading) return <div className="loading">Loading…</div>
  if (!role)   return <Navigate to="/login" replace />
  return <Navigate to="/clients" replace />
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/"                    element={<RootRedirect />} />
          <Route path="/login"               element={<Login />} />
          <Route path="/clients"             element={<ProtectedRoute><ClientsPage /></ProtectedRoute>} />
          <Route path="/clients/:clientId"   element={<ProtectedRoute><ClientPage /></ProtectedRoute>} />
          <Route path="*"                    element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

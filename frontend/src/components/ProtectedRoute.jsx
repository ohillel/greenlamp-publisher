import { useRef } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function ProtectedRoute({ children }) {
  const { loading, role } = useAuth()
  const location = useLocation()

  // Once we have confirmed the user is authenticated (role is set), remember
  // that fact so transient auth events can't unmount the page and lose form state.
  const wasAuthenticated = useRef(false)
  if (role) wasAuthenticated.current = true

  // Initial auth check not yet complete — show loading screen only on first load.
  if (loading && !wasAuthenticated.current) {
    return <div className="loading">Loading…</div>
  }

  // Not authenticated — redirect to login, preserving the intended URL so
  // Login can redirect back to it after a successful sign-in.
  if (!role) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  return children
}

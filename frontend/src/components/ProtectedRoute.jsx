import { useRef } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function ProtectedRoute({ children }) {
  const { loading, role } = useAuth()

  // Once we have confirmed the user is authenticated (role is set), remember
  // that fact so transient auth events can't unmount the page and lose form state.
  const wasAuthenticated = useRef(false)
  if (role) wasAuthenticated.current = true

  // Initial auth check not yet complete — show loading screen only on first load.
  if (loading && !wasAuthenticated.current) {
    return <div className="loading">Loading…</div>
  }

  // Not authenticated — redirect to login.
  // After wasAuthenticated is true, this only triggers on an actual sign-out
  // (role goes null because AuthContext explicitly cleared it).
  if (!role) return <Navigate to="/login" replace />

  return children
}

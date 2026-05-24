import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth, useAuthProfile } from '../context/AuthContext'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const SWITCH_TARGETS = [
  { email: 'denise@greenlamp.co',  initial: 'D', label: 'Switch to Denise', color: '#7c3aed' },
  { email: 'office@greenlamp.co',  initial: 'E', label: 'Switch to Eden',   color: '#0369a1' },
]

function UserSwitcher() {
  const [switchingTo, setSwitchingTo] = useState(null)

  const handleSwitch = async (target) => {
    if (switchingTo) return
    setSwitchingTo(target.email)
    try {
      const res  = await fetch(`${API_BASE}/api/admin/switch-user`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ target_email: target.email }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Switch failed')
      // Navigate to the magic link — Supabase consumes it and redirects back to the app
      window.location.href = data.action_link
    } catch (err) {
      console.error('[switch-user]', err)
      setSwitchingTo(null)
    }
  }

  return (
    <div className="user-switcher" title="Switch account">
      {SWITCH_TARGETS.map(target => (
        <button
          key={target.email}
          className="user-switch-btn"
          style={{ '--switch-color': target.color }}
          title={target.label}
          onClick={() => handleSwitch(target)}
          disabled={!!switchingTo}
        >
          {switchingTo === target.email ? '…' : target.initial}
        </button>
      ))}
    </div>
  )
}

export default function Layout({ title, children }) {
  // useAuthProfile re-renders only when user email/avatar changes (login/logout).
  // Page content (children) is unaffected by these topbar-only updates.
  const { user }          = useAuthProfile()
  const { signOut, role } = useAuth()

  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="topbar-brand">
          <span className="logo-dot" /> Greenlamp Publisher
        </span>
        <div className="topbar-right">
          {role === 'or' && (
            <>
              <UserSwitcher />
              <Link to="/users" className="topbar-nav-link">Users</Link>
            </>
          )}
          <span className="topbar-email">{user?.email}</span>
          <button className="btn-signout" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <main className="page-content">
        {title && <h1 className="page-title">{title}</h1>}
        {children}
      </main>
    </div>
  )
}

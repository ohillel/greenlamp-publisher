import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth, useAuthProfile } from '../context/AuthContext'
import { supabase } from '../lib/supabase'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const ACCOUNTS = {
  // Or uses Gmail OAuth — no password; session is created server-side via admin API
  or:        { email: 'seojobisrael@gmail.com', useAdminSwitch: true,  initial: 'O', label: 'Switch to Or',     color: '#16a34a' },
  denise:    { email: 'denise@greenlamp.co',    password: 'Greenlamp1!', initial: 'D', label: 'Switch to Denise', color: '#7c3aed' },
  publisher: { email: 'office@greenlamp.co',    password: 'Greenlamp1!', initial: 'E', label: 'Switch to Eden',   color: '#0369a1' },
}

// Which accounts each role can switch into
const SWITCH_TARGETS_BY_ROLE = {
  or:        [ACCOUNTS.denise, ACCOUNTS.publisher],
  denise:    [ACCOUNTS.or],
  publisher: [ACCOUNTS.or],
}

function UserSwitcher({ role }) {
  const [switchingTo, setSwitchingTo] = useState(null)
  const [switchError,  setSwitchError]  = useState('')
  const navigate = useNavigate()

  const targets = SWITCH_TARGETS_BY_ROLE[role] ?? []
  if (targets.length === 0) return null

  const handleSwitch = async (target) => {
    if (switchingTo) return
    setSwitchingTo(target.email)
    setSwitchError('')
    try {
      if (target.useAdminSwitch) {
        // Or's account uses Gmail OAuth — create session via backend admin API
        console.log('[switch-user] POSTing to', `${API_BASE}/api/admin/switch-user`, 'for', target.email)
        const res = await fetch(`${API_BASE}/api/admin/switch-user`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ target_email: target.email }),
        })
        console.log('[switch-user] HTTP status:', res.status)
        const body = await res.json()
        console.log('[switch-user] full response body:', JSON.stringify(body))
        if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
        const { access_token, refresh_token } = body
        console.log('[switch-user] access_token present:', !!access_token, '| refresh_token present:', !!refresh_token)
        const { data: sessionData, error: sessionError } = await supabase.auth.setSession({ access_token, refresh_token })
        console.log('[switch-user] setSession data:', JSON.stringify(sessionData), '| error:', sessionError)
        if (sessionError) throw sessionError
      } else {
        const { error } = await supabase.auth.signInWithPassword({
          email:    target.email,
          password: target.password,
        })
        if (error) throw error
      }
      navigate('/clients')
    } catch (err) {
      console.error('[switch-user] FAILED:', err)
      setSwitchError(err.message || 'Switch failed')
      setSwitchingTo(null)
    }
  }

  return (
    <div className="user-switcher">
      {targets.map(target => (
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
      {switchError && (
        <span className="user-switch-error" title={switchError}>!</span>
      )}
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
          <UserSwitcher key={role} role={role} />
          {role === 'or' && (
            <Link to="/users" className="topbar-nav-link">Users</Link>
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

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { supabase } from '../lib/supabase'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function registerPush(userId) {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      console.log('[push] not supported in this browser')
      return
    }

    const vapidKey = import.meta.env.VITE_VAPID_PUBLIC_KEY
    if (!vapidKey) {
      console.warn('[push] VITE_VAPID_PUBLIC_KEY is not set — skipping subscription')
      return
    }

    const permission = await Notification.requestPermission()
    console.log('[push] notification permission:', permission)
    if (permission !== 'granted') return

    const registration = await navigator.serviceWorker.register('/sw.js')
    await navigator.serviceWorker.ready
    console.log('[push] service worker ready')

    // Convert base64url VAPID public key to Uint8Array
    const padding   = '='.repeat((4 - vapidKey.length % 4) % 4)
    const base64    = (vapidKey + padding).replace(/-/g, '+').replace(/_/g, '/')
    const rawKey    = Uint8Array.from(atob(base64), c => c.charCodeAt(0))

    // If an existing subscription was created with a different key it will
    // throw on subscribe() — unsubscribe first to get a clean state.
    const existing = await registration.pushManager.getSubscription()
    if (existing) {
      await existing.unsubscribe()
      console.log('[push] unsubscribed stale subscription')
    }

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly:      true,
      applicationServerKey: rawKey,
    })
    console.log('[push] subscribed, posting to server…')

    const res = await fetch(`${API_BASE}/api/push/subscribe`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ user_id: userId, subscription: subscription.toJSON() }),
    })
    console.log('[push] /api/push/subscribe response:', res.status)
  } catch (err) {
    console.warn('[push] registration failed:', err)
  }
}

// ─── Contexts ───────────────────────────────────────────────────────────────
// Split into two so the topbar (email / sign-out) can subscribe without
// re-rendering the entire page when auth-profile data changes.
const AuthContext        = createContext(null)  // { loading, role, signOut }
const AuthProfileContext = createContext(null)  // { user }

// ─── Provider ────────────────────────────────────────────────────────────────
export function AuthProvider({ children }) {
  const [user,    setUser]    = useState(null)
  const [role,    setRole]    = useState(null)
  const [loading, setLoading] = useState(true)

  // Tracks the ID of the currently authenticated user in a ref so that
  // token-refresh events (same user, new JWT) can be detected and ignored
  // without triggering any state updates or consumer re-renders.
  const authedUserIdRef = useRef(null)

  const fetchRole = async (userId) => {
    try {
      const result = await Promise.race([
        supabase.from('profiles').select('role').eq('id', userId).single(),
        new Promise(resolve =>
          setTimeout(() => resolve({ data: null, error: new Error('timeout') }), 3000)
        ),
      ])
      const { data, error } = result
      if (error) return null
      return data?.role ?? null
    } catch {
      return null
    }
  }

  useEffect(() => {
    let mounted = true

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (_event, session) => {
        if (!mounted) return

        if (session?.user) {
          const userId   = session.user.id
          const isNewUser = userId !== authedUserIdRef.current

          if (isNewUser) {
            // Genuine new login or first page load — update user and fetch role.
            authedUserIdRef.current = userId
            setUser(session.user)

            const r = await fetchRole(userId)
            if (!mounted) return   // stale mount (StrictMode) — the live mount handles it
            setRole(r)

            // Register push subscription in the background — non-blocking
            registerPush(userId)
          }
          // If same user (TOKEN_REFRESHED, etc.) skip all state updates.
          // user/role are already correct; no re-render needed.

          setLoading(false)
        } else {
          // Signed out
          authedUserIdRef.current = null
          setUser(null)
          setRole(null)
          setLoading(false)
        }
      }
    )

    // Safety net: if onAuthStateChange never fires (stale/corrupt localStorage),
    // don't leave the app stuck on "Loading…" indefinitely.
    const fallback = setTimeout(() => {
      if (mounted) setLoading(false)
    }, 5000)

    return () => {
      mounted = false
      // Reset so the next effect invocation (React StrictMode double-invoke)
      // treats the first session as a fresh login rather than a same-user event.
      authedUserIdRef.current = null
      clearTimeout(fallback)
      subscription.unsubscribe()
    }
  }, [])

  // useCallback so the function reference is stable across renders —
  // prevents useMemo below from generating a new context object every render.
  const signOut = useCallback(async () => {
    setUser(null)
    setRole(null)

    Object.keys(localStorage).forEach(key => {
      if (key.startsWith('sb-')) localStorage.removeItem(key)
    })

    await supabase.auth.signOut()
  }, [])

  // ── Split context values ──────────────────────────────────────────────────
  // authValue: consumed by ProtectedRoute, page components, any role-gated UI.
  // Stable as long as loading/role/signOut don't change — TOKEN_REFRESHED won't
  // touch any of these, so page components won't re-render.
  const authValue = useMemo(
    () => ({ loading, role, signOut }),
    [loading, role, signOut]
  )

  // profileValue: consumed only by the topbar (Layout).
  // Kept separate so a future email/avatar update doesn't re-render every page.
  const profileValue = useMemo(
    () => ({ user }),
    [user]
  )

  return (
    <AuthContext.Provider value={authValue}>
      <AuthProfileContext.Provider value={profileValue}>
        {children}
      </AuthProfileContext.Provider>
    </AuthContext.Provider>
  )
}

// ─── Hooks ───────────────────────────────────────────────────────────────────

// Used by ProtectedRoute and page components (role-gated rendering, signOut).
export const useAuth = () => useContext(AuthContext)

// Used by Layout / topbar only — isolates re-renders caused by user object
// changes from the rest of the component tree.
export const useAuthProfile = () => useContext(AuthProfileContext)

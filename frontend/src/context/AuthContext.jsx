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

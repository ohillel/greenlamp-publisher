import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import Layout from '../components/Layout'

const API_BASE = 'https://greenlamp-publisher-production-75fd.up.railway.app'

const ROLE_LABEL = {
  or:        'Or',
  publisher: 'Publisher (Eden)',
  denise:    'Denise',
}

export default function UsersPage() {
  const { role } = useAuth()
  const navigate = useNavigate()

  const [users,       setUsers]       = useState([])
  const [loadError,   setLoadError]   = useState('')
  const [activeId,    setActiveId]    = useState(null)   // user whose form is open
  const [newPassword, setNewPassword] = useState('')
  const [saving,      setSaving]      = useState(false)
  const [saveError,   setSaveError]   = useState('')
  const [saveSuccess, setSaveSuccess] = useState('')     // email of last success

  // Guard — only Or may access this page
  useEffect(() => {
    if (role && role !== 'or') navigate('/', { replace: true })
  }, [role, navigate])

  useEffect(() => {
    if (role !== 'or') return
    fetch(`${API_BASE}/api/admin/users`)
      .then(r => r.json())
      .then(d => {
        if (d.users) {
          // Sort by a stable order: or → publisher → denise → others
          const order = ['or', 'publisher', 'denise']
          const sorted = [...d.users].sort(
            (a, b) => order.indexOf(a.role) - order.indexOf(b.role)
          )
          setUsers(sorted)
        } else {
          setLoadError('Failed to load users.')
        }
      })
      .catch(() => setLoadError('Failed to load users.'))
  }, [role])

  function openForm(userId) {
    setActiveId(userId)
    setNewPassword('')
    setSaveError('')
    setSaveSuccess('')
  }

  function closeForm() {
    setActiveId(null)
    setNewPassword('')
    setSaveError('')
  }

  async function handleSave(user) {
    if (!newPassword) { setSaveError('Enter a new password.'); return }
    if (newPassword.length < 6) { setSaveError('Must be at least 6 characters.'); return }
    setSaving(true)
    setSaveError('')
    try {
      const res = await fetch(`${API_BASE}/api/admin/change-password`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user_id: user.id, new_password: newPassword }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Error')
      setSaveSuccess(user.email)
      setActiveId(null)
      setNewPassword('')
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (role !== 'or') return null

  return (
    <Layout title="User Management">
      {loadError && <p className="form-error">{loadError}</p>}

      {saveSuccess && (
        <p className="users-success">
          ✓ Password updated for {saveSuccess}
        </p>
      )}

      <div className="users-list">
        {users.map(user => (
          <div key={user.id} className="user-row">
            <div className="user-info">
              <span className="user-name">{ROLE_LABEL[user.role] ?? user.role}</span>
              <span className="user-email">{user.email}</span>
            </div>

            {activeId === user.id ? (
              <form
                className="pw-form"
                onSubmit={e => { e.preventDefault(); handleSave(user) }}
              >
                <input
                  type="password"
                  className="pw-input"
                  placeholder="New password"
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                  autoFocus
                  minLength={6}
                />
                {saveError && <span className="pw-error">{saveError}</span>}
                <div className="pw-actions">
                  <button
                    type="submit"
                    className="btn-primary pw-save"
                    disabled={saving}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    type="button"
                    className="btn-cancel"
                    onClick={closeForm}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                className="btn-change-pw"
                onClick={() => openForm(user.id)}
              >
                Change Password
              </button>
            )}
          </div>
        ))}
      </div>
    </Layout>
  )
}

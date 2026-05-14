import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'

export default function ClientsPage() {
  const [clients,    setClients]    = useState([])
  const [loading,    setLoading]    = useState(true)
  const [search,     setSearch]     = useState('')
  const [adding,     setAdding]     = useState(false)
  const [newName,    setNewName]    = useState('')
  const [saving,     setSaving]     = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const inputRef = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    supabase.from('clients').select('id, name').order('name')
      .then(({ data }) => { setClients(data ?? []); setLoading(false) })
  }, [])

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  const addClient = async () => {
    const name = newName.trim()
    if (!name) return
    setSaving(true)
    const { data, error } = await supabase
      .from('clients').insert({ name }).select().single()
    if (!error) {
      setClients(prev => [...prev, data].sort((a, b) => a.name.localeCompare(b.name)))
      setNewName('')
      setAdding(false)
    }
    setSaving(false)
  }

  const cancelAdd = () => { setAdding(false); setNewName('') }

  const deleteClient = async (e, id, name) => {
    e.stopPropagation()
    if (!window.confirm(`Delete "${name}"?\nThis will also delete all their articles.`)) return
    setDeletingId(id)
    const { error } = await supabase.from('clients').delete().eq('id', id)
    if (!error) setClients(prev => prev.filter(c => c.id !== id))
    setDeletingId(null)
  }

  const q = search.trim().toLowerCase()
  const visibleClients = q
    ? clients.filter(c => c.name.toLowerCase().includes(q))
    : clients

  return (
    <Layout title="Clients">
      {/* ── Toolbar ── */}
      <div className="clients-toolbar">
        {adding ? (
          <div className="add-client-row">
            <input
              ref={inputRef}
              className="add-client-input"
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') addClient(); if (e.key === 'Escape') cancelAdd() }}
              placeholder="Client name"
              maxLength={100}
            />
            <button className="btn-save" onClick={addClient} disabled={saving || !newName.trim()}>
              {saving ? 'Saving…' : 'Add'}
            </button>
            <button className="btn-ghost" onClick={cancelAdd}>Cancel</button>
          </div>
        ) : (
          <button className="btn-add-client" onClick={() => setAdding(true)}>
            + New Client
          </button>
        )}
      </div>

      {/* ── Search ── */}
      {!loading && clients.length > 0 && (
        <div className="clients-search-wrap">
          <input
            className="clients-search"
            type="search"
            placeholder="Search clients…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      )}

      {/* ── Grid ── */}
      {loading ? (
        <div className="loading-inline">Loading…</div>
      ) : visibleClients.length === 0 ? (
        <div className="empty-state">
          {search ? <p>No clients match "{search}".</p> : <p>No clients yet. Add your first one above.</p>}
        </div>
      ) : (
        <div className="clients-grid">
          {visibleClients.map(client => (
            <div
              key={client.id}
              className={`client-card ${deletingId === client.id ? 'deleting' : ''}`}
              onClick={() => navigate(`/clients/${client.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && navigate(`/clients/${client.id}`)}
            >
              <div className="folder-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
                </svg>
              </div>
              <span className="client-name">{client.name}</span>
              <button
                className="client-delete-btn"
                onClick={e => deleteClient(e, client.id, client.name)}
                disabled={deletingId === client.id}
                title="Delete client"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </Layout>
  )
}

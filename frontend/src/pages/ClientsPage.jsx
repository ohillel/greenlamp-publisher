import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// Which article status triggers the red indicator per role (null = no indicator)
const PENDING_STATUS = {
  publisher: 'approved',
}

const PUB_LABEL = { presswhizz: 'PressWhizz', linksme: 'Links.me' }

const fmtPrice = v => (v != null ? `$${Number(v).toLocaleString()}` : null)

const fmtDate = dateStr => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export default function ClientsPage() {
  const [clients,          setClients]          = useState([])
  const [loading,          setLoading]          = useState(true)
  const [search,           setSearch]           = useState('')
  const [adding,           setAdding]           = useState(false)
  const [newName,          setNewName]          = useState('')
  const [saving,           setSaving]           = useState(false)
  const [deletingId,       setDeletingId]       = useState(null)
  const [pendingClientIds, setPendingClientIds] = useState(new Set())

  // Or — pending articles table
  const [pendingArticles, setPendingArticles] = useState([])
  const [popup,           setPopup]           = useState(null)   // { article, x, y }

  const inputRef = useRef(null)
  const navigate = useNavigate()
  const { role } = useAuth()

  const pendingStatus = PENDING_STATUS[role] ?? null

  // ── Fetch clients ────────────────────────────────────────────────────────────

  useEffect(() => {
    supabase.from('clients').select('id, name').order('name')
      .then(({ data }) => { setClients(data ?? []); setLoading(false) })
  }, [])

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  // ── Or: fetch pending articles (submitted + approved) with full data ──────────

  const refreshOrArticles = useCallback(async () => {
    if (role !== 'or') return
    const { data } = await supabase
      .from('articles')
      .select('id, client_id, status, created_at, magazine, google_doc_url, chosen_publisher, preferred_publisher, price_presswhizz, price_linksme, clients(name)')
      .eq('status', 'submitted')
      .order('created_at', { ascending: true })
    const articles = data ?? []
    setPendingArticles(articles)
    setPendingClientIds(new Set(articles.map(a => a.client_id)))
  }, [role])

  useEffect(() => { refreshOrArticles() }, [refreshOrArticles])

  // ── Publisher: pending client indicators ─────────────────────────────────────

  const refreshPending = useCallback(async () => {
    if (!pendingStatus) return
    const { data } = await supabase
      .from('articles')
      .select('client_id')
      .eq('status', pendingStatus)
    if (data) setPendingClientIds(new Set(data.map(r => r.client_id)))
  }, [pendingStatus])

  useEffect(() => { refreshPending() }, [refreshPending])

  // Realtime: keep indicators accurate
  useEffect(() => {
    if (role !== 'or' && !pendingStatus) return
    const channel = supabase
      .channel('articles-pending-indicators')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'articles' }, () => {
        if (role === 'or') refreshOrArticles()
        else refreshPending()
      })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [role, pendingStatus, refreshOrArticles, refreshPending])

  // ── Close popup on outside click ─────────────────────────────────────────────

  useEffect(() => {
    if (!popup) return
    const close = e => { setPopup(null) }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [popup])

  // ── Mutations ────────────────────────────────────────────────────────────────

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

  // ── Render ───────────────────────────────────────────────────────────────────

  const q = search.trim().toLowerCase()
  const visibleClients = q
    ? clients.filter(c => c.name.toLowerCase().includes(q))
    : clients

  const upToDateClients = visibleClients.filter(c => !pendingClientIds.has(c.id))

  const renderClientFolder = client => (
    <div
      key={client.id}
      className={`client-card${deletingId === client.id ? ' deleting' : ''}`}
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
  )

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

      {/* ── Search (non-Or roles) ── */}
      {!loading && clients.length > 0 && role !== 'or' && (
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

      {/* ── Or layout: pending table + up-to-date folders (or flat grid when none pending) ── */}
      {role === 'or' && !loading && pendingArticles.length === 0 && (
        <div className="clients-grid">
          {visibleClients.map(renderClientFolder)}
        </div>
      )}
      {role === 'or' && !loading && pendingArticles.length > 0 && (
        <div className="or-clients-layout">

          {/* Left: pending articles table */}
          <div className="or-pending-col">
            <div className="clients-col-title pending">
              Needs attention
              {pendingArticles.length > 0 && (
                <span className="pending-count">{pendingArticles.length}</span>
              )}
            </div>

            {pendingArticles.length > 0 && (
              <div className="pending-table-wrap">
                <table className="pending-articles-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Client</th>
                      <th>Submitted</th>
                      <th>Magazine</th>
                      <th>Doc</th>
                      <th>Publisher</th>
                      <th>Prices</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pendingArticles.map((article, i) => {
                      const effPub = article.chosen_publisher || article.preferred_publisher
                      const pw = article.price_presswhizz
                      const lm = article.price_linksme
                      const priceStr = (pw == null && lm == null)
                        ? (article.status === 'submitted' ? 'Fetching…' : '—')
                        : [pw != null && `PressWhizz: ${fmtPrice(pw)}`, lm != null && `Links.me: ${fmtPrice(lm)}`]
                            .filter(Boolean).join(' | ')
                      return (
                        <tr
                          key={article.id}
                          className={`pending-article-row status-${article.status}`}
                          onClick={e => {
                            e.stopPropagation()
                            if (popup?.article.id === article.id) { setPopup(null); return }
                            const rect = e.currentTarget.getBoundingClientRect()
                            setPopup({ article, x: rect.left, y: rect.bottom })
                          }}
                        >
                          <td className="col-num">{i + 1}</td>
                          <td className="col-client">{article.clients?.name ?? '—'}</td>
                          <td className="col-date">{fmtDate(article.created_at)}</td>
                          <td className="col-magazine">{article.magazine ?? '—'}</td>
                          <td className="col-doc">
                            {article.google_doc_url
                              ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="doc-link">Doc ↗</a>
                              : <span className="cf-empty">—</span>
                            }
                          </td>
                          <td className="col-publisher">
                            <span className={effPub ? 'pub-tag' : 'cf-empty'}>
                              {PUB_LABEL[effPub] ?? '—'}
                            </span>
                          </td>
                          <td className="col-prices">
                            {pw == null && lm == null
                              ? <span className="price-fetching-inline"><span className="price-spinner" /> Fetching…</span>
                              : <span>{priceStr}</span>
                            }
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Right: up-to-date folders */}
          <div className="or-uptodate-col">
            <div className="clients-col-title">Up to date</div>
            {upToDateClients.length === 0 ? (
              <div className="empty-state" style={{ padding: '24px 0' }}>
                <p>No clients yet.</p>
              </div>
            ) : (
              <div className="clients-grid">
                {upToDateClients.map(renderClientFolder)}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Publisher layout: pending + up-to-date folders ── */}
      {role === 'publisher' && !loading && (
        visibleClients.length === 0 ? (
          <div className="empty-state">
            {search ? <p>No clients match "{search}".</p> : <p>No clients yet.</p>}
          </div>
        ) : (
          <div className="clients-split">
            {[true, false].map(wantPending => {
              const col = visibleClients.filter(c => pendingClientIds.has(c.id) === wantPending)
              if (col.length === 0) return null
              return (
                <div key={String(wantPending)} className="clients-col">
                  <div className={`clients-col-title${wantPending ? ' pending' : ''}`}>
                    {wantPending ? 'Needs attention' : 'Up to date'}
                  </div>
                  <div className="clients-grid">
                    {col.map(client => (
                      <div
                        key={client.id}
                        className={`client-card${wantPending ? ' client-card--pending' : ''}${deletingId === client.id ? ' deleting' : ''}`}
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
                </div>
              )
            })}
          </div>
        )
      )}

      {/* ── Denise and other roles: flat grid ── */}
      {role !== 'or' && role !== 'publisher' && (
        loading ? (
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
                className={`client-card${deletingId === client.id ? ' deleting' : ''}`}
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
        )
      )}

      {/* ── Row popup ── */}
      {popup && (
        <div
          className="row-action-popup"
          style={{ position: 'fixed', top: popup.y + 4, left: popup.x, zIndex: 1000 }}
          onClick={e => e.stopPropagation()}
        >
          <button
            className="row-popup-btn row-popup-approve"
            onClick={() => {
              setPopup(null)
              navigate(`/clients/${popup.article.client_id}?approve=${popup.article.id}`)
            }}
          >
            Approve
          </button>
          <button
            className="row-popup-btn"
            onClick={() => { setPopup(null); navigate(`/clients/${popup.article.client_id}`) }}
          >
            Go to client folder
          </button>
        </div>
      )}
    </Layout>
  )
}

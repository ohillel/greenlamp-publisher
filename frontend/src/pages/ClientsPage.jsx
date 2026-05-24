import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const PUB_LABEL = { presswhizz: 'PressWhizz', linksme: 'Links.me' }

const fmtPrice = v => (v != null ? `$${Number(v).toLocaleString()}` : null)

const fmtDate = dateStr => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

// ── Shared: all-clients folder grid (with optional pending highlights) ────────

function ClientFoldersGrid({ clients, pendingClientIds, deletingId, onNavigate, onDelete }) {
  return (
    <div className="clients-grid">
      {clients.map(client => {
        const isPending = pendingClientIds.has(client.id)
        return (
          <div
            key={client.id}
            className={`client-card${isPending ? ' client-card--pending' : ''}${deletingId === client.id ? ' deleting' : ''}`}
            onClick={() => onNavigate(client.id)}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && onNavigate(client.id)}
          >
            <div className="folder-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
              </svg>
            </div>
            <span className="client-name">{client.name}</span>
            <button
              className="client-delete-btn"
              onClick={e => onDelete(e, client.id, client.name)}
              disabled={deletingId === client.id}
              title="Delete client"
            >
              ×
            </button>
          </div>
        )
      })}
    </div>
  )
}

// ── Shared: table + folders split layout ──────────────────────────────────────

function TaskTableLayout({
  articles, pendingClientIds, clients, deletingId,
  onNavigate, onDelete, onRowClick, popup, setPopup,
  tableTitle, tableColumns, renderRow, renderPopup,
}) {
  return (
    <div className="or-clients-layout">
      {/* Left: task table */}
      <div className="or-pending-col">
        <div className="clients-col-title pending">
          {tableTitle}
          <span className="pending-count">{articles.length}</span>
        </div>
        <div className="pending-table-wrap">
          <table className="pending-articles-table">
            <thead>
              <tr>{tableColumns.map(col => <th key={col}>{col}</th>)}</tr>
            </thead>
            <tbody>
              {articles.map((article, i) => renderRow(article, i))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Right: all client folders */}
      <div className="or-uptodate-col">
        <div className="clients-col-title">All clients</div>
        <ClientFoldersGrid
          clients={clients}
          pendingClientIds={pendingClientIds}
          deletingId={deletingId}
          onNavigate={onNavigate}
          onDelete={onDelete}
        />
      </div>
    </div>
  )
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

  // Or — submitted articles table
  const [pendingArticles,   setPendingArticles]   = useState([])
  // Publisher — approved articles table
  const [publisherArticles, setPublisherArticles] = useState([])

  const [popup, setPopup] = useState(null)   // { article, x, y, role }

  const inputRef = useRef(null)
  const navigate = useNavigate()
  const { role } = useAuth()

  // ── Fetch clients ────────────────────────────────────────────────────────────

  useEffect(() => {
    supabase.from('clients').select('id, name').order('name')
      .then(({ data }) => { setClients(data ?? []); setLoading(false) })
  }, [])

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  // ── Or: fetch submitted articles ─────────────────────────────────────────────

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

  // ── Publisher: fetch approved articles ───────────────────────────────────────

  const refreshPublisherArticles = useCallback(async () => {
    if (role !== 'publisher') return
    const { data } = await supabase
      .from('articles')
      .select('id, client_id, status, created_at, magazine, google_doc_url, chosen_publisher, preferred_publisher, clients(name)')
      .eq('status', 'approved')
      .order('created_at', { ascending: true })
    const articles = data ?? []
    setPublisherArticles(articles)
    setPendingClientIds(new Set(articles.map(a => a.client_id)))
  }, [role])

  useEffect(() => { refreshPublisherArticles() }, [refreshPublisherArticles])

  // ── Realtime: keep tables accurate ───────────────────────────────────────────

  useEffect(() => {
    if (role !== 'or' && role !== 'publisher') return
    const channel = supabase
      .channel('articles-pending-indicators')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'articles' }, () => {
        if (role === 'or') refreshOrArticles()
        else refreshPublisherArticles()
      })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [role, refreshOrArticles, refreshPublisherArticles])

  // ── Close popup on outside click ─────────────────────────────────────────────

  useEffect(() => {
    if (!popup) return
    const close = () => setPopup(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [popup])

  // ── Row click handler (shared) ───────────────────────────────────────────────

  const handleRowClick = (e, article) => {
    e.stopPropagation()
    if (popup?.article.id === article.id) { setPopup(null); return }
    const rect = e.currentTarget.getBoundingClientRect()
    setPopup({ article, x: rect.left, y: rect.bottom })
  }

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

  // ── Render helpers ───────────────────────────────────────────────────────────

  const q = search.trim().toLowerCase()
  const visibleClients = q
    ? clients.filter(c => c.name.toLowerCase().includes(q))
    : clients

  const folderProps = {
    clients: visibleClients,
    pendingClientIds,
    deletingId,
    onNavigate: id => navigate(`/clients/${id}`),
    onDelete: deleteClient,
  }

  // Or table row
  const renderOrRow = (article, i) => {
    const effPub = article.chosen_publisher || article.preferred_publisher
    const pw = article.price_presswhizz
    const lm = article.price_linksme
    const priceStr = (pw == null && lm == null)
      ? null
      : [pw != null && `PressWhizz: ${fmtPrice(pw)}`, lm != null && `Links.me: ${fmtPrice(lm)}`]
          .filter(Boolean).join(' | ')
    return (
      <tr key={article.id} className="pending-article-row status-submitted" onClick={e => handleRowClick(e, article)}>
        <td className="col-num">{i + 1}</td>
        <td className="col-client">{article.clients?.name ?? '—'}</td>
        <td className="col-date">{fmtDate(article.created_at)}</td>
        <td className="col-magazine">{article.magazine ?? '—'}</td>
        <td className="col-doc">
          {article.google_doc_url
            ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="doc-link">Doc ↗</a>
            : <span className="cf-empty">—</span>}
        </td>
        <td className="col-publisher">
          <span className={effPub ? 'pub-tag' : 'cf-empty'}>{PUB_LABEL[effPub] ?? '—'}</span>
        </td>
        <td className="col-prices">
          {priceStr == null
            ? <span className="price-fetching-inline"><span className="price-spinner" /> Fetching…</span>
            : <span>{priceStr}</span>}
        </td>
      </tr>
    )
  }

  // Publisher table row
  const renderPublisherRow = (article, i) => {
    const effPub = article.chosen_publisher || article.preferred_publisher
    return (
      <tr key={article.id} className="pending-article-row status-approved" onClick={e => handleRowClick(e, article)}>
        <td className="col-num">{i + 1}</td>
        <td className="col-client">{article.clients?.name ?? '—'}</td>
        <td className="col-magazine">{article.magazine ?? '—'}</td>
        <td className="col-doc">
          {article.google_doc_url
            ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="doc-link">Doc ↗</a>
            : <span className="cf-empty">—</span>}
        </td>
        <td className="col-publisher">
          <span className={effPub ? 'pub-tag' : 'cf-empty'}>{PUB_LABEL[effPub] ?? '—'}</span>
        </td>
        <td className="col-date">{fmtDate(article.created_at)}</td>
      </tr>
    )
  }

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

      {loading && <div className="loading-inline">Loading…</div>}

      {/* ── Or layout ── */}
      {!loading && role === 'or' && pendingArticles.length === 0 && (
        <ClientFoldersGrid {...folderProps} />
      )}
      {!loading && role === 'or' && pendingArticles.length > 0 && (
        <TaskTableLayout
          articles={pendingArticles}
          tableTitle="Needs attention"
          tableColumns={['#', 'Client', 'Submitted', 'Magazine', 'Doc', 'Publisher', 'Prices']}
          renderRow={renderOrRow}
          {...folderProps}
        />
      )}

      {/* ── Publisher layout ── */}
      {!loading && role === 'publisher' && publisherArticles.length === 0 && (
        <ClientFoldersGrid {...folderProps} />
      )}
      {!loading && role === 'publisher' && publisherArticles.length > 0 && (
        <TaskTableLayout
          articles={publisherArticles}
          tableTitle="To send"
          tableColumns={['#', 'Client', 'Magazine', 'Doc', 'Publisher', 'Submitted']}
          renderRow={renderPublisherRow}
          {...folderProps}
        />
      )}

      {/* ── Denise and other roles: flat grid ── */}
      {!loading && role !== 'or' && role !== 'publisher' && (
        visibleClients.length === 0 ? (
          <div className="empty-state">
            {search ? <p>No clients match "{search}".</p> : <p>No clients yet. Add your first one above.</p>}
          </div>
        ) : (
          <ClientFoldersGrid {...folderProps} />
        )
      )}

      {/* ── Row action popup ── */}
      {popup && (
        <div
          className="row-action-popup"
          style={{ position: 'fixed', top: popup.y + 4, left: popup.x, zIndex: 1000 }}
          onClick={e => e.stopPropagation()}
        >
          {role === 'or' && (
            <button
              className="row-popup-btn row-popup-approve"
              onClick={() => {
                setPopup(null)
                navigate(`/clients/${popup.article.client_id}?approve=${popup.article.id}`)
              }}
            >
              Approve
            </button>
          )}
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

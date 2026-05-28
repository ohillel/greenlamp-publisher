import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const PUB_LABEL = { presswhizz: 'PressWhizz', linksme: 'Links.me', other: 'Other' }

const fmtPrice = v => (v != null ? `$${Number(v).toLocaleString()}` : null)

const fmtDate = dateStr => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

const normalizeMag = raw => {
  let s = (raw ?? '').trim().toLowerCase()
  s = s.replace(/^https?:\/\//, '')
  s = s.replace(/^www\./, '')
  s = s.replace(/\/$/, '')
  return s
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
  const [newDocUrl,        setNewDocUrl]        = useState('')
  const [saving,           setSaving]           = useState(false)
  const [deletingId,       setDeletingId]       = useState(null)
  const [pendingClientIds, setPendingClientIds] = useState(new Set())

  // Magazine search
  const [magSearch,      setMagSearch]      = useState('')
  const [magResults,     setMagResults]     = useState([])
  const [magSearching,   setMagSearching]   = useState(false)
  const [magMonthFilter, setMagMonthFilter] = useState('all')

  // Or — submitted articles + approved "other" assigned to Or
  const [pendingArticles,   setPendingArticles]   = useState([])
  // Publisher — approved articles table
  const [publisherArticles, setPublisherArticles] = useState([])
  // Denise — approved "other" articles assigned to her
  const [deniseArticles,    setDeniseArticles]    = useState([])

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

  // ── Or: fetch submitted articles + approved "other" assigned to Or ───────────

  const refreshOrArticles = useCallback(async () => {
    if (role !== 'or') return
    const cols = 'id, client_id, status, created_at, magazine, google_doc_url, chosen_publisher, preferred_publisher, assigned_to, price_presswhizz, price_linksme, clients(name)'
    const [submittedRes, approvedOrRes] = await Promise.all([
      supabase.from('articles').select(cols).eq('status', 'submitted').order('created_at', { ascending: true }),
      supabase.from('articles').select(cols).eq('status', 'approved').eq('assigned_to', 'or').order('created_at', { ascending: true }),
    ])
    const articles = [
      ...(submittedRes.data ?? []),
      ...(approvedOrRes.data ?? []),
    ].sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
    setPendingArticles(articles)
    setPendingClientIds(new Set(articles.map(a => a.client_id)))
  }, [role])

  useEffect(() => { refreshOrArticles() }, [refreshOrArticles])

  // ── Denise: fetch approved "other" articles assigned to her ─────────────────

  const refreshDeniseArticles = useCallback(async () => {
    if (role !== 'denise') return
    const { data } = await supabase
      .from('articles')
      .select('id, client_id, status, created_at, magazine, google_doc_url, clients(name)')
      .eq('status', 'approved')
      .eq('assigned_to', 'denise')
      .order('created_at', { ascending: true })
    const articles = data ?? []
    setDeniseArticles(articles)
    setPendingClientIds(new Set(articles.map(a => a.client_id)))
  }, [role])

  useEffect(() => { refreshDeniseArticles() }, [refreshDeniseArticles])

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
    if (role !== 'or' && role !== 'publisher' && role !== 'denise') return
    const channel = supabase
      .channel('articles-pending-indicators')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'articles' }, () => {
        if (role === 'or')        refreshOrArticles()
        else if (role === 'publisher') refreshPublisherArticles()
        else if (role === 'denise')    refreshDeniseArticles()
      })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [role, refreshOrArticles, refreshPublisherArticles, refreshDeniseArticles])

  // ── Magazine search ──────────────────────────────────────────────────────────

  useEffect(() => {
    const term = normalizeMag(magSearch)
    setMagMonthFilter('all')
    if (!term) { setMagResults([]); return }

    const timer = setTimeout(async () => {
      setMagSearching(true)
      // Fetch candidates with a broad ilike, then normalize client-side
      const { data } = await supabase
        .from('articles')
        .select('id, client_id, magazine, created_at, published_at, clients(name)')
        .ilike('magazine', `%${term}%`)
        .order('created_at', { ascending: false })

      const matched = (data ?? []).filter(a => normalizeMag(a.magazine) === term)
      setMagResults(matched)
      setMagSearching(false)
    }, 350)

    return () => clearTimeout(timer)
  }, [magSearch])

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
    const row = { name, ...(newDocUrl.trim() ? { google_doc_url: newDocUrl.trim() } : {}) }
    const { data, error } = await supabase
      .from('clients').insert(row).select().single()
    if (!error) {
      setClients(prev => [...prev, data].sort((a, b) => a.name.localeCompare(b.name)))
      setNewName('')
      setNewDocUrl('')
      setAdding(false)
    }
    setSaving(false)
  }

  const cancelAdd = () => { setAdding(false); setNewName(''); setNewDocUrl('') }

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
    const effPub  = article.chosen_publisher || article.preferred_publisher
    const isOther = effPub === 'other'
    const pw = article.price_presswhizz
    const lm = article.price_linksme
    const priceStr = isOther ? null
      : (pw == null && lm == null)
        ? null
        : [pw != null && `PressWhizz: ${fmtPrice(pw)}`, lm != null && `Links.me: ${fmtPrice(lm)}`]
            .filter(Boolean).join(' | ')
    const rowClass = article.status === 'approved' ? 'status-approved' : 'status-submitted'
    return (
      <tr key={article.id} className={`pending-article-row ${rowClass}`} onClick={e => handleRowClick(e, article)}>
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
          {isOther
            ? <span className="cf-empty">—</span>
            : priceStr == null
              ? <span className="price-fetching-inline"><span className="price-spinner" /> Fetching…</span>
              : <span>{priceStr}</span>}
        </td>
      </tr>
    )
  }

  // Denise table row (approved "other" articles assigned to her)
  const renderDeniseRow = (article, i) => (
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
        <span className="pub-tag">Other</span>
      </td>
      <td className="col-date">{fmtDate(article.created_at)}</td>
    </tr>
  )

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
          <div className="add-client-form">
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
            <input
              className="add-client-input"
              type="url"
              value={newDocUrl}
              onChange={e => setNewDocUrl(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') addClient(); if (e.key === 'Escape') cancelAdd() }}
              placeholder="Google Doc URL (optional)"
            />
            <div className="add-client-actions">
              <button className="btn-save" onClick={addClient} disabled={saving || !newName.trim()}>
                {saving ? 'Saving…' : 'Add'}
              </button>
              <button className="btn-ghost" onClick={cancelAdd}>Cancel</button>
            </div>
          </div>
        ) : (
          <>
            <button className="btn-add-client" onClick={() => setAdding(true)}>
              + New Client
            </button>
            <div className="clients-search-wrap">
              <input
                className="clients-search"
                type="search"
                placeholder="Search clients…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <input
                className="clients-search"
                type="search"
                placeholder="Search magazine…"
                value={magSearch}
                onChange={e => setMagSearch(e.target.value)}
                style={{ marginLeft: 8 }}
              />
            </div>
          </>
        )}
      </div>

      {loading && <div className="loading-inline">Loading…</div>}

      {/* ── Magazine search results ── */}
      {!loading && magSearch.trim() && (() => {
        // Derive sorted month list from results
        const magMonths = (() => {
          const seen = new Set()
          const months = []
          for (const a of magResults) {
            if (!a.created_at) continue
            const d   = new Date(a.created_at)
            const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
            if (!seen.has(key)) {
              seen.add(key)
              months.push({ key, label: d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }) })
            }
          }
          return months.sort((a, b) => b.key.localeCompare(a.key))
        })()

        const visibleMagResults = magMonthFilter === 'all'
          ? magResults
          : magResults.filter(a =>
              a.created_at && new Date(a.created_at).toISOString().slice(0, 7) === magMonthFilter
            )

        return (
          <div style={{ marginBottom: 24 }}>
            {magSearching ? (
              <div className="loading-inline">Searching…</div>
            ) : magResults.length === 0 ? (
              <div className="empty-state"><p>No articles found for this magazine.</p></div>
            ) : (
              <>
                {magMonths.length > 1 && (
                  <div style={{ marginBottom: 10 }}>
                    <select
                      value={magMonthFilter}
                      onChange={e => setMagMonthFilter(e.target.value)}
                      style={{ height: 32, padding: '0 10px', border: '1px solid #d1d5db', borderRadius: 6, background: '#ffffff', color: '#111827', fontSize: 13, cursor: 'pointer' }}
                    >
                      <option value="all">All months</option>
                      {magMonths.map(({ key, label }) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                    </select>
                  </div>
                )}
                <table className="pending-articles-table" style={{ width: '100%' }}>
                  <thead>
                    <tr>
                      <th>Client</th>
                      <th>Added date</th>
                      <th>Published date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleMagResults.length === 0 ? (
                      <tr><td colSpan={3} style={{ textAlign: 'center', color: '#6b7280', padding: 16 }}>No articles in this month.</td></tr>
                    ) : visibleMagResults.map(article => (
                      <tr
                        key={article.id}
                        className="pending-article-row"
                        style={{ cursor: 'pointer' }}
                        onClick={() => navigate(`/clients/${article.client_id}?article=${article.id}`)}
                      >
                        <td className="col-client">{article.clients?.name ?? '—'}</td>
                        <td className="col-date">{fmtDate(article.created_at)}</td>
                        <td className="col-date">{article.published_at ? fmtDate(article.published_at) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )
      })()}

      {/* ── Role-based layouts (hidden while magazine search is active) ── */}
      {!loading && !magSearch.trim() && <>

      {/* ── Or layout ── */}
      {role === 'or' && pendingArticles.length === 0 && (
        <ClientFoldersGrid {...folderProps} />
      )}
      {role === 'or' && pendingArticles.length > 0 && (
        <TaskTableLayout
          articles={pendingArticles}
          tableTitle="Needs attention"
          tableColumns={['#', 'Client', 'Submitted', 'Magazine', 'Doc', 'Publisher', 'Prices']}
          renderRow={renderOrRow}
          {...folderProps}
        />
      )}

      {/* ── Publisher layout ── */}
      {role === 'publisher' && publisherArticles.length === 0 && (
        <ClientFoldersGrid {...folderProps} />
      )}
      {role === 'publisher' && publisherArticles.length > 0 && (
        <TaskTableLayout
          articles={publisherArticles}
          tableTitle="To send"
          tableColumns={['#', 'Client', 'Magazine', 'Doc', 'Publisher', 'Submitted']}
          renderRow={renderPublisherRow}
          {...folderProps}
        />
      )}

      {/* ── Denise: assigned "other" articles table + folders ── */}
      {role === 'denise' && deniseArticles.length === 0 && (
        visibleClients.length === 0 ? (
          <div className="empty-state">
            {search ? <p>No clients match "{search}".</p> : <p>No clients yet. Add your first one above.</p>}
          </div>
        ) : (
          <ClientFoldersGrid {...folderProps} />
        )
      )}
      {role === 'denise' && deniseArticles.length > 0 && (
        <TaskTableLayout
          articles={deniseArticles}
          tableTitle="To send"
          tableColumns={['#', 'Client', 'Magazine', 'Doc', 'Publisher', 'Submitted']}
          renderRow={renderDeniseRow}
          {...folderProps}
        />
      )}

      {/* ── Other roles: flat grid ── */}
      {role !== 'or' && role !== 'publisher' && role !== 'denise' && (
        visibleClients.length === 0 ? (
          <div className="empty-state">
            {search ? <p>No clients match "{search}".</p> : <p>No clients yet. Add your first one above.</p>}
          </div>
        ) : (
          <ClientFoldersGrid {...folderProps} />
        )
      )}

      </> /* end role-based layouts */}

      {/* ── Row action popup ── */}
      {popup && (
        <div
          className="row-action-popup"
          style={{ position: 'fixed', top: popup.y + 4, left: popup.x, zIndex: 1000 }}
          onClick={e => e.stopPropagation()}
        >
          {role === 'or' && popup.article.status === 'submitted' && (
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

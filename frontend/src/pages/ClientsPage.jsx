import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import * as XLSX from 'xlsx'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'

const API_BASE = 'https://greenlamp-publisher-production-75fd.up.railway.app'

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

// ── SearchableSelect ─────────────────────────────────────────────────────────
// A dropdown with an inline search input. options: [{value, label}]
// allLabel is the label for the "show all" option (value='all').

function SearchableSelect({ value, onChange, options, allLabel = 'All' }) {
  const [open,   setOpen]   = useState(false)
  const [query,  setQuery]  = useState('')
  const ref      = useRef(null)
  const inputRef = useRef(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? options.filter(o => o.label.toLowerCase().includes(q)) : options
  }, [options, query])

  const current = value === 'all' ? allLabel : (options.find(o => o.value === value)?.label ?? allLabel)

  useEffect(() => {
    if (!open) { setQuery(''); return }
    setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const select = val => { onChange(val); setOpen(false) }

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        style={{
          height: 32, padding: '0 28px 0 10px', border: '1px solid #d1d5db',
          borderRadius: 6, background: '#ffffff', color: '#111827', fontSize: 13,
          cursor: 'pointer', whiteSpace: 'nowrap', position: 'relative',
        }}
      >
        {current}
        <span style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', fontSize: 10, opacity: 0.5 }}>▼</span>
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, zIndex: 200, marginTop: 4,
          background: '#fff', border: '1px solid #d1d5db', borderRadius: 8,
          boxShadow: '0 4px 16px rgba(0,0,0,0.12)', minWidth: 180, maxWidth: 260,
        }}>
          <div style={{ padding: '6px 8px', borderBottom: '1px solid #f0f0f0' }}>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search…"
              style={{ width: '100%', padding: '4px 6px', border: '1px solid #e5e7eb', borderRadius: 4, fontSize: 12, boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            {!query && (
              <div
                onClick={() => select('all')}
                style={{ padding: '7px 12px', fontSize: 13, cursor: 'pointer', fontStyle: 'italic', color: '#6b7280', background: value === 'all' ? '#f0fdf4' : 'transparent' }}
              >
                {allLabel}
              </div>
            )}
            {filtered.length === 0 ? (
              <div style={{ padding: '7px 12px', fontSize: 12, color: '#9ca3af' }}>No matches</div>
            ) : filtered.map(o => (
              <div
                key={o.value}
                onClick={() => select(o.value)}
                style={{
                  padding: '7px 12px', fontSize: 13, cursor: 'pointer',
                  background: o.value === value ? '#f0fdf4' : 'transparent',
                  fontWeight: o.value === value ? 600 : 400,
                }}
                onMouseEnter={e => { e.currentTarget.style.background = o.value === value ? '#f0fdf4' : '#f9fafb' }}
                onMouseLeave={e => { e.currentTarget.style.background = o.value === value ? '#f0fdf4' : 'transparent' }}
              >
                {o.label}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
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
  const [magInput,         setMagInput]         = useState('')        // what user is typing
  const [magSelected,      setMagSelected]      = useState('')        // confirmed selection
  const [magSuggestions,   setMagSuggestions]   = useState([])        // autocomplete list
  const [magResults,       setMagResults]       = useState([])
  const [magSearching,     setMagSearching]     = useState(false)
  const [magMonthFilter,   setMagMonthFilter]   = useState('all')
  const [magClientFilter,  setMagClientFilter]  = useState('all')
  const magInputRef = useRef(null)

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

  // ── Export to Excel ──────────────────────────────────────────────────────────

  const [exporting,       setExporting]       = useState(false)
  const [exportMonth,     setExportMonth]      = useState('all')
  const [exportCountry,   setExportCountry]    = useState('all')
  const [exportMonthOpts, setExportMonthOpts]  = useState([])  // [{value:'2026-05', label:'May 2026'}]

  // Fetch distinct published months for the dropdown
  useEffect(() => {
    if (role !== 'or') return
    supabase
      .from('articles')
      .select('published_at')
      .eq('status', 'published')
      .not('published_at', 'is', null)
      .then(({ data }) => {
        const seen = new Set()
        const opts = []
        for (const row of (data ?? []).sort((a, b) => b.published_at.localeCompare(a.published_at))) {
          const d   = new Date(row.published_at)
          const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
          if (!seen.has(key)) {
            seen.add(key)
            opts.push({ value: key, label: d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }) })
          }
        }
        setExportMonthOpts(opts)
      })
  }, [role])

  const exportToExcel = async () => {
    setExporting(true)
    let query = supabase
      .from('articles')
      .select('client_id, magazine, chosen_publisher, price_presswhizz, price_linksme, published_url, created_at, published_at, published_country, clients(name)')
      .eq('status', 'published')
      .not('published_at', 'is', null)
      .order('published_at', { ascending: false })

    if (exportMonth !== 'all') {
      const [year, month] = exportMonth.split('-').map(Number)
      const from = new Date(year, month - 1, 1).toISOString()
      const to   = new Date(year, month, 1).toISOString()
      query = query.gte('published_at', from).lt('published_at', to)
    }
    if (exportCountry !== 'all') {
      query = query.eq('published_country', exportCountry)
    }

    const { data } = await query

    const fmt = ts => {
      if (!ts) return ''
      const d = new Date(ts)
      return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}/${d.getFullYear()}`
    }

    const PUB = { presswhizz: 'PressWhizz', linksme: 'Links.me', other: 'Other' }

    const rows = (data ?? []).map(a => {
      const pub   = a.chosen_publisher
      const price = pub === 'presswhizz' ? a.price_presswhizz : pub === 'linksme' ? a.price_linksme : null
      return {
        'Client':     a.clients?.name ?? '',
        'Website':    a.magazine ?? '',
        'Platform':   PUB[pub] ?? pub ?? '',
        'Price':      price != null ? price : '',
        'Live URL':   a.published_url ?? '',
        'Added':      fmt(a.created_at),
        'Published':  fmt(a.published_at),
        'CY/IL':      a.published_country ?? '',
      }
    })

    const ws = XLSX.utils.json_to_sheet(rows)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Published Articles')
    const monthSuffix   = exportMonth   !== 'all' ? `-${exportMonth}`    : ''
    const countrySuffix = exportCountry !== 'all' ? `-${exportCountry}`  : ''
    XLSX.writeFile(wb, `published-articles${monthSuffix}${countrySuffix}.xlsx`)
    setExporting(false)
  }

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
      .select('id, client_id, status, created_at, magazine, google_doc_url, chosen_publisher, preferred_publisher, assigned_to, clients(name)')
      .eq('status', 'approved')
      .order('created_at', { ascending: true })
    // Include non-Other articles + Other articles explicitly assigned to the publisher role
    const articles = (data ?? []).filter(a =>
      (a.chosen_publisher || a.preferred_publisher) !== 'other' ||
      a.assigned_to === 'publisher'
    )
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

  // ── Magazine search: suggestions while typing ───────────────────────────────

  useEffect(() => {
    const term = normalizeMag(magInput)
    if (!term || magSelected) { setMagSuggestions([]); return }

    const timer = setTimeout(async () => {
      const { data } = await supabase
        .from('articles')
        .select('magazine')
        .ilike('magazine', `%${term}%`)
        .limit(200)

      // Deduplicate by normalized domain, keep display form
      const seen = new Set()
      const suggestions = []
      for (const row of data ?? []) {
        const norm = normalizeMag(row.magazine)
        if (norm && norm.includes(term) && !seen.has(norm)) {
          seen.add(norm)
          suggestions.push(norm)
        }
      }
      suggestions.sort()
      setMagSuggestions(suggestions)
    }, 200)

    return () => clearTimeout(timer)
  }, [magInput, magSelected])

  // ── Magazine search: fetch results after selection ───────────────────────────

  useEffect(() => {
    const term = normalizeMag(magSelected)
    setMagMonthFilter('all')
    setMagClientFilter('all')
    if (!term) { setMagResults([]); return }

    ;(async () => {
      setMagSearching(true)
      const { data } = await supabase
        .from('articles')
        .select('id, client_id, magazine, created_at, published_at, clients(name)')
        .ilike('magazine', `%${term}%`)
        .order('created_at', { ascending: false })

      const matched = (data ?? []).filter(a => normalizeMag(a.magazine).includes(term))
      setMagResults(matched)
      setMagSearching(false)
    })()
  }, [magSelected])

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
            {role === 'or' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8 }}>
                <select
                  value={exportMonth}
                  onChange={e => setExportMonth(e.target.value)}
                  style={{ height: 32, padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 13, background: '#fff', color: '#111827' }}
                >
                  <option value="all">All months</option>
                  {exportMonthOpts.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                <select
                  value={exportCountry}
                  onChange={e => setExportCountry(e.target.value)}
                  style={{ height: 32, padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 13, background: '#fff', color: '#111827' }}
                >
                  <option value="all">All</option>
                  <option value="IL">IL</option>
                  <option value="CY">CY</option>
                </select>
                <button
                  className="btn-ghost"
                  onClick={exportToExcel}
                  disabled={exporting}
                >
                  {exporting ? 'Exporting…' : '↓ Export to Excel'}
                </button>
              </div>
            )}
            <div className="clients-search-wrap">
              <input
                className="clients-search"
                type="search"
                placeholder="Search clients…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <div style={{ position: 'relative', marginLeft: 8 }}>
                <input
                  ref={magInputRef}
                  className="clients-search"
                  type="search"
                  placeholder="Search magazine…"
                  value={magInput}
                  onChange={e => {
                    setMagInput(e.target.value)
                    if (magSelected) setMagSelected('')  // clear selection when typing again
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Escape') { setMagInput(''); setMagSelected(''); setMagSuggestions([]) }
                    if (e.key === 'Enter' && magSuggestions.length === 1) {
                      setMagInput(magSuggestions[0]); setMagSelected(magSuggestions[0]); setMagSuggestions([])
                    }
                  }}
                  autoComplete="off"
                />
                {magSuggestions.length > 0 && (
                  <div style={{
                    position: 'absolute', top: '100%', left: 0, zIndex: 300, marginTop: 4,
                    background: '#fff', border: '1px solid #d1d5db', borderRadius: 8,
                    boxShadow: '0 4px 16px rgba(0,0,0,0.12)', minWidth: 240, maxWidth: 360,
                    maxHeight: 240, overflowY: 'auto',
                  }}>
                    {magSuggestions.map(s => (
                      <div
                        key={s}
                        onMouseDown={e => {
                          e.preventDefault()  // prevent input blur
                          setMagInput(s); setMagSelected(s); setMagSuggestions([])
                        }}
                        style={{ padding: '8px 12px', fontSize: 13, cursor: 'pointer', color: '#111827' }}
                        onMouseEnter={e => { e.currentTarget.style.background = '#f0fdf4' }}
                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                      >
                        {s}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {loading && <div className="loading-inline">Loading…</div>}

      {/* ── Magazine search results ── */}
      {!loading && magSelected && (() => {
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

        // Derive sorted client list from results
        const magClients = (() => {
          const seen = new Map()
          for (const a of magResults) {
            const id   = a.client_id
            const name = a.clients?.name ?? '—'
            if (!seen.has(id)) seen.set(id, name)
          }
          return [...seen.entries()]
            .map(([id, name]) => ({ id, name }))
            .sort((a, b) => a.name.localeCompare(b.name))
        })()

        const visibleMagResults = magResults.filter(a => {
          const monthOk = magMonthFilter === 'all' ||
            (a.created_at && new Date(a.created_at).toISOString().slice(0, 7) === magMonthFilter)
          const clientOk = magClientFilter === 'all' || a.client_id === magClientFilter
          return monthOk && clientOk
        })

        return (
          <div style={{ marginBottom: 24 }}>
            {magSearching ? (
              <div className="loading-inline">Searching…</div>
            ) : magResults.length === 0 ? (
              <div className="empty-state"><p>No articles found for this magazine.</p></div>
            ) : (
              <>
                {(magMonths.length > 0 || magClients.length > 0) && (
                  <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                    <SearchableSelect
                      value={magMonthFilter}
                      onChange={setMagMonthFilter}
                      options={magMonths.map(({ key, label }) => ({ value: key, label }))}
                      allLabel="All months"
                    />
                    <SearchableSelect
                      value={magClientFilter}
                      onChange={setMagClientFilter}
                      options={magClients.map(({ id, name }) => ({ value: id, label: name }))}
                      allLabel="All clients"
                    />
                  </div>
                )}
                <table className="pending-articles-table" style={{ width: '100%' }}>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Client</th>
                      <th>Added date</th>
                      <th>Published date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleMagResults.length === 0 ? (
                      <tr><td colSpan={4} style={{ textAlign: 'center', color: '#6b7280', padding: 16 }}>No articles match the selected filters.</td></tr>
                    ) : visibleMagResults.map((article, i) => (
                      <tr
                        key={article.id}
                        className="pending-article-row"
                        style={{ cursor: 'pointer' }}
                        onClick={() => navigate(`/clients/${article.client_id}?article=${article.id}`)}
                      >
                        <td className="col-num">{i + 1}</td>
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
      {!loading && !magSelected && <>

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

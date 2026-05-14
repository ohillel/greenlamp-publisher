import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'

// ── Helpers ────────────────────────────────────────────────────────────────────

const fmtPrice = v => (v != null ? `$${Number(v).toLocaleString()}` : null)

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const sendNotify = (event, clientName, magazine) => {
  const payload = { event, client_name: clientName, magazine }
  console.log(`[notify] calling /api/notify`, payload, '→', `${API_BASE}/api/notify`)
  fetch(`${API_BASE}/api/notify`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  })
    .then(res => res.json().then(body => {
      console.log(`[notify] response ${res.status}:`, body)
    }))
    .catch(err => console.warn('[notify] fetch failed:', err))
}

const PUBLISHERS = [
  { value: 'presswhizz', label: 'PressWhizz' },
  { value: 'linksme',    label: 'Links.me'   },
]
const PUB_LABEL = { presswhizz: 'PressWhizz', linksme: 'Links.me' }

const EMPTY_FORM = { google_doc_url: '', magazine: '', preferred_publisher: '' }
const EMPTY_EDIT = {
  google_doc_url: '', magazine: '', preferred_publisher: '',
  chosen_publisher: '', price_presswhizz: '', price_linksme: '',
}

// ── Price comparison display ───────────────────────────────────────────────────

function PriceComparison({ pw, lm, pwError, lmError }) {
  const hasPw = pw != null
  const hasLm = lm != null
  let winner = null
  if (hasPw && hasLm) winner = pw <= lm ? 'presswhizz' : 'linksme'

  return (
    <div className="price-comparison">
      <div className={`price-comp-row${winner === 'presswhizz' ? ' cheaper' : ''}`}>
        <span className="comp-label">PressWhizz</span>
        <span className="comp-val">
          {hasPw ? fmtPrice(pw) : <span className="comp-na">{pwError ? 'Error' : 'Not found'}</span>}
        </span>
        {winner === 'presswhizz' && <span className="comp-badge">Cheaper</span>}
      </div>
      <div className={`price-comp-row${winner === 'linksme' ? ' cheaper' : ''}`}>
        <span className="comp-label">Links.me</span>
        <span className="comp-val">
          {hasLm
            ? fmtPrice(lm)
            : <span className="comp-na">
                {lmError
                  ? (lmError.includes('no project') || lmError.includes("could not find project")
                      ? 'No project found for this client'
                      : 'Error')
                  : 'Not found in catalog'}
              </span>
          }
        </span>
        {winner === 'linksme' && <span className="comp-badge">Cheaper</span>}
      </div>
    </div>
  )
}

// ── Denise article card ────────────────────────────────────────────────────────

function DeniseArticleCard({
  article, onDelete, onConfirm, confirming, deleting,
  isFetchingPrices, pricesDone, priceErrors,
}) {
  const isDraft    = article.status === 'draft'
  const hasPrices  = article.price_presswhizz != null || article.price_linksme != null
  // Confirm button shows once price fetch is done (success or failure)
  const canConfirm = isDraft && pricesDone && !isFetchingPrices

  return (
    <div className={`article-card ${isDraft ? 'draft' : ''}`}>
      <div className="card-header">
        <span className="card-magazine">{article.magazine ?? '—'}</span>
        <StatusBadge status={article.status} />
      </div>
      <div className="card-body">
        <div className="card-field">
          <span className="cf-label">Google Doc</span>
          {article.google_doc_url
            ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" className="doc-link">Open →</a>
            : <span className="cf-empty">—</span>}
        </div>
        <div className="card-field">
          <span className="cf-label">Preferred publisher</span>
          <span className={article.preferred_publisher ? 'pub-tag' : 'cf-empty'}>
            {PUB_LABEL[article.preferred_publisher] ?? article.preferred_publisher ?? '—'}
          </span>
        </div>

        {/* Price section */}
        {isDraft && !isFetchingPrices && pricesDone && console.log(
          '[prices] 4. PriceComparison props — pw:', article.price_presswhizz,
          '| lm:', article.price_linksme,
          '| errors:', JSON.stringify(priceErrors ?? null)
        )}
        {isDraft && (
          isFetchingPrices ? (
            <div className="price-fetching">
              <span className="price-spinner" />
              Checking prices on PressWhizz &amp; Links.me…
            </div>
          ) : (hasPrices || pricesDone) ? (
            <PriceComparison
              pw={article.price_presswhizz}
              lm={article.price_linksme}
              pwError={priceErrors?.presswhizz}
              lmError={priceErrors?.linksme}
            />
          ) : null
        )}
      </div>

      <div className="card-actions">
        <button
          className="btn-delete-article"
          onClick={() => onDelete(article.id)}
          disabled={deleting || confirming || isFetchingPrices}
        >
          {deleting ? 'Deleting…' : 'Delete'}
        </button>
        {canConfirm && (
          <button
            className="btn-confirm"
            onClick={() => onConfirm(article.id, article.magazine)}
            disabled={confirming || deleting}
          >
            {confirming ? 'Notifying…' : 'Confirm & Notify Or'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ClientPage() {
  const { clientId } = useParams()
  const { role }     = useAuth()
  const navigate     = useNavigate()

  const [client,   setClient]   = useState(null)
  const [articles, setArticles] = useState([])
  const [loading,  setLoading]  = useState(true)

  // Status filter (shared across all roles)
  const [statusFilter, setStatusFilter] = useState('all')

  // Denise — submit form
  const [showForm,    setShowForm]    = useState(false)
  const [form,        setForm]        = useState(EMPTY_FORM)
  const [submitting,  setSubmitting]  = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [successMsg,  setSuccessMsg]  = useState('')

  // Denise — per-card actions
  const [confirmingId, setConfirmingId] = useState(null)
  const [deletingId,   setDeletingId]   = useState(null)

  // Denise — price fetching: which article is currently being fetched,
  // which have completed, and any errors returned per article
  const [fetchingPricesId, setFetchingPricesId] = useState(null)
  const [priceDoneIds,     setPriceDoneIds]     = useState(new Set())
  const [priceErrorsMap,   setPriceErrorsMap]   = useState({})

  // Or — inline edit
  const [editingId, setEditingId] = useState(null)
  const [editData,  setEditData]  = useState(EMPTY_EDIT)
  const [saving,    setSaving]    = useState(false)
  const [saveError, setSaveError] = useState('')
  const [approving, setApproving] = useState(null)

  // Publisher
  const [markingId, setMarkingId] = useState(null)

  // ── Initial data fetch ───────────────────────────────────────────────────────

  useEffect(() => {
    if (!role) return
    const fetchAll = async () => {
      const [{ data: clientData }, { data: articlesData }] = await Promise.all([
        supabase.from('clients').select('id, name').eq('id', clientId).single(),
        // All roles see ALL articles — actions are gated per status, not filtered.
        supabase.from('articles').select('*').eq('client_id', clientId)
          .order('created_at', { ascending: false }),
      ])
      setClient(clientData)
      setArticles(articlesData ?? [])
      setLoading(false)
    }
    fetchAll()
  }, [clientId, role])

  // ── Realtime subscription ────────────────────────────────────────────────────

  useEffect(() => {
    if (!clientId) return

    const channel = supabase
      .channel(`articles-${clientId}`)
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'articles',
          filter: `client_id=eq.${clientId}`,
        },
        payload => {
          if (payload.eventType === 'INSERT') {
            setArticles(prev =>
              prev.some(a => a.id === payload.new.id)
                ? prev  // already added optimistically
                : [payload.new, ...prev]
            )
          } else if (payload.eventType === 'UPDATE') {
            console.log('[realtime] UPDATE received — price_presswhizz:', payload.new.price_presswhizz,
              '| price_linksme:', payload.new.price_linksme)
            setArticles(prev =>
              prev.map(a => {
                if (a.id !== payload.new.id) return a
                // Don't let a stale realtime event overwrite prices we just set locally
                // while a scrape is in progress for this article.
                if (
                  (a.price_presswhizz != null || a.price_linksme != null) &&
                  payload.new.price_presswhizz == null &&
                  payload.new.price_linksme == null
                ) return a
                return payload.new
              })
            )
          } else if (payload.eventType === 'DELETE') {
            setArticles(prev => prev.filter(a => a.id !== payload.old.id))
          }
        }
      )
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [clientId])

  // ── Denise: fetch prices from backend scraper ───────────────────────────────

  const fetchPricesForArticle = async (articleId, magazine, clientName) => {
    // Clear stale prices in local state only — do NOT write nulls to Supabase here,
    // because that would fire a realtime event that can arrive *after* we save the
    // fresh prices and wipe them back to null (race condition).
    setArticles(prev => prev.map(a =>
      a.id === articleId
        ? { ...a, price_presswhizz: null, price_linksme: null }
        : a
    ))

    setFetchingPricesId(articleId)
    try {
      console.log('[prices] 0. calling API with:', { magazine, client_name: clientName })
      const res = await fetch(`${API_BASE}/api/prices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ magazine, client_name: clientName }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()

      // 1. Raw API response
      console.log('[prices] 1. raw API response:', JSON.stringify(data))
      console.log('[prices]    data.presswhizz =', data.presswhizz, '| typeof:', typeof data.presswhizz)
      console.log('[prices]    data.linksme    =', data.linksme,    '| typeof:', typeof data.linksme)

      // 2. What gets saved to Supabase
      const supabasePayload = {
        price_presswhizz: data.presswhizz ?? null,
        price_linksme:    data.linksme    ?? null,
      }
      console.log('[prices] 2. saving to Supabase:', JSON.stringify(supabasePayload))
      const { error: sbError } = await supabase.from('articles').update(supabasePayload).eq('id', articleId)
      console.log('[prices]    Supabase update error:', sbError ?? 'none')

      // 3. Article object after save
      setArticles(prev => {
        const updated = prev.map(a =>
          a.id === articleId
            ? { ...a, price_presswhizz: data.presswhizz ?? null, price_linksme: data.linksme ?? null }
            : a
        )
        const article = updated.find(a => a.id === articleId)
        console.log('[prices] 3. article after setArticles:',
          article
            ? `price_presswhizz=${article.price_presswhizz} price_linksme=${article.price_linksme}`
            : '(not found in list)')
        return updated
      })

      if (data.errors) {
        console.log('[prices]    data.errors:', JSON.stringify(data.errors))
        setPriceErrorsMap(prev => ({ ...prev, [articleId]: data.errors }))
      }
    } catch (err) {
      console.error('[prices] ERROR:', err)
      setPriceErrorsMap(prev => ({ ...prev, [articleId]: { general: err.message } }))
    } finally {
      setFetchingPricesId(null)
      setPriceDoneIds(prev => new Set([...prev, articleId]))
    }
  }

  // ── Denise: save draft ───────────────────────────────────────────────────────

  const handleFormChange = e => setForm(f => ({ ...f, [e.target.name]: e.target.value }))

  const handleSubmit = async e => {
    e.preventDefault()
    const { google_doc_url, magazine, preferred_publisher } = form
    if (!google_doc_url || !magazine || !preferred_publisher) {
      setSubmitError('All fields are required.')
      return
    }
    // Capture client name synchronously before any awaits — client state is
    // guaranteed loaded at this point (submit button only renders post-load).
    const clientName = client?.name ?? ''
    setSubmitError('')
    setSubmitting(true)
    try {
      const { data: article, error } = await supabase
        .from('articles')
        .insert({ client_id: clientId, google_doc_url, magazine, preferred_publisher, status: 'draft' })
        .select()
        .single()
      if (error) throw error

      // Optimistic add — realtime will also fire but deduplicates
      setArticles(prev => [article, ...prev])
      setForm(EMPTY_FORM)
      setShowForm(false)
      setSuccessMsg(`"${magazine}" saved. Fetching prices…`)
      setTimeout(() => setSuccessMsg(''), 8000)

      // Kick off price scraping in the background — don't await
      fetchPricesForArticle(article.id, magazine, clientName)
    } catch (err) {
      setSubmitError(err.message ?? 'Submission failed.')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Denise: delete draft ─────────────────────────────────────────────────────

  const deleteArticle = async id => {
    if (!window.confirm('Delete this article? This cannot be undone.')) return
    setDeletingId(id)
    const { error } = await supabase.from('articles').delete().eq('id', id)
    if (!error) setArticles(prev => prev.filter(a => a.id !== id))
    setDeletingId(null)
  }

  // ── Denise: confirm & notify Or ──────────────────────────────────────────────

  const confirmAndNotifyOr = async (id, magazine) => {
    setConfirmingId(id)
    try {
      const { error } = await supabase
        .from('articles')
        .update({ status: 'submitted' })
        .eq('id', id)
      if (error) throw error

      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'submitted' } : a))
      sendNotify('submitted', client?.name ?? '', magazine)
      setSuccessMsg(`"${magazine}" sent to Or for review.`)
      setTimeout(() => setSuccessMsg(''), 6000)
    } catch (err) {
      console.error('confirmAndNotifyOr:', err)
    } finally {
      setConfirmingId(null)
    }
  }

  // ── Or: edit ─────────────────────────────────────────────────────────────────

  const startEdit = article => {
    setEditingId(article.id)
    setSaveError('')
    setEditData({
      google_doc_url:      article.google_doc_url      ?? '',
      magazine:            article.magazine            ?? '',
      preferred_publisher: article.preferred_publisher ?? '',
      chosen_publisher:    article.chosen_publisher    ?? '',
      price_presswhizz:    article.price_presswhizz    ?? '',
      price_linksme:       article.price_linksme       ?? '',
    })
  }

  const cancelEdit = () => { setEditingId(null); setSaveError('') }

  const handleEditChange = e => setEditData(d => ({ ...d, [e.target.name]: e.target.value }))

  const saveEdit = async id => {
    setSaving(true); setSaveError('')
    const { error } = await supabase.from('articles').update({
      google_doc_url:      editData.google_doc_url      || null,
      magazine:            editData.magazine            || null,
      preferred_publisher: editData.preferred_publisher || null,
      chosen_publisher:    editData.chosen_publisher    || null,
      price_presswhizz:    editData.price_presswhizz !== '' ? parseFloat(editData.price_presswhizz) : null,
      price_linksme:       editData.price_linksme     !== '' ? parseFloat(editData.price_linksme)    : null,
    }).eq('id', id)

    if (error) { setSaveError(error.message) }
    else {
      setArticles(prev => prev.map(a => a.id === id
        ? { ...a, ...editData,
            price_presswhizz: editData.price_presswhizz !== '' ? parseFloat(editData.price_presswhizz) : null,
            price_linksme:    editData.price_linksme    !== '' ? parseFloat(editData.price_linksme)    : null }
        : a))
      setEditingId(null)
    }
    setSaving(false)
  }

  // ── Or: approve ──────────────────────────────────────────────────────────────

  const approve = async id => {
    setApproving(id)
    const { error } = await supabase.from('articles').update({ status: 'approved' }).eq('id', id)
    if (!error) {
      const article = articles.find(a => a.id === id)
      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'approved' } : a))
      sendNotify('approved', client?.name ?? '', article?.magazine ?? '')
    }
    setApproving(null)
  }

  // ── Publisher: mark sent ─────────────────────────────────────────────────────

  const markSent = async id => {
    setMarkingId(id)
    const { error } = await supabase.from('articles').update({ status: 'sent_to_publisher' }).eq('id', id)
    if (!error) {
      const article = articles.find(a => a.id === id)
      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'sent_to_publisher' } : a))
      sendNotify('sent', client?.name ?? '', article?.magazine ?? '')
    }
    setMarkingId(null)
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <Layout><div className="loading-inline">Loading…</div></Layout>

  const STATUS_FILTERS = [
    { key: 'all',               label: 'All' },
    { key: 'draft',             label: 'Draft' },
    { key: 'submitted',         label: 'Submitted' },
    { key: 'approved',          label: 'Approved' },
    { key: 'sent_to_publisher', label: 'Sent to Publisher' },
    { key: 'published',         label: 'Published' },
    { key: 'not_published',     label: 'Not Published' },
  ]

  const visibleArticles = statusFilter === 'all'
    ? articles
    : articles.filter(a => a.status === statusFilter)

  return (
    <Layout>
      <div className="breadcrumb">
        <button className="breadcrumb-link" onClick={() => navigate('/clients')}>Clients</button>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">{client?.name ?? '…'}</span>
      </div>

      <div className="client-page-header">
        <h1 className="page-title" style={{ margin: 0 }}>
          {client?.name}
          {articles.length > 0 && (
            <span className="article-count">
              {statusFilter === 'all'
                ? `${articles.length} total`
                : `${visibleArticles.length} of ${articles.length}`}
            </span>
          )}
        </h1>

        {role === 'denise' && (
          <button
            className={`btn-submit-article ${showForm ? 'active' : ''}`}
            onClick={() => { setShowForm(v => !v); setSubmitError('') }}
          >
            {showForm ? '✕ Cancel' : '+ Submit New Article'}
          </button>
        )}
      </div>

      {/* ── Status filter bar ── */}
      <div className="status-filter-bar">
        {STATUS_FILTERS.map(({ key, label }) => {
          const count = key === 'all' ? articles.length : articles.filter(a => a.status === key).length
          return (
            <button
              key={key}
              className={`status-filter-btn${statusFilter === key ? ' active' : ''}${count === 0 && key !== 'all' ? ' empty' : ''}`}
              onClick={() => setStatusFilter(key)}
              data-status={key}
            >
              {label}
              {count > 0 && <span className="filter-count">{count}</span>}
            </button>
          )
        })}
      </div>

      {successMsg && <div className="success-banner">✓ {successMsg}</div>}

      {/* ── Inline submit form (Denise) ── */}
      {role === 'denise' && showForm && (
        <div className="submit-panel">
          <h3 className="submit-panel-title">New Article</h3>
          <form onSubmit={handleSubmit} noValidate>
            <div className="submit-panel-fields">
              <div className="field">
                <label>Google Doc URL</label>
                <input
                  type="url" name="google_doc_url" value={form.google_doc_url}
                  onChange={handleFormChange}
                  placeholder="https://docs.google.com/document/d/…" required
                />
              </div>
              <div className="field">
                <label>Magazine domain</label>
                <input
                  type="text" name="magazine" value={form.magazine}
                  onChange={handleFormChange}
                  placeholder="e.g. walla.co.il, ynet.co.il" required
                />
              </div>
              <div className="field">
                <label>Preferred Publisher</label>
                <select name="preferred_publisher" value={form.preferred_publisher} onChange={handleFormChange} required>
                  <option value="">— Select —</option>
                  <option value="presswhizz">PressWhizz</option>
                  <option value="linksme">Links.me</option>
                </select>
              </div>
            </div>
            {submitError && <p className="form-error">{submitError}</p>}
            <button type="submit" className="btn-primary submit-btn" disabled={submitting}>
              {submitting ? 'Saving…' : 'Save Draft'}
            </button>
          </form>
        </div>
      )}

      {/* ── Articles ── */}
      {visibleArticles.length === 0 ? (
        <div className="empty-state">
          {statusFilter !== 'all'
            ? <p>No {STATUS_FILTERS.find(f => f.key === statusFilter)?.label.toLowerCase()} articles for this client.</p>
            : <p>No articles yet for this client.</p>
          }
        </div>
      ) : (
        <div className="article-grid">

          {/* ── DENISE ── */}
          {role === 'denise' && visibleArticles.map(article => (
            <DeniseArticleCard
              key={article.id}
              article={article}
              onDelete={deleteArticle}
              onConfirm={confirmAndNotifyOr}
              confirming={confirmingId === article.id}
              deleting={deletingId === article.id}
              isFetchingPrices={fetchingPricesId === article.id}
              pricesDone={priceDoneIds.has(article.id)}
              priceErrors={priceErrorsMap[article.id]}
            />
          ))}

          {/* ── OR ── */}
          {role === 'or' && visibleArticles.map(article => {
            const isEditing   = editingId === article.id
            const canActOnIt  = article.status === 'submitted'

            return (
              <div key={article.id} className={`article-card ${isEditing ? 'editing' : ''}`}>
                <div className="card-header">
                  <span className="card-magazine">
                    {isEditing
                      ? <input name="magazine" value={editData.magazine} onChange={handleEditChange} className="edit-input" placeholder="Magazine" />
                      : (article.magazine ?? '—')
                    }
                  </span>
                  <StatusBadge status={article.status} />
                </div>

                <div className="card-body">
                  <div className="card-field">
                    <span className="cf-label">Google Doc</span>
                    {isEditing
                      ? <input name="google_doc_url" value={editData.google_doc_url} onChange={handleEditChange} className="edit-input" placeholder="https://docs.google.com/…" />
                      : article.google_doc_url
                        ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" className="doc-link">Open →</a>
                        : <span className="cf-empty">—</span>
                    }
                  </div>
                  <div className="card-field-row">
                    <div className="card-field">
                      <span className="cf-label">Preferred publisher</span>
                      {isEditing
                        ? <select name="preferred_publisher" value={editData.preferred_publisher} onChange={handleEditChange} className="edit-select">
                            <option value="">—</option>
                            {PUBLISHERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                          </select>
                        : <span className={article.preferred_publisher ? 'pub-tag' : 'cf-empty'}>
                            {PUB_LABEL[article.preferred_publisher] ?? article.preferred_publisher ?? '—'}
                          </span>
                      }
                    </div>
                    <div className="card-field">
                      <span className="cf-label">Chosen publisher</span>
                      {isEditing
                        ? <select name="chosen_publisher" value={editData.chosen_publisher} onChange={handleEditChange} className="edit-select">
                            <option value="">—</option>
                            {PUBLISHERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                          </select>
                        : <span className={article.chosen_publisher ? 'pub-tag chosen' : 'cf-empty'}>
                            {PUB_LABEL[article.chosen_publisher] ?? article.chosen_publisher ?? '—'}
                          </span>
                      }
                    </div>
                  </div>
                  <div className="card-field-row prices">
                    <div className="card-field">
                      <span className="cf-label">PressWhizz price</span>
                      {isEditing
                        ? <input name="price_presswhizz" type="number" value={editData.price_presswhizz} onChange={handleEditChange} className="edit-input" placeholder="₪" />
                        : <span className="price-val">{fmtPrice(article.price_presswhizz)}</span>
                      }
                    </div>
                    <div className="card-field">
                      <span className="cf-label">Links.me price</span>
                      {isEditing
                        ? <input name="price_linksme" type="number" value={editData.price_linksme} onChange={handleEditChange} className="edit-input" placeholder="₪" />
                        : <span className="price-val">{fmtPrice(article.price_linksme)}</span>
                      }
                    </div>
                  </div>
                  {saveError && isEditing && <p className="form-error" style={{ marginTop: 8 }}>{saveError}</p>}
                </div>

                <div className="card-actions">
                  {isEditing ? (
                    <>
                      <button className="btn-save" onClick={() => saveEdit(article.id)} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                      <button className="btn-ghost" onClick={cancelEdit} disabled={saving}>Cancel</button>
                    </>
                  ) : (
                    <>
                      {canActOnIt && (
                        <>
                          <button className="btn-edit" onClick={() => startEdit(article)}>Edit</button>
                          <button className="btn-approve" onClick={() => approve(article.id)} disabled={approving === article.id}>
                            {approving === article.id ? 'Approving…' : 'Approve'}
                          </button>
                        </>
                      )}
                      <button
                        className="btn-delete-article"
                        onClick={() => deleteArticle(article.id)}
                        disabled={deletingId === article.id || saving}
                      >
                        {deletingId === article.id ? 'Deleting…' : 'Delete'}
                      </button>
                    </>
                  )}
                </div>
              </div>
            )
          })}

          {/* ── PUBLISHER ── */}
          {role === 'publisher' && visibleArticles.map(article => {
            const chosenPrice = article.chosen_publisher === 'presswhizz'
              ? article.price_presswhizz
              : article.chosen_publisher === 'linksme'
                ? article.price_linksme
                : null

            return (
              <div key={article.id} className="article-card">
                <div className="card-header">
                  <span className="card-magazine">{article.magazine ?? '—'}</span>
                  <StatusBadge status={article.status} />
                </div>
                <div className="card-body">
                  <div className="card-field">
                    <span className="cf-label">Google Doc</span>
                    {article.google_doc_url
                      ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" className="doc-link">Open →</a>
                      : <span className="cf-empty">—</span>}
                  </div>
                  <div className="card-field-row">
                    <div className="card-field">
                      <span className="cf-label">Publisher</span>
                      <span className={article.chosen_publisher ? 'pub-tag chosen' : 'cf-empty'}>
                        {PUB_LABEL[article.chosen_publisher] ?? '—'}
                      </span>
                    </div>
                    {chosenPrice != null && (
                      <div className="card-field">
                        <span className="cf-label">Price</span>
                        <span className="price-val highlight">{fmtPrice(chosenPrice)}</span>
                      </div>
                    )}
                  </div>
                  {(article.price_presswhizz != null || article.price_linksme != null) && (
                    <div className="card-field-row prices">
                      <div className="card-field">
                        <span className="cf-label">PressWhizz</span>
                        <span className="price-val">{fmtPrice(article.price_presswhizz)}</span>
                      </div>
                      <div className="card-field">
                        <span className="cf-label">Links.me</span>
                        <span className="price-val">{fmtPrice(article.price_linksme)}</span>
                      </div>
                    </div>
                  )}
                </div>

                <div className="card-actions">
                  {article.status === 'approved' && (
                    <button className="btn-send" onClick={() => markSent(article.id)} disabled={markingId === article.id}>
                      {markingId === article.id ? 'Updating…' : 'Mark as sent to publisher'}
                    </button>
                  )}
                  <button
                    className="btn-delete-article"
                    onClick={() => deleteArticle(article.id)}
                    disabled={deletingId === article.id || markingId === article.id}
                  >
                    {deletingId === article.id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </div>
            )
          })}

        </div>
      )}
    </Layout>
  )
}

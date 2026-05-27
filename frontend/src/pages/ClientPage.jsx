import { useEffect, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import Layout from '../components/Layout'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'

// ── Helpers ────────────────────────────────────────────────────────────────────

const fmtPrice = v => (v != null ? `$${Number(v).toLocaleString()}` : null)
const fmtDate  = ts => ts ? new Date(ts).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : null

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const sendNotify = (event, clientName, magazine, reason, articleId, clientId) => {
  const payload = {
    event, client_name: clientName, magazine,
    ...(reason    ? { reason }                : {}),
    ...(articleId ? { article_id: articleId } : {}),
    ...(clientId  ? { client_id:  clientId  } : {}),
  }
  fetch(`${API_BASE}/api/notify`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  }).catch(err => console.warn('[notify] fetch failed:', err))
}

const PUBLISHERS = [
  { value: 'presswhizz', label: 'PressWhizz' },
  { value: 'linksme',    label: 'Links.me'   },
  { value: 'other',      label: 'Other'      },
]
const PUB_LABEL = { presswhizz: 'PressWhizz', linksme: 'Links.me', other: 'Other' }

const EMPTY_FORM = { google_doc_url: '', magazine: '', preferred_publisher: '', custom_publisher_note: '' }
const EMPTY_EDIT = {
  google_doc_url: '', magazine: '', preferred_publisher: '',
  chosen_publisher: '', price_presswhizz: '', price_linksme: '',
  publisher_notes: '',
}
const EMPTY_DENISE_EDIT = { google_doc_url: '', magazine: '', preferred_publisher: '', custom_publisher_note: '' }

// ── Denise article card ────────────────────────────────────────────────────────

function DeniseArticleCard({
  article, id, onDelete, onConfirm, confirming, deleting,
  isEditing, editData, onEditChange, onSaveEdit, onStartEdit, onCancelEdit, savingEdit,
  onMarkSent, markingSent,
}) {
  const isDraft       = article.status === 'draft'
  const isOtherAssigned = article.preferred_publisher === 'other' && article.assigned_to === 'denise'
    && article.status === 'approved'
  // Confirm is available once the required fields are filled — no price check needed
  const canConfirm = isDraft && !!article.magazine && !!article.preferred_publisher && !isEditing

  return (
    <div id={id} className={`article-card ${isDraft ? 'draft' : ''}${isEditing ? ' editing' : ''}`}>
      <div className="card-header">
        <span className="card-magazine">
          {isEditing
            ? <input className="edit-input" name="magazine" value={editData.magazine} onChange={onEditChange} placeholder="Magazine domain" />
            : (article.magazine ?? '—')
          }
        </span>
        {!isEditing && article.created_at && (
          <span className="card-date">{fmtDate(article.created_at)}</span>
        )}
        <StatusBadge status={article.status} />
      </div>
      <div className="card-body">
        {isEditing ? (
          <>
            <div className="denise-edit-field">
              <label>Google Doc URL</label>
              <input type="url" name="google_doc_url" value={editData.google_doc_url} onChange={onEditChange} placeholder="https://docs.google.com/…" />
            </div>
            <div className="denise-edit-field">
              <label>Preferred Publisher</label>
              <select name="preferred_publisher" value={editData.preferred_publisher} onChange={onEditChange}>
                <option value="">— Select —</option>
                <option value="presswhizz">PressWhizz</option>
                <option value="linksme">Links.me</option>
                <option value="other">Other</option>
              </select>
            </div>
            {editData.preferred_publisher === 'other' && (
              <div className="denise-edit-field">
                <label>Publisher destination</label>
                <input
                  type="text"
                  name="custom_publisher_note"
                  value={editData.custom_publisher_note}
                  onChange={onEditChange}
                  placeholder="e.g. Outreach.io, direct email to editor…"
                />
              </div>
            )}
          </>
        ) : (
          <>
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
            {article.custom_publisher_note && (
              <div className="card-field">
                <span className="cf-label">Publisher destination</span>
                <span style={{ fontSize: 13, color: 'var(--text)', fontStyle: 'italic' }}>{article.custom_publisher_note}</span>
              </div>
            )}
            {article.publisher_notes && !isDraft && (
              <div className="card-field">
                <div className="publisher-notes-label">Notes from Or</div>
                <div className="publisher-notes">{article.publisher_notes}</div>
              </div>
            )}
            {article.published_url && (
              <div className="card-field">
                <span className="cf-label" style={{ color: '#16a34a' }}>Published URL</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <a href={article.published_url} target="_blank" rel="noreferrer" className="doc-link" style={{ color: '#16a34a' }}>
                    {article.published_url.length > 50 ? article.published_url.slice(0, 50) + '…' : article.published_url}
                  </a>
                  {article.published_at && (
                    <span style={{ color: '#16a34a', fontSize: 11, opacity: 0.75 }}>{fmtDate(article.published_at)}</span>
                  )}
                </span>
              </div>
            )}
          </>
        )}
      </div>

      <div className="card-actions">
        {isEditing ? (
          <>
            <button className="btn-save" onClick={() => onSaveEdit(article.id)} disabled={savingEdit}>{savingEdit ? 'Saving…' : 'Save'}</button>
            <button className="btn-ghost" onClick={onCancelEdit} disabled={savingEdit}>Cancel</button>
          </>
        ) : (
          <>
            {isDraft && (
              <button className="btn-edit" onClick={() => onStartEdit(article)} disabled={deleting || confirming}>
                Edit
              </button>
            )}
            <button
              className="btn-delete-article"
              onClick={() => onDelete(article.id)}
              disabled={deleting || confirming || markingSent}
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
            {isOtherAssigned && (
              <button
                className="btn-send"
                onClick={() => onMarkSent(article.id)}
                disabled={markingSent || deleting}
              >
                {markingSent ? 'Updating…' : 'Mark as sent'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ClientPage() {
  const { clientId }    = useParams()
  const { role }        = useAuth()
  const navigate        = useNavigate()
  const [searchParams]  = useSearchParams()

  const [client,   setClient]   = useState(null)
  const [articles, setArticles] = useState([])
  const [loading,  setLoading]  = useState(true)

  // Status + month filters (shared across all roles)
  const [statusFilter, setStatusFilter] = useState('all')
  const [monthFilter,  setMonthFilter]  = useState('all')

  // Denise — submit form
  const [showForm,    setShowForm]    = useState(false)
  const [form,        setForm]        = useState(EMPTY_FORM)
  const [submitting,  setSubmitting]  = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [successMsg,  setSuccessMsg]  = useState('')

  // Denise — per-card actions
  const [confirmingId, setConfirmingId] = useState(null)
  const [deletingId,   setDeletingId]   = useState(null)

  // Denise — in-card edit for draft articles
  const [editingDeniseId,   setEditingDeniseId]   = useState(null)
  const [editingDeniseData, setEditingDeniseData] = useState(EMPTY_DENISE_EDIT)
  const [savingDeniseEdit,  setSavingDeniseEdit]  = useState(false)

  // Or — inline edit
  const [editingId, setEditingId] = useState(null)
  const [editData,  setEditData]  = useState(EMPTY_EDIT)
  const [saving,    setSaving]    = useState(false)
  const [saveError, setSaveError] = useState('')

  // Or — approve with optional notes
  const [approving,             setApproving]             = useState(null)   // id being approved
  const [approveNotesId,        setApproveNotesId]        = useState(null)   // id showing notes input
  const [approveNotes,          setApproveNotes]          = useState('')
  const [approveCustomNote,     setApproveCustomNote]     = useState('')
  const [approveChosenPublisher, setApproveChosenPublisher] = useState('')

  // Publisher
  const [markingId,   setMarkingId]   = useState(null)
  const [returningId, setReturningId] = useState(null)

  // Client Google Doc URL inline edit
  const [editingClientDoc, setEditingClientDoc] = useState(false)
  const [clientDocDraft,   setClientDocDraft]   = useState('')
  const [savingClientDoc,  setSavingClientDoc]  = useState(false)

  // Or — manual status override
  const [overridingId,   setOverridingId]   = useState(null)
  const [publishUrlId,   setPublishUrlId]   = useState(null)  // article showing URL input
  const [publishUrl,     setPublishUrl]     = useState('')
  const [publishUrlError, setPublishUrlError] = useState('')

  // ── Initial data fetch ───────────────────────────────────────────────────────

  useEffect(() => {
    if (!role) return
    const fetchAll = async () => {
      const [{ data: clientData }, { data: articlesData }] = await Promise.all([
        supabase.from('clients').select('id, name, google_doc_url').eq('id', clientId).single(),
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

  // ── Auto-open approve notes when navigated from the dashboard ───────────────

  useEffect(() => {
    const approveId = searchParams.get('approve')
    if (!approveId || articles.length === 0) return
    const article = articles.find(a => a.id === approveId && a.status === 'submitted')
    if (article) {
      setApproveNotesId(approveId)
      setApproveNotes('')
      setApproveCustomNote(article.custom_publisher_note ?? '')
      setApproveChosenPublisher(article.chosen_publisher || article.preferred_publisher || '')
      // Scroll the card into view after a brief tick so it's rendered
      setTimeout(() => {
        document.getElementById(`article-card-${approveId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    }
  }, [searchParams, articles])

  // ── Scroll + highlight when navigated via email deep link ────────────────────

  useEffect(() => {
    const articleId = searchParams.get('article')
    if (!articleId || articles.length === 0) return
    setTimeout(() => {
      const card = document.getElementById(`article-card-${articleId}`)
      if (!card) return
      card.scrollIntoView({ behavior: 'smooth', block: 'center' })
      card.classList.add('card-highlighted')
      setTimeout(() => card.classList.remove('card-highlighted'), 2500)
    }, 100)
  }, [searchParams, articles])

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

  // ── Denise: in-card edit for draft articles ─────────────────────────────────

  const startDeniseEdit = article => {
    setEditingDeniseId(article.id)
    setEditingDeniseData({
      google_doc_url:        article.google_doc_url        ?? '',
      magazine:              article.magazine              ?? '',
      preferred_publisher:   article.preferred_publisher   ?? '',
      custom_publisher_note: article.custom_publisher_note ?? '',
    })
  }

  const cancelDeniseEdit = () => setEditingDeniseId(null)

  const handleDeniseEditChange = e =>
    setEditingDeniseData(d => ({ ...d, [e.target.name]: e.target.value }))

  const saveDeniseEdit = async id => {
    setSavingDeniseEdit(true)
    const oldArticle     = articles.find(a => a.id === id)
    const magazineChanged = (editingDeniseData.magazine || '') !== (oldArticle?.magazine || '')
    const newMagazine    = editingDeniseData.magazine || null

    const { error } = await supabase.from('articles').update({
      google_doc_url:        editingDeniseData.google_doc_url        || null,
      magazine:              newMagazine,
      preferred_publisher:   editingDeniseData.preferred_publisher   || null,
      custom_publisher_note: editingDeniseData.custom_publisher_note || null,
      // Clear stale prices when magazine changes so the new fetch is authoritative
      ...(magazineChanged ? { price_presswhizz: null, price_linksme: null } : {}),
    }).eq('id', id)

    if (!error) {
      setArticles(prev => prev.map(a =>
        a.id === id
          ? {
              ...a,
              ...editingDeniseData,
              ...(magazineChanged ? { price_presswhizz: null, price_linksme: null } : {}),
            }
          : a
      ))
      setEditingDeniseId(null)
    }
    setSavingDeniseEdit(false)
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
        .insert({
          client_id: clientId, google_doc_url, magazine, preferred_publisher, status: 'draft',
          custom_publisher_note: form.custom_publisher_note || null,
        })
        .select()
        .single()
      if (error) throw error

      // Optimistic add — realtime will also fire but deduplicates
      setArticles(prev => [article, ...prev])
      setForm(EMPTY_FORM)
      setShowForm(false)
      setSuccessMsg(`"${magazine}" saved as draft.`)
      setTimeout(() => setSuccessMsg(''), 5000)
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
        .update({ status: 'submitted', reminder_sent: false })
        .eq('id', id)
      if (error) throw error

      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'submitted' } : a))
      sendNotify('submitted', client?.name ?? '', magazine, undefined, id, clientId)
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
      publisher_notes:     article.publisher_notes     ?? '',
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
      publisher_notes:     editData.publisher_notes    || null,
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

  // ── Or: approve (with optional notes; assignedTo only used for "other" articles) ──

  const approve = async (id, notes, assignedTo = null, customNote = null, chosenPub = null) => {
    setApproving(id)
    const article  = articles.find(a => a.id === id)
    // "Other" if Or explicitly picked Other in the panel, or article was already Other
    const isOther  = chosenPub === 'other' || article?.preferred_publisher === 'other'
    const updateData = { status: 'approved', reminder_sent: false }

    if (isOther) {
      updateData.chosen_publisher = 'other'
      if (assignedTo) updateData.assigned_to = assignedTo
    } else {
      // Use Or's panel selection, then article's existing choice, then preferred
      const effectiveChosen = chosenPub || article?.chosen_publisher || article?.preferred_publisher
      if (effectiveChosen) updateData.chosen_publisher = effectiveChosen
    }

    if (notes)      updateData.publisher_notes       = notes
    if (customNote) updateData.custom_publisher_note = customNote
    const { error } = await supabase.from('articles').update(updateData).eq('id', id)
    if (!error) {
      setArticles(prev => prev.map(a => a.id === id ? { ...a, ...updateData } : a))
      if (isOther && assignedTo === 'denise') {
        sendNotify('approved_other_denise', client?.name ?? '', article?.magazine ?? '', undefined, id, clientId)
      } else if (!isOther) {
        sendNotify('approved', client?.name ?? '', article?.magazine ?? '', undefined, id, clientId)
      }
      // assigned_to='or': Or assigned it to himself — no notification needed
    }
    setApproving(null)
    setApproveNotesId(null)
    setApproveNotes('')
    setApproveCustomNote('')
    setApproveChosenPublisher('')
  }

  // ── Publisher: return article to Or ─────────────────────────────────────────

  const returnToOr = async id => {
    const reason = window.prompt(
      'Reason for returning to Or:',
      'Too many words for Links.me',
    )
    if (reason === null) return   // cancelled
    setReturningId(id)
    const article = articles.find(a => a.id === id)
    const { error } = await supabase.from('articles').update({
      status:        'submitted',
      return_reason: reason || null,
      reminder_sent: false,
    }).eq('id', id)
    if (!error) {
      setArticles(prev => prev.map(a =>
        a.id === id ? { ...a, status: 'submitted', return_reason: reason || null } : a
      ))
      sendNotify('returned', client?.name ?? '', article?.magazine ?? '', reason || undefined, id, clientId)
    }
    setReturningId(null)
  }

  // ── Or: manual status override for sent_to_publisher ───────────────────────

  const confirmPublished = async (id, url) => {
    setOverridingId(id)
    setPublishUrlError('')
    const article = articles.find(a => a.id === id)
    const update = { status: 'published', published_url: url || null, published_at: new Date().toISOString() }

    console.log('[confirmPublished] updating article', id, 'with', update)
    const { data, error } = await supabase
      .from('articles')
      .update(update)
      .eq('id', id)
      .select()
    console.log('[confirmPublished] result — data:', data, '| error:', error)

    if (!error) {
      setArticles(prev => prev.map(a => a.id === id ? { ...a, ...update } : a))
      setPublishUrlId(null)
      setPublishUrl('')
      sendNotify('published', client?.name ?? '', article?.magazine ?? '', undefined, id, clientId)
    } else {
      // Keep input open so Or can see the error and retry
      setPublishUrlError(error.message || 'DB update failed — check console')
    }
    setOverridingId(null)
  }

  const markNotPublished = async id => {
    setOverridingId(id)
    const article = articles.find(a => a.id === id)
    const { error } = await supabase.from('articles').update({ status: 'not_published' }).eq('id', id)
    if (!error) {
      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'not_published' } : a))
      sendNotify('not_published', client?.name ?? '', article?.magazine ?? '', undefined, id, clientId)
    } else {
      console.error('[markNotPublished] DB error:', error)
    }
    setOverridingId(null)
  }

  const undoOverride = async id => {
    setOverridingId(id)
    const update = { status: 'sent_to_publisher', published_url: null }
    const { error } = await supabase.from('articles').update(update).eq('id', id)
    if (!error) {
      setArticles(prev => prev.map(a => a.id === id ? { ...a, ...update } : a))
    } else {
      console.error('[undoOverride] DB error:', error)
    }
    setOverridingId(null)
  }

  // ── Publisher: mark sent ─────────────────────────────────────────────────────

  const markSent = async id => {
    setMarkingId(id)
    const { error } = await supabase.from('articles').update({ status: 'sent_to_publisher' }).eq('id', id)
    if (!error) {
      const article = articles.find(a => a.id === id)
      setArticles(prev => prev.map(a => a.id === id ? { ...a, status: 'sent_to_publisher' } : a))
      sendNotify('sent', client?.name ?? '', article?.magazine ?? '', undefined, id, clientId)
    }
    setMarkingId(null)
  }

  // ── Client Google Doc URL edit ───────────────────────────────────────────────

  const startClientDocEdit = () => {
    setClientDocDraft(client?.google_doc_url ?? '')
    setEditingClientDoc(true)
  }

  const cancelClientDocEdit = () => setEditingClientDoc(false)

  const saveClientDoc = async () => {
    setSavingClientDoc(true)
    const url = clientDocDraft.trim() || null
    const { error } = await supabase.from('clients').update({ google_doc_url: url }).eq('id', clientId)
    if (!error) {
      setClient(prev => ({ ...prev, google_doc_url: url }))
      setEditingClientDoc(false)
    }
    setSavingClientDoc(false)
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

  // Derive sorted list of distinct months present in articles (newest first)
  const availableMonths = (() => {
    const seen = new Set()
    const months = []
    for (const a of articles) {
      if (!a.created_at) continue
      const d   = new Date(a.created_at)
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      if (!seen.has(key)) {
        seen.add(key)
        months.push({
          key,
          label: d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }),
        })
      }
    }
    return months.sort((a, b) => b.key.localeCompare(a.key))
  })()

  const visibleArticles = articles.filter(a => {
    const statusOk = statusFilter === 'all' || a.status === statusFilter
    const monthOk  = monthFilter  === 'all' || (
      a.created_at &&
      new Date(a.created_at).toISOString().slice(0, 7) === monthFilter
    )
    return statusOk && monthOk
  })

  return (
    <Layout>
      <div className="breadcrumb">
        <button className="breadcrumb-link" onClick={() => navigate('/clients')}>Clients</button>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">{client?.name ?? '…'}</span>
      </div>

      <div className="client-page-header">
        <div className="client-header-left">
          <div className="client-name-row">
            <h1 className="page-title" style={{ margin: 0 }}>
              {client?.name}
              {articles.length > 0 && (
                <span className="article-count">
                  {statusFilter === 'all' && monthFilter === 'all'
                    ? `${articles.length} total`
                    : `${visibleArticles.length} of ${articles.length}`}
                </span>
              )}
            </h1>
            <button
              className="btn-pencil"
              onClick={editingClientDoc ? cancelClientDocEdit : startClientDocEdit}
              title={editingClientDoc ? 'Cancel edit' : 'Edit client Google Doc URL'}
            >
              {editingClientDoc ? '✕' : '✎'}
            </button>
          </div>

          {editingClientDoc ? (
            <div className="client-doc-edit-row">
              <input
                type="url"
                className="edit-input"
                value={clientDocDraft}
                onChange={e => setClientDocDraft(e.target.value)}
                placeholder="https://docs.google.com/…"
                autoFocus
                onKeyDown={e => {
                  if (e.key === 'Enter')  saveClientDoc()
                  if (e.key === 'Escape') cancelClientDocEdit()
                }}
              />
              <button className="btn-save" onClick={saveClientDoc} disabled={savingClientDoc}>
                {savingClientDoc ? 'Saving…' : 'Save'}
              </button>
              <button className="btn-ghost" onClick={cancelClientDocEdit}>Cancel</button>
            </div>
          ) : client?.google_doc_url ? (
            <a href={client.google_doc_url} target="_blank" rel="noreferrer" className="client-doc-link">
              📄 Client Doc ↗
            </a>
          ) : null}
        </div>

        {role === 'denise' && (
          <button
            className={`btn-submit-article ${showForm ? 'active' : ''}`}
            onClick={() => { setShowForm(v => !v); setSubmitError('') }}
          >
            {showForm ? '✕ Cancel' : '+ Submit New Article'}
          </button>
        )}
      </div>

      {/* ── Filters bar: status buttons + month dropdown ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <div className="status-filter-bar" style={{ marginBottom: 0, flex: 1 }}>
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
        <select
          value={monthFilter}
          onChange={e => setMonthFilter(e.target.value)}
          style={{
            height: 32,
            padding: '0 10px',
            border: '1px solid #d1d5db',
            borderRadius: 6,
            background: '#ffffff',
            color: '#111827',
            fontSize: 13,
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <option value="all">All months</option>
          {availableMonths.map(({ key, label }) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
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
                  <option value="other">Other</option>
                </select>
              </div>
              {form.preferred_publisher === 'other' && (
                <div className="field">
                  <label>Publisher destination</label>
                  <input
                    type="text"
                    name="custom_publisher_note"
                    value={form.custom_publisher_note}
                    onChange={handleFormChange}
                    placeholder="e.g. Outreach.io, direct email to editor…"
                  />
                </div>
              )}
            </div>
            {submitError && <p className="form-error">{submitError}</p>}
            <button type="submit" className="btn-primary submit-btn" disabled={submitting}>
              {submitting ? 'Saving…' : 'Save Draft'}
            </button>
          </form>
        </div>
      )}

      {/* ── Articles ── */}
      {(() => {
        if (visibleArticles.length === 0) {
          return (
            <div className="empty-state">
              {statusFilter !== 'all'
                ? <p>No {STATUS_FILTERS.find(f => f.key === statusFilter)?.label.toLowerCase()} articles for this client.</p>
                : <p>No articles yet for this client.</p>
              }
            </div>
          )
        }

        // ── Per-article card renderer (role-aware) ──────────────────────────

        const renderDeniseCard = article => (
          <DeniseArticleCard
            key={article.id}
            id={`article-card-${article.id}`}
            article={article}
            onDelete={deleteArticle}
            onConfirm={confirmAndNotifyOr}
            confirming={confirmingId === article.id}
            deleting={deletingId === article.id}
            isEditing={editingDeniseId === article.id}
            editData={editingDeniseData}
            onEditChange={handleDeniseEditChange}
            onSaveEdit={saveDeniseEdit}
            onStartEdit={startDeniseEdit}
            onCancelEdit={cancelDeniseEdit}
            savingEdit={savingDeniseEdit}
            onMarkSent={markSent}
            markingSent={markingId === article.id}
          />
        )

        const renderOrCard = article => {
          const isEditing     = editingId === article.id
          const isOther       = article.preferred_publisher === 'other'
          const canActOnIt    = article.status === 'submitted'
          const pricesLoading = !isOther && article.status === 'submitted'
            && article.price_presswhizz == null
            && article.price_linksme    == null
          return (
            <div key={article.id} id={`article-card-${article.id}`} className={`article-card ${isEditing ? 'editing' : ''}`}>
              <div className="card-header">
                <span className="card-magazine">
                  {isEditing
                    ? <input name="magazine" value={editData.magazine} onChange={handleEditChange} className="edit-input" placeholder="Magazine" />
                    : (article.magazine ?? '—')
                  }
                </span>
                {!isEditing && article.created_at && (
                  <span className="card-date">{fmtDate(article.created_at)}</span>
                )}
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
                {!isOther && (
                  <div className="card-field-row prices">
                    {pricesLoading && !isEditing ? (
                      <div className="price-fetching" style={{ gridColumn: '1 / -1' }}>
                        <span className="price-spinner" />
                        Fetching prices…
                      </div>
                    ) : (
                      <>
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
                      </>
                    )}
                  </div>
                )}
                <div className="card-field">
                  <span className="cf-label">Notes for Publisher</span>
                  {isEditing
                    ? <textarea
                        name="publisher_notes"
                        value={editData.publisher_notes}
                        onChange={handleEditChange}
                        className="edit-input"
                        placeholder="Optional notes for the publisher…"
                        rows={2}
                        style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13 }}
                      />
                    : article.publisher_notes
                      ? <span style={{ fontSize: 13, color: 'var(--text)' }}>{article.publisher_notes}</span>
                      : <span className="cf-empty">—</span>
                  }
                </div>
                {article.custom_publisher_note && !isEditing && (
                  <div className="card-field">
                    <span className="cf-label">Publisher destination</span>
                    <span style={{ fontSize: 13, color: 'var(--text)', fontStyle: 'italic' }}>{article.custom_publisher_note}</span>
                  </div>
                )}
                {article.return_reason && !isEditing && (
                  <div className="card-field">
                    <span className="cf-label" style={{ color: '#dc2626' }}>Returned — reason</span>
                    <span style={{ fontSize: 13, color: '#dc2626' }}>{article.return_reason}</span>
                  </div>
                )}
                {article.published_url && !isEditing && (
                  <div className="card-field">
                    <span className="cf-label" style={{ color: '#16a34a' }}>Published URL</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <a href={article.published_url} target="_blank" rel="noreferrer" className="doc-link" style={{ color: '#16a34a' }}>
                        {article.published_url.length > 50 ? article.published_url.slice(0, 50) + '…' : article.published_url}
                      </a>
                      {article.published_at && (
                        <span style={{ color: '#16a34a', fontSize: 11, opacity: 0.75 }}>{fmtDate(article.published_at)}</span>
                      )}
                    </span>
                  </div>
                )}
                {saveError && isEditing && <p className="form-error" style={{ marginTop: 8 }}>{saveError}</p>}
              </div>

              <div className="card-actions" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
                {isEditing ? (
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn-save" onClick={() => saveEdit(article.id)} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                    <button className="btn-ghost" onClick={cancelEdit} disabled={saving}>Cancel</button>
                  </div>
                ) : approveNotesId === article.id ? (
                  <div className="approve-notes-area">
                    <select
                      value={approveChosenPublisher}
                      onChange={e => setApproveChosenPublisher(e.target.value)}
                      style={{ width: '100%', marginBottom: 6, padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 13 }}
                    >
                      <option value="">— Publisher —</option>
                      {PUBLISHERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                    </select>
                    <textarea
                      value={approveNotes}
                      onChange={e => setApproveNotes(e.target.value)}
                      placeholder={approveChosenPublisher === 'other' ? 'Optional notes…' : 'Optional notes for publisher…'}
                      autoFocus
                    />
                    {approveChosenPublisher === 'other' && (
                      <input
                        type="text"
                        value={approveCustomNote}
                        onChange={e => setApproveCustomNote(e.target.value)}
                        placeholder="Publisher destination (e.g. Outreach.io, direct email…)"
                        style={{ marginTop: 6, width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 13 }}
                      />
                    )}
                    <div className="approve-notes-actions">
                      {approveChosenPublisher === 'other' ? (
                        <>
                          <button
                            className="btn-approve"
                            style={{ marginLeft: 0 }}
                            onClick={() => approve(article.id, approveNotes, 'denise', approveCustomNote || null, 'other')}
                            disabled={approving === article.id}
                          >
                            {approving === article.id ? 'Approving…' : '→ Assign to Denise'}
                          </button>
                          <button
                            className="btn-send"
                            style={{ marginLeft: 0 }}
                            onClick={() => approve(article.id, approveNotes, 'or', approveCustomNote || null, 'other')}
                            disabled={approving === article.id}
                          >
                            {approving === article.id ? 'Approving…' : '→ Handle myself'}
                          </button>
                        </>
                      ) : (
                        <button
                          className="btn-approve"
                          style={{ marginLeft: 0 }}
                          onClick={() => approve(article.id, approveNotes, null, approveCustomNote || null, approveChosenPublisher || null)}
                          disabled={approving === article.id}
                        >
                          {approving === article.id ? 'Approving…' : 'Confirm Approve'}
                        </button>
                      )}
                      <button className="btn-ghost" onClick={() => { setApproveNotesId(null); setApproveNotes(''); setApproveCustomNote(''); setApproveChosenPublisher('') }}>Cancel</button>
                    </div>
                  </div>
                ) : publishUrlId === article.id ? (
                  <div className="approve-notes-area">
                    <input
                      type="url"
                      className="edit-input"
                      placeholder="https://… (published article URL)"
                      value={publishUrl}
                      onChange={e => { setPublishUrl(e.target.value); setPublishUrlError('') }}
                      autoFocus
                      onKeyDown={e => { if (e.key === 'Enter') confirmPublished(article.id, publishUrl) }}
                    />
                    {publishUrlError && (
                      <p className="form-error" style={{ marginTop: 4, fontSize: 12 }}>{publishUrlError}</p>
                    )}
                    <div className="approve-notes-actions">
                      <button
                        className="btn-override-published"
                        style={{ marginLeft: 0 }}
                        onClick={() => confirmPublished(article.id, publishUrl)}
                        disabled={overridingId === article.id}
                      >
                        {overridingId === article.id ? 'Saving…' : 'Confirm Published'}
                      </button>
                      <button className="btn-ghost" onClick={() => { setPublishUrlId(null); setPublishUrl(''); setPublishUrlError('') }}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {canActOnIt && (
                      <>
                        <button className="btn-edit" onClick={() => startEdit(article)}>Edit</button>
                        <button
                          className="btn-approve"
                          style={{ marginLeft: 0 }}
                          onClick={() => {
                            setApproveNotesId(article.id)
                            setApproveNotes('')
                            setApproveCustomNote(article.custom_publisher_note ?? '')
                            setApproveChosenPublisher(article.chosen_publisher || article.preferred_publisher || '')
                          }}
                          disabled={approving === article.id}
                        >
                          Approve
                        </button>
                      </>
                    )}
                    {article.status === 'approved' && article.assigned_to === 'or' && (
                      <button
                        className="btn-send"
                        onClick={() => markSent(article.id)}
                        disabled={markingId === article.id}
                      >
                        {markingId === article.id ? 'Updating…' : 'Mark as sent'}
                      </button>
                    )}
                    {article.status === 'sent_to_publisher' && (
                      <>
                        <button
                          className="btn-override-published"
                          onClick={() => { setPublishUrlId(article.id); setPublishUrl('') }}
                          disabled={overridingId === article.id}
                        >
                          ✓ Published
                        </button>
                        <button
                          className="btn-override-rejected"
                          onClick={() => markNotPublished(article.id)}
                          disabled={overridingId === article.id}
                        >
                          {overridingId === article.id ? '…' : '✗ Not Published'}
                        </button>
                      </>
                    )}
                    {(article.status === 'published' || article.status === 'not_published') && (
                      <button
                        className="btn-undo"
                        onClick={() => undoOverride(article.id)}
                        disabled={overridingId === article.id}
                      >
                        {overridingId === article.id ? '…' : '↩ Undo'}
                      </button>
                    )}
                    <button
                      className="btn-delete-article"
                      onClick={() => deleteArticle(article.id)}
                      disabled={deletingId === article.id || saving}
                    >
                      {deletingId === article.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )
        }

        const renderPublisherCard = article => {
          const chosenPrice = article.chosen_publisher === 'presswhizz'
            ? article.price_presswhizz
            : article.chosen_publisher === 'linksme'
              ? article.price_linksme
              : null
          return (
            <div key={article.id} id={`article-card-${article.id}`} className="article-card">
              <div className="card-header">
                <span className="card-magazine">{article.magazine ?? '—'}</span>
                {article.created_at && (
                  <span className="card-date">{fmtDate(article.created_at)}</span>
                )}
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
                {article.custom_publisher_note && (
                  <div className="card-field" style={{ marginTop: 8 }}>
                    <span className="cf-label">Publisher destination</span>
                    <span style={{ fontSize: 13, color: 'var(--text)', fontStyle: 'italic' }}>{article.custom_publisher_note}</span>
                  </div>
                )}
                {article.publisher_notes && (
                  <div className="card-field" style={{ marginTop: 8 }}>
                    <div className="publisher-notes-label">Notes from Or</div>
                    <div className="publisher-notes">{article.publisher_notes}</div>
                  </div>
                )}
                {article.published_url && (
                  <div className="card-field">
                    <span className="cf-label" style={{ color: '#16a34a' }}>Published URL</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <a href={article.published_url} target="_blank" rel="noreferrer" className="doc-link" style={{ color: '#16a34a' }}>
                        {article.published_url.length > 50 ? article.published_url.slice(0, 50) + '…' : article.published_url}
                      </a>
                      {article.published_at && (
                        <span style={{ color: '#16a34a', fontSize: 11, opacity: 0.75 }}>{fmtDate(article.published_at)}</span>
                      )}
                    </span>
                  </div>
                )}
              </div>

              <div className="card-actions" style={{ flexDirection: 'column', gap: 8 }}>
                {article.status === 'approved' && article.preferred_publisher !== 'other' && (
                  <>
                    <button className="btn-send" onClick={() => markSent(article.id)} disabled={markingId === article.id || returningId === article.id}>
                      {markingId === article.id ? 'Updating…' : 'Mark as sent to publisher'}
                    </button>
                    <button className="btn-return" onClick={() => returnToOr(article.id)} disabled={returningId === article.id || markingId === article.id}>
                      {returningId === article.id ? 'Returning…' : 'Return to Or'}
                    </button>
                  </>
                )}
                <button
                  className="btn-delete-article"
                  onClick={() => deleteArticle(article.id)}
                  disabled={deletingId === article.id || markingId === article.id || returningId === article.id}
                >
                  {deletingId === article.id ? 'Deleting…' : 'Delete'}
                </button>
              </div>
            </div>
          )
        }

        const renderCard = role === 'denise'    ? renderDeniseCard
                         : role === 'or'        ? renderOrCard
                         : /* publisher */        renderPublisherCard

        // ── Publisher split by chosen / preferred publisher ─────────────────
        const effectivePublisher = a => a.chosen_publisher || a.preferred_publisher
        const pwArticles  = visibleArticles.filter(a => effectivePublisher(a) === 'presswhizz')
        const lmArticles  = visibleArticles.filter(a => effectivePublisher(a) === 'linksme')
        const otherArticles = visibleArticles.filter(
          a => !['presswhizz', 'linksme'].includes(effectivePublisher(a))
        )
        const hasBoth = pwArticles.length > 0 && lmArticles.length > 0

        if (hasBoth) {
          return (
            <div className="clients-split" style={{ alignItems: 'flex-start' }}>
              <div className="clients-col">
                <div className="clients-col-title">PressWhizz</div>
                <div className="article-grid">
                  {pwArticles.map(renderCard)}
                  {otherArticles.map(renderCard)}
                </div>
              </div>
              <div className="clients-col">
                <div className="clients-col-title">Links.me</div>
                <div className="article-grid">
                  {lmArticles.map(renderCard)}
                </div>
              </div>
            </div>
          )
        }

        return <div className="article-grid">{visibleArticles.map(renderCard)}</div>
      })()}
    </Layout>
  )
}

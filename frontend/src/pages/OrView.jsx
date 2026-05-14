import { useEffect, useState } from 'react'
import Layout from '../components/Layout'
import StatusBadge from '../components/StatusBadge'
import { supabase } from '../lib/supabase'

const PUBLISHERS = [
  { value: 'presswhizz', label: 'PressWhizz' },
  { value: 'linksme',    label: 'Links.me'   },
]

const EMPTY_EDIT = {
  google_doc_url:      '',
  magazine:            '',
  preferred_publisher: '',
  chosen_publisher:    '',
  price_presswhizz:    '',
  price_linksme:       '',
}

export default function OrView() {
  const [articles,   setArticles]   = useState([])
  const [loading,    setLoading]    = useState(true)
  const [editingId,  setEditingId]  = useState(null)
  const [editData,   setEditData]   = useState(EMPTY_EDIT)
  const [saving,     setSaving]     = useState(false)
  const [approving,  setApproving]  = useState(null)
  const [saveError,  setSaveError]  = useState('')

  const fetchArticles = async () => {
    setLoading(true)
    const { data } = await supabase
      .from('articles')
      .select('*, clients(name)')
      .eq('status', 'submitted')
      .order('created_at', { ascending: false })
    setArticles(data ?? [])
    setLoading(false)
  }

  useEffect(() => { fetchArticles() }, [])

  const startEdit = (article) => {
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

  const handleEditChange = e =>
    setEditData(d => ({ ...d, [e.target.name]: e.target.value }))

  const saveEdit = async (id) => {
    setSaving(true)
    setSaveError('')
    const { error } = await supabase
      .from('articles')
      .update({
        google_doc_url:      editData.google_doc_url      || null,
        magazine:            editData.magazine            || null,
        preferred_publisher: editData.preferred_publisher || null,
        chosen_publisher:    editData.chosen_publisher    || null,
        price_presswhizz:    editData.price_presswhizz !== '' ? parseFloat(editData.price_presswhizz) : null,
        price_linksme:       editData.price_linksme     !== '' ? parseFloat(editData.price_linksme)    : null,
      })
      .eq('id', id)

    if (error) {
      setSaveError(error.message)
    } else {
      setArticles(prev => prev.map(a =>
        a.id === id
          ? { ...a, ...editData,
              price_presswhizz: editData.price_presswhizz !== '' ? parseFloat(editData.price_presswhizz) : null,
              price_linksme:    editData.price_linksme    !== '' ? parseFloat(editData.price_linksme)    : null,
            }
          : a
      ))
      setEditingId(null)
    }
    setSaving(false)
  }

  const approve = async (id) => {
    setApproving(id)
    const { error } = await supabase
      .from('articles')
      .update({ status: 'approved' })
      .eq('id', id)
    if (!error) setArticles(prev => prev.filter(a => a.id !== id))
    setApproving(null)
  }

  const fmtPrice = v => v != null ? `₪${Number(v).toLocaleString()}` : '—'

  // ── Loading / empty ─────────────────────────────────────────────────────────
  if (loading) return <Layout title="Review Articles"><div className="loading-inline">Loading…</div></Layout>

  return (
    <Layout title={`Review Articles ${articles.length > 0 ? `(${articles.length})` : ''}`}>
      {articles.length === 0 ? (
        <div className="empty-state">
          <p>No articles pending review.</p>
        </div>
      ) : (
        <div className="article-grid">
          {articles.map(article => {
            const isEditing = editingId === article.id

            return (
              <div key={article.id} className={`article-card ${isEditing ? 'editing' : ''}`}>

                {/* ── Card header ── */}
                <div className="card-header">
                  <div className="card-header-left">
                    <span className="card-client">{article.clients?.name ?? '—'}</span>
                    <span className="card-sep">·</span>
                    <span className="card-magazine">
                      {isEditing
                        ? <input name="magazine" value={editData.magazine} onChange={handleEditChange} className="edit-input" placeholder="Magazine" />
                        : (article.magazine ?? '—')
                      }
                    </span>
                  </div>
                  <StatusBadge status={article.status} />
                </div>

                {/* ── Card body ── */}
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
                        : <span className="pub-tag">{article.preferred_publisher ?? '—'}</span>
                      }
                    </div>
                    <div className="card-field">
                      <span className="cf-label">Chosen publisher</span>
                      {isEditing
                        ? <select name="chosen_publisher" value={editData.chosen_publisher} onChange={handleEditChange} className="edit-select">
                            <option value="">—</option>
                            {PUBLISHERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                          </select>
                        : <span className={article.chosen_publisher ? 'pub-tag chosen' : 'cf-empty'}>{article.chosen_publisher ?? '—'}</span>
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

                  {saveError && <p className="form-error" style={{ marginTop: 8 }}>{saveError}</p>}
                </div>

                {/* ── Card actions ── */}
                <div className="card-actions">
                  {isEditing ? (
                    <>
                      <button className="btn-save" onClick={() => saveEdit(article.id)} disabled={saving}>
                        {saving ? 'Saving…' : 'Save'}
                      </button>
                      <button className="btn-ghost" onClick={cancelEdit} disabled={saving}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button className="btn-edit" onClick={() => startEdit(article)}>
                        Edit
                      </button>
                      <button
                        className="btn-approve"
                        onClick={() => approve(article.id)}
                        disabled={approving === article.id}
                      >
                        {approving === article.id ? 'Approving…' : 'Approve'}
                      </button>
                    </>
                  )}
                </div>

              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}

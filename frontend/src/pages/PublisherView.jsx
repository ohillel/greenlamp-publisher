import { useEffect, useState } from 'react'
import Layout from '../components/Layout'
import StatusBadge from '../components/StatusBadge'
import { supabase } from '../lib/supabase'

export default function PublisherView() {
  const [articles,  setArticles]  = useState([])
  const [loading,   setLoading]   = useState(true)
  const [markingId, setMarkingId] = useState(null)

  useEffect(() => {
    const fetch = async () => {
      const { data } = await supabase
        .from('articles')
        .select('*, clients(name)')
        .eq('status', 'approved')
        .order('created_at', { ascending: false })
      setArticles(data ?? [])
      setLoading(false)
    }
    fetch()
  }, [])

  const markSent = async (id) => {
    setMarkingId(id)
    const { error } = await supabase
      .from('articles')
      .update({ status: 'sent_to_publisher' })
      .eq('id', id)
    if (!error) setArticles(prev => prev.filter(a => a.id !== id))
    setMarkingId(null)
  }

  const chosenPrice = (article) => {
    if (!article.chosen_publisher) return null
    return article.chosen_publisher === 'presswhizz'
      ? article.price_presswhizz
      : article.price_linksme
  }

  const fmtPrice = v => v != null ? `₪${Number(v).toLocaleString()}` : '—'

  if (loading) return <Layout title="Approved Articles"><div className="loading-inline">Loading…</div></Layout>

  return (
    <Layout title={`Approved Articles ${articles.length > 0 ? `(${articles.length})` : ''}`}>
      {articles.length === 0 ? (
        <div className="empty-state">
          <p>No approved articles waiting to be sent.</p>
        </div>
      ) : (
        <div className="article-grid">
          {articles.map(article => (
            <div key={article.id} className="article-card">

              {/* ── Card header ── */}
              <div className="card-header">
                <div className="card-header-left">
                  <span className="card-client">{article.clients?.name ?? '—'}</span>
                  <span className="card-sep">·</span>
                  <span className="card-magazine">{article.magazine ?? '—'}</span>
                </div>
                <StatusBadge status={article.status} />
              </div>

              {/* ── Card body ── */}
              <div className="card-body">

                <div className="card-field">
                  <span className="cf-label">Google Doc</span>
                  {article.google_doc_url
                    ? <a href={article.google_doc_url} target="_blank" rel="noreferrer" className="doc-link">Open →</a>
                    : <span className="cf-empty">—</span>
                  }
                </div>

                <div className="card-field-row">
                  <div className="card-field">
                    <span className="cf-label">Publisher</span>
                    <span className={article.chosen_publisher ? 'pub-tag chosen' : 'cf-empty'}>
                      {article.chosen_publisher
                        ? (article.chosen_publisher === 'presswhizz' ? 'PressWhizz' : 'Links.me')
                        : '—'
                      }
                    </span>
                  </div>
                  <div className="card-field">
                    <span className="cf-label">Price</span>
                    <span className="price-val highlight">{fmtPrice(chosenPrice(article))}</span>
                  </div>
                </div>

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

              </div>

              {/* ── Card actions ── */}
              <div className="card-actions">
                <button
                  className="btn-send"
                  onClick={() => markSent(article.id)}
                  disabled={markingId === article.id}
                >
                  {markingId === article.id ? 'Updating…' : 'Mark as sent to publisher'}
                </button>
              </div>

            </div>
          ))}
        </div>
      )}
    </Layout>
  )
}

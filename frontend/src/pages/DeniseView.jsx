import { useEffect, useState } from 'react'
import Layout from '../components/Layout'
import { supabase } from '../lib/supabase'

// Deterministic mock prices based on magazine name — replaced once real APIs exist
function mockPrice(magazine, seed) {
  let h = seed
  for (let i = 0; i < magazine.length; i++) h = (h * 31 + magazine.charCodeAt(i)) & 0x7fffffff
  return 700 + (h % 900) // ₪700–₪1600
}

const EMPTY_FORM = { client_id: '', google_doc_url: '', magazine: '', preferred_publisher: '' }

export default function DeniseView() {
  const [clients,    setClients]    = useState([])
  const [form,       setForm]       = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error,      setError]      = useState('')
  const [submitted,  setSubmitted]  = useState(null)   // article after submit
  const [pricesDone, setPricesDone] = useState(false)

  useEffect(() => {
    supabase.from('clients').select('id, name').order('name')
      .then(({ data }) => setClients(data ?? []))
  }, [])

  const handleChange = e =>
    setForm(f => ({ ...f, [e.target.name]: e.target.value }))

  const handleSubmit = async e => {
    e.preventDefault()
    const { client_id, google_doc_url, magazine, preferred_publisher } = form
    if (!client_id || !google_doc_url || !magazine || !preferred_publisher) {
      setError('All fields are required.')
      return
    }
    setError('')
    setSubmitting(true)
    try {
      const { data: article, error: err } = await supabase
        .from('articles')
        .insert({ client_id, google_doc_url, magazine, preferred_publisher, status: 'submitted' })
        .select('*, clients(name)')
        .single()
      if (err) throw err

      setSubmitted(article)
      setPricesDone(false)
      setForm(EMPTY_FORM)

      // Simulate price comparison (replace with real API calls later)
      setTimeout(async () => {
        const pw = mockPrice(magazine, 17)
        const lm = mockPrice(magazine, 89)
        await supabase
          .from('articles')
          .update({ price_presswhizz: pw, price_linksme: lm })
          .eq('id', article.id)
        setSubmitted(prev => prev ? { ...prev, price_presswhizz: pw, price_linksme: lm } : prev)
        setPricesDone(true)
      }, 2000)
    } catch (err) {
      setError(err.message ?? 'Submission failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Success screen ──────────────────────────────────────────────────────────
  if (submitted) {
    return (
      <Layout title="Submit Article">
        <div className="success-panel">
          <div className="success-icon">✓</div>
          <h2>Article submitted successfully!</h2>
          <p className="success-sub">
            It's now pending review by Or.
          </p>

          <div className="success-details">
            <div className="detail-row">
              <span className="detail-label">Client</span>
              <span>{submitted.clients?.name ?? '—'}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Magazine</span>
              <span>{submitted.magazine}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Preferred publisher</span>
              <span className="pub-tag">{submitted.preferred_publisher}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Google Doc</span>
              <a href={submitted.google_doc_url} target="_blank" rel="noreferrer" className="doc-link">
                Open →
              </a>
            </div>

            <div className="prices-section">
              {pricesDone ? (
                <>
                  <p className="prices-heading">Price comparison</p>
                  <div className="price-cards">
                    <div className={`price-card ${submitted.preferred_publisher === 'presswhizz' ? 'preferred' : ''}`}>
                      <span className="price-pub">PressWhizz</span>
                      <span className="price-val">₪{submitted.price_presswhizz?.toLocaleString()}</span>
                      {submitted.preferred_publisher === 'presswhizz' && <span className="pref-tag">preferred</span>}
                    </div>
                    <div className={`price-card ${submitted.preferred_publisher === 'linksme' ? 'preferred' : ''}`}>
                      <span className="price-pub">Links.me</span>
                      <span className="price-val">₪{submitted.price_linksme?.toLocaleString()}</span>
                      {submitted.preferred_publisher === 'linksme' && <span className="pref-tag">preferred</span>}
                    </div>
                  </div>
                </>
              ) : (
                <p className="fetching-prices">⏳ Fetching price comparison…</p>
              )}
            </div>
          </div>

          <button className="btn-primary submit-another" onClick={() => { setSubmitted(null); setPricesDone(false) }}>
            Submit another article
          </button>
        </div>
      </Layout>
    )
  }

  // ── Submission form ─────────────────────────────────────────────────────────
  return (
    <Layout title="Submit Article">
      <div className="form-card">
        <form onSubmit={handleSubmit} noValidate>

          <div className="field">
            <label htmlFor="client_id">Client</label>
            {clients.length === 0 ? (
              <p className="field-hint">No clients found — add clients in Supabase first.</p>
            ) : (
              <select id="client_id" name="client_id" value={form.client_id} onChange={handleChange} required>
                <option value="">— Select client —</option>
                {clients.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>

          <div className="field">
            <label htmlFor="google_doc_url">Google Doc URL</label>
            <input
              id="google_doc_url"
              type="url"
              name="google_doc_url"
              value={form.google_doc_url}
              onChange={handleChange}
              placeholder="https://docs.google.com/document/d/…"
              required
            />
          </div>

          <div className="field">
            <label htmlFor="magazine">Magazine</label>
            <input
              id="magazine"
              type="text"
              name="magazine"
              value={form.magazine}
              onChange={handleChange}
              placeholder="e.g. Walla, Ynet, Calcalist"
              required
            />
          </div>

          <div className="field">
            <label htmlFor="preferred_publisher">Preferred Publisher</label>
            <select id="preferred_publisher" name="preferred_publisher" value={form.preferred_publisher} onChange={handleChange} required>
              <option value="">— Select publisher —</option>
              <option value="presswhizz">PressWhizz</option>
              <option value="linksme">Links.me</option>
            </select>
          </div>

          {error && <p className="form-error">{error}</p>}

          <button type="submit" className="btn-primary" disabled={submitting || clients.length === 0}>
            {submitting ? 'Submitting…' : 'Submit Article'}
          </button>
        </form>
      </div>
    </Layout>
  )
}

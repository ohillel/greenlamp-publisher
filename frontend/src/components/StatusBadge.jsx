const CONFIG = {
  draft:             { label: 'Draft',              bg: '#F4F4F4', color: '#777'    },
  submitted:         { label: 'Submitted',          bg: '#EBF5FB', color: '#1A6EA0' },
  approved:          { label: 'Approved',            bg: '#EAFAF1', color: '#1E8449' },
  sent_to_publisher: { label: 'Sent to Publisher',   bg: '#FEF9E7', color: '#9A7D0A' },
  published:         { label: 'Published',           bg: '#E8F8F5', color: '#117A65' },
  not_published:     { label: 'Not Published',       bg: '#FDEDEC', color: '#C0392B' },
}

export default function StatusBadge({ status }) {
  const cfg = CONFIG[status] ?? { label: status, bg: '#f0f0f0', color: '#555' }
  return (
    <span className="status-badge" style={{ background: cfg.bg, color: cfg.color }}>
      {cfg.label}
    </span>
  )
}

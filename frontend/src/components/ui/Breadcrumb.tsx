import { Link } from 'react-router-dom'
export function Breadcrumb({ items }: { items: Array<{ label: string; to?: string }> }) {
  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      {items.map((it, i) => (
        <span key={it.to ?? it.label} className="breadcrumb-item">
          {it.to ? <Link to={it.to}>{it.label}</Link> : <span className="breadcrumb-current">{it.label}</span>}
          {i < items.length - 1 && <span className="breadcrumb-sep">/</span>}
        </span>
      ))}
    </nav>
  )
}

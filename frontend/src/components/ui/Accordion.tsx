import { useState, type ReactNode } from 'react'
export function Accordion({ items }: { items: Array<{ id: string; header: ReactNode; content: ReactNode }> }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="accordion">
      {items.map((it) => (
        <div key={it.id} className={`accordion-item ${open === it.id ? 'open' : ''}`}>
          <button type="button" className="accordion-header" onClick={() => setOpen(open === it.id ? null : it.id)} aria-expanded={open === it.id}>
            {it.header}
            <span className="accordion-chevron" aria-hidden>▾</span>
          </button>
          {open === it.id && <div className="accordion-content">{it.content}</div>}
        </div>
      ))}
    </div>
  )
}

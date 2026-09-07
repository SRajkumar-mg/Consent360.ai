import { createContext, useCallback, useContext, useId, useState, type CSSProperties, type ReactNode } from 'react'
import { IconAlert, IconCheck, IconInbox, IconX } from './icons'
import { useFocusTrap } from '../hooks/useFocusTrap'

export * from './ui/index'

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  const cls = `badge b-${status.toUpperCase().replace(/\s+/g, '_')}`
  return (
    <span className={cls}>
      <span className="badge-dot" />
      {children ?? formatStatus(status)}
    </span>
  )
}

export function formatStatus(status: string): string {
  if (!status) return '—'
  return status.replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function formatDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/**
 * R2-08: a percentage that may not exist.
 *
 * Several backend metrics declare a `float` in their OpenAPI schema but return
 * null when the denominator is empty (erasure notice compliance with nothing
 * executed, Consent Manager availability with no calls, K-08 with no
 * withdrawal that reached a processor). Rendering those as `0%` would be the
 * same lie the compliance dashboard exists to avoid: nothing measured is not
 * zero. Verified against the live 8010 API, not against the schema.
 */
export function formatPct(value: number | null | undefined, digits = 0, empty = 'No data yet'): string {
  return value == null ? empty : `${value.toFixed(digits)}%`
}

export function daysUntil(iso?: string | null): number | null {
  if (!iso) return null
  const diff = new Date(iso).getTime() - Date.now()
  return Math.ceil(diff / 86400000)
}

// ---------- PII masking ----------
export function maskPhone(phone?: string | null): string {
  if (!phone) return '—'
  const digits = phone.replace(/\D/g, '')
  if (digits.length < 3) return '****'
  const head = digits.slice(0, 2)
  const tail = digits.slice(-1)
  return head + '*'.repeat(digits.length - 3) + tail
}

export function maskEmail(email?: string | null): string {
  if (!email) return '—'
  const at = email.lastIndexOf('@')
  if (at <= 0) return '***'
  const local = email.slice(0, at)
  const domain = email.slice(at)
  if (local.length <= 2) return local[0] + '***' + domain
  return local.slice(0, 2) + '***' + local.slice(-1) + domain
}

export function MaskedValue({ value, type, label = 'Masked for privacy' }: { value: string; type: 'phone' | 'email'; label?: string }) {
  const text = type === 'phone' ? maskPhone(value) : maskEmail(value)
  return <span className="masked" title={label}>{text}</span>
}

// ---------- Toast system ----------
interface Toast {
  id: number
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

const ToastContext = createContext<(type: Toast['type'], message: string) => void>(() => undefined)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((type: Toast['type'], message: string) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, type, message }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>
            <div className="flex">
              {t.type === 'error' ? <IconAlert size={16} style={{ color: 'var(--danger)' }} /> : t.type === 'success' ? <IconCheck size={16} style={{ color: 'var(--success)' }} /> : <IconAlert size={16} style={{ color: 'var(--warning)' }} />}
              <span>{t.message}</span>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}

// ---------- Modal ----------
export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  const titleId = useId()
  // Always called (Rules of Hooks) — the hook itself no-ops while `open` is false.
  const dialogRef = useFocusTrap<HTMLDivElement>(open, onClose)
  if (!open) return null
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className={`modal ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id={titleId}>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close dialog"><IconX size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  )
}

// ---------- Confirm dialog ----------
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  danger,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Modal open={open} title={title} onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>{confirmLabel}</button>
        </>
      }>
      <p>{message}</p>
    </Modal>
  )
}

// ---------- Spinner / empty / skeleton ----------
export function Spinner() {
  return <div className="center-load"><div className="spinner" /></div>
}

export function EmptyState({ message, icon, title }: { message: string; icon?: ReactNode; title?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon ?? <IconInbox size={26} />}</div>
      {title && <div className="empty-title">{title}</div>}
      <div>{message}</div>
    </div>
  )
}

export function Skeleton({ width = '100%', height = 13, style }: { width?: number | string; height?: number | string; style?: CSSProperties }) {
  return <div className="skeleton" style={{ width, height, ...style }} />
}

export function TableSkeleton({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="table-wrap">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="sk-row">
          {Array.from({ length: cols }).map((_, j) => (
            <Skeleton
              key={j}
              style={{ flex: 1, width: 'auto', height: 12, opacity: 1 - (i * 0.08) }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

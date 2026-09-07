/**
 * R2-08: the two pieces every irreversible or regulator-facing screen in this
 * console shares.
 *
 * `TypedConfirm` — a confirmation that cannot be dismissed by muscle memory.
 * Erasure execution, legal-hold release and a statutory filing are not "are
 * you sure?" actions: the first destroys a named human being's data, the
 * second un-blocks that destruction, and the third puts a signed report in
 * front of a regulator. Each one makes the operator read what it does and
 * type an exact phrase before the button enables.
 *
 * `RefusalNotice` — the refusal, verbatim. The erasure engine deliberately
 * refuses with a *named rule* ("no pre-erasure notice on record", "legal hold
 * LH-… in force"), and that sentence is the single most useful thing on the
 * screen: it says which precondition failed and therefore what to do next.
 * Replacing it with "Something went wrong" throws away the only actionable
 * content in the response. So a refusal is rendered inline, next to the
 * control that produced it, and it stays on screen until the operator acts —
 * a toast that vanishes in four seconds is not a record of a refusal.
 *
 * Nothing here ever renders a success state on a rejected call: every caller
 * sets either `result` or `refusal`, never both.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Modal } from './ui'
import { IconAlert } from './icons'

/**
 * A refusal from the API, shown verbatim.
 *
 * `detail` is the backend's own `detail` string, unedited. `title` frames it
 * ("The erasure engine refused this execution") but never replaces it.
 */
export function RefusalNotice({
  title,
  detail,
  onDismiss,
}: {
  title: string
  detail: string
  onDismiss?: () => void
}) {
  if (!detail) return null
  return (
    <div className="alert alert-error refusal" role="alert">
      <div className="refusal-head">
        <IconAlert size={16} />
        <b>{title}</b>
        {onDismiss && (
          <button type="button" className="btn btn-ghost btn-sm refusal-dismiss" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>
      {/* The API's own words. Do not summarise, translate or truncate this. */}
      <p className="refusal-detail">{detail}</p>
      <p className="refusal-foot">
        Reported verbatim by the API. Nothing was changed.
      </p>
    </div>
  )
}

/**
 * A confirmation gate for an action that cannot be undone.
 *
 * `consequences` is the list the operator has to read: what will happen, and
 * what will not be recoverable. `phrase` is what they must type — normally
 * the reference of the exact record being acted on, so confirming the wrong
 * row is impossible rather than merely discouraged.
 */
export function TypedConfirm({
  open,
  title,
  phrase,
  intro,
  consequences,
  confirmLabel,
  busy,
  onConfirm,
  onClose,
  children,
}: {
  open: boolean
  title: string
  phrase: string
  intro: ReactNode
  consequences: string[]
  confirmLabel: string
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
  children?: ReactNode
}) {
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (open) setTyped('')
  }, [open, phrase])

  const matches = typed.trim() === phrase

  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={!matches || busy}
            onClick={onConfirm}
            title={matches ? undefined : `Type ${phrase} to enable`}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="danger-intro">{intro}</div>
      <ul className="danger-list">
        {consequences.map((c) => <li key={c}>{c}</li>)}
      </ul>
      {children}
      <div className="form-group" style={{ marginTop: 14 }}>
        <label>
          Type <span className="mono danger-phrase">{phrase}</span> to confirm
        </label>
        {/*
          The placeholder is deliberately NOT the phrase. Putting the reference
          in the field makes an empty field look pre-filled — the operator then
          reads a disabled confirm button as a broken one, and the gate stops
          being a gate the moment the target is sitting in the box.
        */}
        <input
          className="input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Type the reference above"
          autoComplete="off"
          spellCheck={false}
          aria-label={`Type ${phrase} to confirm`}
        />
        {typed.trim() !== '' && !matches && (
          <div className="text-xs text-danger" style={{ marginTop: 5 }}>
            That is not this record&rsquo;s reference. The action stays disabled until it matches exactly.
          </div>
        )}
      </div>
    </Modal>
  )
}

/** A row of tab buttons over `.tabs` / `.tab` (already in styles.css). */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: Array<{ id: T; label: string; count?: number | null; disabled?: boolean; hint?: string }>
  active: T
  onChange: (id: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={active === t.id}
          className={`tab${active === t.id ? ' active' : ''}`}
          disabled={t.disabled}
          title={t.hint}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count != null && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

/**
 * The banner a screen shows when the role can read a module but not act in it.
 * Being explicit about *which* permission is missing is what stops "the button
 * is gone" being mistaken for "the feature is broken".
 */
export function ReadOnlyBanner({ permission, what }: { permission: string; what: string }) {
  return (
    <div className="alert alert-info mb">
      <b>Read-only.</b> {what} requires the <span className="mono">{permission}</span> permission,
      which your role does not have. Everything on this page is visible; nothing on it can be changed.
    </div>
  )
}

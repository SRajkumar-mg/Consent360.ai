import { useRef, useState, type FormEvent } from 'react'
import { getErrorMessage } from '../../api/client'
import { IconShield } from '../icons'
import type { PortalStrings } from '../../translations/portal'
import { portalApi } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
  onVerified: () => void
}

/**
 * Email-OTP identity verification (R3-05).
 *
 * Accessibility notes that are load-bearing rather than decorative, because
 * this is the gate a principal has to get through before they can exercise any
 * right at all:
 *
 *  - The error is `role="alert"` and focus moves to the code field after a
 *    failure, so a screen-reader user hears what went wrong and lands where
 *    the correction is made (WCAG 3.3.1, 3.3.3).
 *  - "We sent you a code" is announced via `role="status"`, so a sighted-only
 *    cue is not the only signal that the button did something.
 *  - The input is `autoComplete="one-time-code"` with `inputMode="numeric"`,
 *    so iOS/Android offer the code from the notification and show a numeric
 *    keypad - which for a one-handed or low-dexterity user is the difference
 *    between finishing and giving up.
 *  - Nothing here is behind a hover or a drag.
 */
export function PortalVerify({ t, token, onVerified }: Props) {
  const [sent, setSent] = useState(false)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const codeRef = useRef<HTMLInputElement>(null)

  const send = async () => {
    setError('')
    setBusy(true)
    try {
      await portalApi.verifyStart(token)
      setSent(true)
      setNotice(t.verify.sentBody)
      // Deliberately after the state flush so the field exists to focus.
      setTimeout(() => codeRef.current?.focus(), 0)
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await portalApi.verifyConfirm(token, code.trim())
      onVerified()
    } catch (err) {
      setError(getErrorMessage(err))
      setCode('')
      codeRef.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="pp-verify" aria-labelledby="pp-verify-title">
      <div className="pp-verify-mark" aria-hidden="true"><IconShield size={26} /></div>
      <h1 id="pp-verify-title" className="pp-h1">{t.verify.title}</h1>
      <p className="pp-lede">{t.verify.intro}</p>

      {!sent ? (
        <button className="btn btn-primary pp-btn-wide" onClick={send} disabled={busy}>
          {busy ? t.verify.sending : t.verify.sendCode}
        </button>
      ) : (
        <form onSubmit={confirm} noValidate>
          <p className="pp-verify-sent" role="status">
            <strong>{t.verify.sentTitle}</strong> {notice}
          </p>
          <div className="form-group pp-otp-group">
            <label htmlFor="pp-otp">{t.verify.codeLabel}</label>
            <input
              id="pp-otp"
              ref={codeRef}
              className="input pp-otp"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={8}
              aria-describedby="pp-otp-hint"
              aria-invalid={error ? true : undefined}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ''))}
            />
            <p id="pp-otp-hint" className="pp-hint">{t.verify.codeHint}</p>
          </div>
          <div className="pp-verify-actions">
            <button className="btn btn-primary pp-btn-wide" disabled={busy || code.length < 4}>
              {busy ? t.verify.confirming : t.verify.confirm}
            </button>
            <button type="button" className="btn btn-ghost" onClick={send} disabled={busy}>
              {t.verify.resend}
            </button>
          </div>
        </form>
      )}

      {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

      <details className="pp-why">
        <summary>{t.verify.whyTitle}</summary>
        <p>{t.verify.whyBody}</p>
      </details>
    </section>
  )
}

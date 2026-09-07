import { useId, useState } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { getErrorMessage } from '../../api/client'
import { formatDateTime } from '../ui'
import { IconX } from '../icons'
import { type PortalStrings } from '../../translations/portal'
import { portalApi } from './portalApi'
import type { NotificationRow } from '../../types'

interface Props {
  t: PortalStrings
  token: string
  notices: NotificationRow[]
  onAcknowledged: (updated: NotificationRow) => void
  onWithdraw: () => void
  onClose: () => void
}

/**
 * The s.5(2) legacy notice, as the principal reads it (R2-11 / gap A-08).
 *
 * s.5(2) covers personal data processed on consent given before the Act
 * commenced: the fiduciary owes her the s.5(1) notice "as soon as reasonably
 * practicable", and may continue processing "until the Data Principal
 * withdraws her consent". That last clause is the reason this is a dialog in
 * the portal and not only an email:
 *
 *  - "May continue until she withdraws" is a lawful permission only if
 *    withdrawal is genuinely available to her. So the notice carries a
 *    **withdrawal button that goes to the real withdraw path** - the same
 *    control the grant flow uses - rather than a sentence saying she may
 *    write to us.
 *  - Nothing here asks her to do anything to keep her consent in place, and
 *    the copy says so. A notice that treated silence as agreement would
 *    manufacture the consent the section exists because she never gave.
 *
 * "I have read this" acknowledges the *notice*, never the consent. It marks
 * the notification ACKNOWLEDGED, which is the only figure on the admin
 * delivery screen a principal herself put there - the rest is what the
 * transport reported. The button's helper text says exactly that, so the two
 * can never be confused by the person pressing it.
 *
 * The body is rendered from `Notification.body`, which is the itemised notice
 * composed per principal server-side (backend/app/services/legacy_notice.py::
 * compose_notice_body) - not re-derived here, so the screen and the delivery
 * record are the same words.
 */
export function PortalLegacyNoticeDialog({ t, token, notices, onAcknowledged, onWithdraw, onClose }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const titleId = useId()
  const descId = useId()
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  const acknowledge = async (n: NotificationRow) => {
    setBusyId(n.id)
    setError('')
    try {
      const res = await portalApi.acknowledgeNotification(token, n.id)
      onAcknowledged(res.data)
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="modal-overlay">
      <div
        ref={dialogRef}
        className="modal pp-notice-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
      >
        <div className="pp-notice-head">
          <h2 id={titleId}>{t.legacyNotice.dialogTitle}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label={t.shell.close}>
            <IconX size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="pp-notice-body">
          <div id={descId}>
            {notices.length === 0 && <p className="pp-empty">{t.legacyNotice.noneUnread}</p>}
            {notices.map((n) => (
              <article key={n.id} className="pp-legacy-notice">
                <p className="pp-notice-meta">
                  {t.legacyNotice.receivedOn}{' '}
                  <time dateTime={n.sent_at || n.created_at}>
                    {formatDateTime(n.sent_at || n.created_at)}
                  </time>
                </p>
                {/* `white-space: pre-line` keeps the server's paragraph breaks:
                    the notice is a legal document and its structure is part of
                    it, not decoration this screen may re-flow. */}
                <p className="pp-notice-text" style={{ whiteSpace: 'pre-line' }}>{n.body}</p>
                {n.acknowledged_at ? (
                  <p className="pp-chip pp-chip-ok">{t.legacyNotice.acknowledged}</p>
                ) : (
                  <>
                    <button
                      className="btn pp-act-btn"
                      onClick={() => acknowledge(n)}
                      disabled={busyId === n.id}
                    >
                      {busyId === n.id ? t.legacyNotice.acknowledging : t.legacyNotice.acknowledge}
                    </button>
                    <p className="pp-bulk-note">{t.legacyNotice.acknowledgeHelp}</p>
                  </>
                )}
              </article>
            ))}
          </div>

          <p className="alert alert-info pp-alert">{t.legacyNotice.continuesUntilWithdrawn}</p>
          {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

          <div className="pp-purpose-actions">
            {/* The withdrawal path, in the notice, one press away - not a
                reference to a page she has to find. */}
            <button className="btn btn-danger pp-act-btn" onClick={onWithdraw}>
              {t.legacyNotice.withdrawCta}
            </button>
            <button className="btn btn-ghost" onClick={onClose}>{t.shell.close}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

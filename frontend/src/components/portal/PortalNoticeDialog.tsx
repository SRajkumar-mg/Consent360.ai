import { useEffect, useState } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { Spinner } from '../ui'
import { IconX } from '../icons'
import { fill, type PortalStrings } from '../../translations/portal'
import { LANGUAGES } from '../../languages'
import { portalApi, type PublicNotice } from './portalApi'

interface Props {
  t: PortalStrings
  tenantCode: string
  purposeCode: string
  purposeName: string
  lang: string
  onClose: () => void
}

function languageName(code: string): string {
  const hit = LANGUAGES.find((l) => l.code === code)
  return hit ? `${hit.nameNative} (${hit.nameEn})` : code
}

/**
 * The published notice for one purpose, in the principal's chosen language
 * (DPDP s.5(3): the notice must be available in English or any Eighth Schedule
 * language, at the principal's option).
 *
 * `language_served` is shown whenever it differs from `language_requested`, so
 * a principal who asked for Odia and got English is TOLD that, rather than
 * being left to assume the organisation publishes only in English or that the
 * selector did nothing.
 */
export function PortalNoticeDialog({ t, tenantCode, purposeCode, purposeName, lang, onClose }: Props) {
  const [notice, setNotice] = useState<PublicNotice | null>(null)
  const [loading, setLoading] = useState(true)
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  useEffect(() => {
    let live = true
    setLoading(true)
    portalApi
      .notice(tenantCode, purposeCode, lang)
      .then((r) => { if (live) setNotice(r.data) })
      .catch(() => { if (live) setNotice(null) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [tenantCode, purposeCode, lang])

  const fellBack = notice && notice.language_served !== notice.language_requested

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal pp-notice-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pp-notice-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="pp-notice-head">
          <h2 id="pp-notice-title">{notice?.title || t.notice.title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label={t.shell.close}>
            <IconX size={16} aria-hidden="true" />
          </button>
        </div>

        {loading && <div className="center-load"><Spinner /></div>}

        {!loading && !notice && <p className="pp-muted">{t.notice.unavailable}</p>}

        {!loading && notice && (
          <div className="pp-notice-body">
            <p className="pp-notice-meta">
              {purposeName} · {t.notice.version} {notice.version_number} · {t.notice.servedIn}{' '}
              {languageName(notice.language_served)}
            </p>
            {fellBack && (
              <p className="alert alert-info pp-alert">
                {fill(t.notice.servedInFallback, { lang: languageName(notice.language_served) })}
              </p>
            )}

            {notice.body && <p className="pp-notice-text">{notice.body}</p>}

            {notice.data_items.length > 0 && (
              <>
                <h3 className="pp-notice-h">{t.notice.whatWeCollect}</h3>
                <ul className="pp-notice-items">
                  {notice.data_items.map((item, i) => {
                    const name = String(item.name ?? item.data_category ?? item.code ?? '')
                    const necessity = String(item.necessity ?? '')
                    const description = String(item.description ?? '')
                    return (
                      <li key={`${name}-${i}`}>
                        <strong>{name || '—'}</strong>
                        {necessity && <span className="pp-chip">{necessity}</span>}
                        {description && <span className="pp-notice-item-desc">{description}</span>}
                      </li>
                    )
                  })}
                </ul>
              </>
            )}

            {notice.services_enabled && (
              <>
                <h3 className="pp-notice-h">{t.notice.necessity}</h3>
                <p className="pp-notice-text">{notice.services_enabled}</p>
              </>
            )}

            {(notice.retention_period_days || notice.retention_note) && (
              <>
                <h3 className="pp-notice-h">{t.notice.retention}</h3>
                <p className="pp-notice-text">
                  {notice.retention_period_days
                    ? fill(t.consents.retentionDays, { n: notice.retention_period_days })
                    : ''}
                  {notice.retention_note ? ` ${notice.retention_note}` : ''}
                </p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

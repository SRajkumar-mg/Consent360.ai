import { useState } from 'react'
import { Badge } from '../ui'
import { getErrorMessage } from '../../api/client'
import { fill, type PortalStrings } from '../../translations/portal'
import { PortalNoticeDialog } from './PortalNoticeDialog'
import { gpcSignalDetected, localizePurpose, portalApi, type PortalPurpose } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
  lang: string
  tenantCode: string
  purposes: PortalPurpose[]
  onChanged: () => Promise<void>
}

const SCREEN_ID = 'principal-portal-consents'

// Every grant/withdraw below fires from a single click on a single button,
// with no confirmation step in between, so "step 1" is accurate today. This is
// a named constant - not `1` inlined at the call site - precisely so that the
// moment either flow grows a second step, it stops silently misreporting and
// has to be replaced with a real per-flow counter.
const SINGLE_CLICK_INTERACTION_STEP = 1

const ACTIVE = new Set(['GRANTED', 'PARTIAL'])

/**
 * The consent overview, one row per purpose.
 *
 * **Withdrawal is deliberately the exact mirror of granting** (DPDP s.6(4):
 * "the Data Principal shall have the right to withdraw her consent at any time,
 * with the ease of doing so being comparable to the ease with which such
 * consent was given"). Concretely, and these are the properties to preserve if
 * this file is ever edited:
 *
 *  - One click each way. No confirmation dialog on either, and in particular
 *    no confirmation on withdraw-only: a confirm step on the way out but not
 *    on the way in IS the asymmetry the section prohibits.
 *  - Equal visual weight. The grant and withdraw buttons are the same size,
 *    same shape, same position in the row; neither is a low-contrast "ghost"
 *    next to a filled primary.
 *  - The bulk pair likewise: "Give consent to all" and "Withdraw all consent"
 *    sit side by side and cost the same one click.
 *  - No reason is asked for on withdrawal, and none is asked for on grant.
 *
 * The protection against a misclick is that both directions are instantly
 * reversible with one click and the result is announced with the inverse
 * offered (WCAG 3.3.4's "reversible" mechanism) - not an extra gate on one
 * side only.
 */
export function PortalConsents({ t, token, lang, tenantCode, purposes, onChanged }: Props) {
  const [busyCode, setBusyCode] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [noticeFor, setNoticeFor] = useState<{ code: string; name: string } | null>(null)

  const context = (kind: 'grant' | 'withdraw', code: string) => ({
    language: lang,
    ui_control_id: `${kind}-${code}`,
    screen_id: SCREEN_ID,
    affirmative_action: 'CLICK' as const,
    interaction_step: SINGLE_CLICK_INTERACTION_STEP,
    gpc_signal: gpcSignalDetected(),
  })

  const act = async (kind: 'grant' | 'withdraw', p: PortalPurpose) => {
    setBusyCode(p.code)
    setError('')
    try {
      const call = kind === 'grant' ? portalApi.grant : portalApi.withdraw
      await call(token, p.code, context(kind, p.code))
      const name = localizePurpose(p, lang).name
      setStatus(`${name}: ${kind === 'grant' ? t.consents.granted : t.consents.withdrawn}`)
      await onChanged()
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusyCode(null)
    }
  }

  const bulk = async (kind: 'grant' | 'withdraw') => {
    const targets = purposes.filter((p) =>
      kind === 'grant' ? p.status !== 'GRANTED' : ACTIVE.has(p.status),
    )
    if (targets.length === 0) return
    setBulkBusy(true)
    setError('')
    let done = 0
    try {
      for (const p of targets) {
        const call = kind === 'grant' ? portalApi.grant : portalApi.withdraw
        await call(token, p.code, context(kind, p.code))
        done += 1
      }
      setStatus(fill(t.consents.bulkDone, { n: done }))
      await onChanged()
    } catch (e) {
      setError(getErrorMessage(e))
      await onChanged()
    } finally {
      setBulkBusy(false)
    }
  }

  const anyGranted = purposes.some((p) => ACTIVE.has(p.status))
  const anyNotGranted = purposes.some((p) => p.status !== 'GRANTED')

  return (
    <section aria-labelledby="pp-consents-title">
      <h1 id="pp-consents-title" className="pp-h1">{t.consents.title}</h1>
      <p className="pp-lede">{t.consents.intro}</p>

      {gpcSignalDetected() && <p className="alert alert-info pp-alert">{t.consents.gpcNotice}</p>}

      {purposes.length > 0 && (
        <div className="pp-bulk">
          <div className="pp-bulk-actions">
            <button
              className="btn btn-primary pp-bulk-btn"
              onClick={() => bulk('grant')}
              disabled={bulkBusy || !anyNotGranted}
            >
              {t.consents.grantAll}
            </button>
            <button
              className="btn btn-danger pp-bulk-btn"
              onClick={() => bulk('withdraw')}
              disabled={bulkBusy || !anyGranted}
            >
              {t.consents.withdrawAll}
            </button>
          </div>
          <p className="pp-bulk-note">{t.consents.withdrawAllBody}</p>
          <p className="pp-bulk-note pp-bulk-symmetry">{t.consents.symmetryNote}</p>
        </div>
      )}

      {/* One live region for the whole list: a screen reader hears the outcome
          of a click without focus being moved off the button the user is on. */}
      <p className="pp-sr-status" role="status">{status}</p>
      {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

      {purposes.length === 0 ? (
        <p className="pp-empty">{t.consents.empty}</p>
      ) : (
        <ul className="pp-purpose-list">
          {purposes.map((p) => {
            const l = localizePurpose(p, lang)
            const busy = busyCode === p.code || bulkBusy
            const granted = p.status === 'GRANTED'
            const partial = p.status === 'PARTIAL'
            return (
              <li key={p.code} className={`pp-purpose${granted ? ' is-granted' : ''}`}>
                <div className="pp-purpose-head">
                  <h2 className="pp-purpose-name">{l.name}</h2>
                  <Badge status={p.status} />
                </div>
                {l.description && <p className="pp-purpose-desc">{l.description}</p>}
                {l.consent_text && <p className="pp-purpose-quote">{l.consent_text}</p>}

                <dl className="pp-purpose-meta">
                  {p.legal_basis && (
                    <div><dt>{t.consents.legalBasis}</dt><dd>{p.legal_basis}</dd></div>
                  )}
                  {p.retention_period_days > 0 && (
                    <div>
                      <dt>{t.consents.retention}</dt>
                      <dd>{fill(t.consents.retentionDays, { n: p.retention_period_days })}</dd>
                    </div>
                  )}
                  {p.total_count > 0 && (
                    <div className="pp-purpose-count">
                      <dt className="pp-sr-only">{t.consents.partOfN}</dt>
                      <dd>{fill(t.consents.partOfN, { granted: p.granted_count, total: p.total_count })}</dd>
                    </div>
                  )}
                </dl>

                <div className="pp-purpose-actions">
                  {/* Both buttons carry the purpose name in their accessible
                      name: a screen-reader user tabbing a list of ten rows
                      otherwise hears "Withdraw consent" ten times with no way
                      to tell which row they are on (WCAG 2.4.6). */}
                  {(!granted || partial) && (
                    <button
                      className="btn btn-primary pp-act-btn"
                      onClick={() => act('grant', p)}
                      disabled={busy}
                      aria-label={`${t.consents.grant}: ${l.name}`}
                    >
                      {busyCode === p.code ? t.consents.granting : t.consents.grant}
                    </button>
                  )}
                  {(granted || partial) && (
                    <button
                      className="btn btn-danger pp-act-btn"
                      onClick={() => act('withdraw', p)}
                      disabled={busy}
                      aria-label={`${t.consents.withdraw}: ${l.name}`}
                    >
                      {busyCode === p.code ? t.consents.withdrawing : t.consents.withdraw}
                    </button>
                  )}
                  {!p.requires_consent && (
                    <span className="pp-purpose-nore">{t.consents.noConsentNeeded}</span>
                  )}
                  <button
                    className="btn btn-ghost pp-notice-link"
                    onClick={() => setNoticeFor({ code: p.code, name: l.name })}
                    aria-label={`${t.consents.viewNotice} ${l.name}`}
                  >
                    {t.consents.viewNotice}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {noticeFor && tenantCode && (
        <PortalNoticeDialog
          t={t}
          tenantCode={tenantCode}
          purposeCode={noticeFor.code}
          purposeName={noticeFor.name}
          lang={lang}
          onClose={() => setNoticeFor(null)}
        />
      )}
    </section>
  )
}

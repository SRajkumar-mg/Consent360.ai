import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { Spinner } from '../components/ui'
import { IconShield } from '../components/icons'
import { LANGUAGES } from '../languages'
import { fill, getPortalStrings, isRtl } from '../translations/portal'
import {
  portalApi,
  readTokenClaims,
  type PortalPurpose,
  type PublicPrivacyContact,
  type PublicRights,
} from '../components/portal/portalApi'
import { PortalConsents } from '../components/portal/PortalConsents'
import { PortalGrievances } from '../components/portal/PortalGrievances'
import { PortalHistory } from '../components/portal/PortalHistory'
import { PortalLegacyNoticeDialog } from '../components/portal/PortalLegacyNoticeDialog'
import { PortalReceipts } from '../components/portal/PortalReceipts'
import { PortalReConsentDialog } from '../components/portal/PortalReConsentDialog'
import { PortalRequests } from '../components/portal/PortalRequests'
import { PortalRights } from '../components/portal/PortalRights'
import { PortalVerify } from '../components/portal/PortalVerify'
import type { NotificationRow } from '../types'

/**
 * The data principal's self-service portal (R2-04).
 *
 * The whole screen is judged against one sentence: a principal must be able to
 * verify, review, export, withdraw, raise a grievance and see its status
 * WITHOUT staff help. Anything that would make someone phone support is a bug
 * here, not a rough edge - including a dead end that offers no next step, a
 * disabled control with no explanation, and an error message that names an
 * HTTP status.
 *
 * Three structural decisions follow from that:
 *
 *  1. DPO contact and rights information render on EVERY view, including the
 *     verification screen, from the unauthenticated `/public/{tenant}/...`
 *     endpoints. Someone who cannot get past verification still needs to know
 *     who to call, and that is precisely the person most likely to need it.
 *  2. The section is component state, not a route. The context token lives in
 *     the query string and is the only credential; pushing history entries per
 *     section would put it into the back-stack repeatedly and make "back" mean
 *     something different on every press. Back here means "return to the site
 *     I came from", which is what a principal expects.
 *  3. Expiry is shown as a countdown BEFORE it bites, and the expired state
 *     explains how to get a new link. The 15-minute context token is a
 *     security property, but "it just stopped working" is a support call.
 *
 * R2-11 adds a fourth: **two things can be waiting for her when she arrives,
 * and both are asks, not announcements.** A purpose she agreed to may have
 * changed materially (R1-09), in which case the decision engine has already
 * stopped relying on her consent and the only missing step is that someone
 * asks her again; or she may be owed the s.5(2) notice for data she consented
 * to before the Act. Neither can wait for her to go looking for it, so each
 * gets a banner that persists until it is dealt with, and one of them opens as
 * a dialog on arrival.
 *
 * The re-consent prompt wins that arrival slot when both apply. It is the one
 * where processing is currently blocked, so it is the one where waiting costs
 * her something; the legacy notice is permitted to be read at leisure, which
 * is exactly what its banner offers. Neither dialog is ever chained straight
 * into the other - stacking two modals on arrival turns a consent moment into
 * something to click past.
 */

type Section = 'consents' | 'history' | 'receipts' | 'grievances' | 'requests'

const SECTIONS: Section[] = ['consents', 'history', 'receipts', 'grievances', 'requests']

const PORTAL_LANG_KEY = 'consent360_portal_lang'

export function PublicPortalPage() {
  const [params] = useSearchParams()
  const token = params.get('token') || params.get('ctx') || ''
  const returnUrl = params.get('return_url') || ''

  const [lang, setLang] = useState(() => {
    try {
      return localStorage.getItem(PORTAL_LANG_KEY) || 'en'
    } catch {
      return 'en'
    }
  })
  const t = getPortalStrings(lang)
  const rtl = isRtl(lang)

  const [section, setSection] = useState<Section>('consents')
  const [purposes, setPurposes] = useState<PortalPurpose[]>([])
  const [customerName, setCustomerName] = useState('')
  const [loading, setLoading] = useState(true)
  const [fatal, setFatal] = useState('')
  const [verified, setVerified] = useState(false)
  const [contact, setContact] = useState<PublicPrivacyContact | null>(null)
  const [rights, setRights] = useState<PublicRights | null>(null)
  const [presetCategory, setPresetCategory] = useState<string | undefined>()
  const [notices, setNotices] = useState<NotificationRow[]>([])
  const [reConsentOpen, setReConsentOpen] = useState(false)
  const [legacyOpen, setLegacyOpen] = useState(false)
  // Whether the arrival prompt has already been offered this session. Without
  // it, every refresh of the overview after she presses a button would reopen
  // the dialog she just closed.
  const promptedRef = useRef(false)

  const claims = useMemo(() => readTokenClaims(token), [token])
  const tenantCode = claims.sourceApp

  // --- session expiry -------------------------------------------------------
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(id)
  }, [])
  const msLeft = claims.expiresAt ? claims.expiresAt - now : null
  const expired = msLeft !== null && msLeft <= 0
  const minutesLeft = msLeft !== null ? Math.max(0, Math.ceil(msLeft / 60000)) : null

  // --- data -----------------------------------------------------------------
  const loadOverview = useCallback(async () => {
    try {
      const res = await portalApi.overview(token)
      setPurposes(res.data.purposes)
      setCustomerName(res.data.customer.name)
      setVerified(true)
      setFatal('')
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 403) {
        setVerified(false)
      } else {
        setFatal(t.errors.expiredLink)
      }
    } finally {
      setLoading(false)
    }
    // `t` is only read for the message; re-running on a language change would
    // pointlessly re-fetch the overview on every dropdown pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (!token) {
      setFatal(t.errors.missingToken)
      setLoading(false)
      return
    }
    loadOverview()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, loadOverview])

  // Messages the organisation sent her, fetched here rather than inside the
  // History tab alone: the s.5(2) legacy notice has to be visible from the
  // moment she arrives, not only if she happens to open History.
  const loadNotices = useCallback(async () => {
    if (!token) return
    try {
      const res = await portalApi.notifications(token)
      setNotices(res.data)
    } catch {
      // A message list that will not load must never block the consent
      // screens; the banner simply does not appear.
    }
  }, [token])

  useEffect(() => {
    if (verified) loadNotices()
  }, [verified, loadNotices])

  /**
   * The purposes to prompt about.
   *
   * `granted_count > 0` is the second condition and it is not cosmetic. A
   * flagged consent she has already WITHDRAWN is still flagged - the backend
   * clears the flag only on a fresh grant or renewal, deliberately, so that
   * "consent cannot be assumed" holds - but she has answered, and re-asking
   * someone who just said no on every subsequent visit is nagging, which is
   * the asymmetry this whole screen is built to avoid. Nothing is being
   * processed under a withdrawn consent, so there is nothing left to ask.
   */
  const pendingReConsent = useMemo(
    () => purposes.filter((p) => p.re_consent_required && p.granted_count > 0),
    [purposes],
  )

  /** Legacy notices that reached her and that she has not confirmed reading.
   *  A PENDING row is not shown: it has not been sent, so presenting it as
   *  "a notice you were given" would be false. */
  const unreadLegacyNotices = useMemo(
    () => notices.filter(
      (n) => n.event_type === 'LEGACY_NOTICE'
        && !n.acknowledged_at
        && (n.status === 'DELIVERED' || n.status === 'SENT'),
    ),
    [notices],
  )

  useEffect(() => {
    if (!verified || loading || promptedRef.current) return
    if (pendingReConsent.length > 0) {
      promptedRef.current = true
      setReConsentOpen(true)
    } else if (unreadLegacyNotices.length > 0) {
      promptedRef.current = true
      setLegacyOpen(true)
    }
  }, [verified, loading, pendingReConsent, unreadLegacyNotices])

  // Rights and DPO contact are PUBLIC and unauthenticated on purpose: they must
  // render before verification, and must survive an expired token.
  useEffect(() => {
    if (!tenantCode) return
    let live = true
    portalApi.privacyContact(tenantCode).then((r) => { if (live) setContact(r.data) }).catch(() => undefined)
    portalApi.rights(tenantCode).then((r) => { if (live) setRights(r.data) }).catch(() => undefined)
    return () => { live = false }
  }, [tenantCode])

  const changeLang = (code: string) => {
    setLang(code)
    try {
      localStorage.setItem(PORTAL_LANG_KEY, code)
    } catch {
      /* private browsing: the choice just does not persist between visits */
    }
  }

  // --- tab keyboard support (WAI-ARIA authoring practices: roving tabindex) --
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const onTabKeyDown = (e: KeyboardEvent, index: number) => {
    const keys: Record<string, number> = {
      ArrowRight: rtl ? index - 1 : index + 1,
      ArrowLeft: rtl ? index + 1 : index - 1,
      Home: 0,
      End: SECTIONS.length - 1,
    }
    if (!(e.key in keys)) return
    e.preventDefault()
    const next = (keys[e.key] + SECTIONS.length) % SECTIONS.length
    const target = SECTIONS[next]
    setSection(target)
    tabRefs.current[target]?.focus()
  }

  const raiseAs = (category: string) => {
    setPresetCategory(category)
    setSection('grievances')
  }

  const rightsPanel = <PortalRights t={t} contact={contact} rights={rights} />

  const languagePicker = (
    <div className="pp-lang">
      <label className="pp-lang-label" htmlFor="pp-lang">{t.shell.languageLabel}</label>
      <select
        id="pp-lang"
        className="input pp-lang-select"
        value={lang}
        onChange={(e) => changeLang(e.target.value)}
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code} lang={l.code}>
            {l.nameNative} — {l.nameEn}
          </option>
        ))}
      </select>
    </div>
  )

  const header = (
    <header className="pp-header">
      <div className="pp-header-in">
        <div className="pp-brand">
          <span className="pp-brand-mark" aria-hidden="true"><IconShield size={20} /></span>
          <span className="pp-brand-text">
            <span className="pp-brand-eyebrow">{t.shell.brand}</span>
            <span className="pp-brand-name">{rights?.tenant_name || t.shell.title}</span>
          </span>
        </div>
        <div className="pp-header-right">
          {verified && customerName && (
            <p className="pp-whoami">{t.shell.signedInAs} <strong>{customerName}</strong></p>
          )}
          {languagePicker}
        </div>
      </div>
      {!expired && minutesLeft !== null && minutesLeft <= 5 && (
        <p className="pp-expiry" role="status">
          {minutesLeft <= 1
            ? t.shell.sessionExpiringSoon
            : fill(t.shell.sessionExpiresIn, { n: minutesLeft })}
        </p>
      )}
    </header>
  )

  const shell = (body: ReactNode, nav?: ReactNode) => (
    <div className={`pp-root${rtl ? ' pp-rtl' : ''}`} dir={rtl ? 'rtl' : 'ltr'} lang={lang}>
      <a className="pp-skip" href="#pp-main">{t.shell.skipToContent}</a>
      {header}
      {nav}
      <div className="pp-body">
        <main id="pp-main" className="pp-main" tabIndex={-1}>
          {body}
        </main>
        {rightsPanel}
      </div>
      {returnUrl && (
        <footer className="pp-footer">
          <a className="pp-return" href={returnUrl}>← {t.shell.returnToSite}</a>
        </footer>
      )}
    </div>
  )

  if (loading) {
    return shell(<div className="center-load"><Spinner /></div>)
  }

  if (expired) {
    return shell(
      <section aria-labelledby="pp-expired-title">
        <h1 id="pp-expired-title" className="pp-h1">{t.shell.sessionExpired}</h1>
        <p className="pp-lede">{t.shell.sessionExpiredHelp}</p>
        {returnUrl && (
          <a className="btn btn-primary pp-btn-wide" href={returnUrl}>{t.shell.returnToSite}</a>
        )}
      </section>,
    )
  }

  if (fatal) {
    return shell(
      <section aria-labelledby="pp-fatal-title">
        <h1 id="pp-fatal-title" className="pp-h1">{t.shell.sessionExpired}</h1>
        <p className="alert alert-error pp-alert" role="alert">{fatal}</p>
        {returnUrl && (
          <a className="btn btn-primary pp-btn-wide" href={returnUrl}>{t.shell.returnToSite}</a>
        )}
      </section>,
    )
  }

  if (!verified) {
    return shell(
      <PortalVerify
        t={t}
        token={token}
        onVerified={() => {
          setLoading(true)
          loadOverview()
        }}
      />,
    )
  }

  const nav = (
    <nav className="pp-nav" aria-label={t.nav.label}>
      <div className="pp-nav-in" role="tablist" aria-label={t.nav.label}>
        {SECTIONS.map((s, i) => (
          <button
            key={s}
            id={`pp-tab-${s}`}
            ref={(el) => { tabRefs.current[s] = el }}
            role="tab"
            type="button"
            aria-selected={section === s}
            aria-controls="pp-panel"
            tabIndex={section === s ? 0 : -1}
            className={`pp-tab${section === s ? ' is-active' : ''}`}
            onClick={() => setSection(s)}
            onKeyDown={(e) => onTabKeyDown(e, i)}
          >
            {t.nav[s]}
          </button>
        ))}
      </div>
    </nav>
  )

  const panel = (() => {
    switch (section) {
      case 'history':
        return <PortalHistory t={t} token={token} />
      case 'receipts':
        return <PortalReceipts t={t} token={token} />
      case 'grievances':
        return (
          <PortalGrievances
            t={t}
            token={token}
            lang={lang}
            purposes={purposes}
            presetCategory={presetCategory}
            onPresetConsumed={() => setPresetCategory(undefined)}
          />
        )
      case 'requests':
        return <PortalRequests t={t} onRaiseAs={raiseAs} onGoToExport={() => setSection('receipts')} />
      case 'consents':
      default:
        return (
          <PortalConsents
            t={t}
            token={token}
            lang={lang}
            tenantCode={tenantCode}
            purposes={purposes}
            onChanged={loadOverview}
          />
        )
    }
  })()

  /**
   * The two standing asks.
   *
   * Both persist while their condition holds - closing the dialog does not
   * make the ask go away, because the ask has not been answered. Each carries
   * its own button back into the dialog, so "Decide later" is a real option
   * rather than the last time she will hear about it.
   */
  const banners = (
    <>
      {pendingReConsent.length > 0 && (
        <div className="alert alert-warning pp-alert pp-standing-ask" role="status">
          <b>{t.reConsent.bannerTitle}</b>{' '}
          {fill(t.reConsent.bannerBody, { n: pendingReConsent.length })}
          <button className="btn btn-primary pp-act-btn" onClick={() => setReConsentOpen(true)}>
            {t.reConsent.review}
          </button>
        </div>
      )}
      {unreadLegacyNotices.length > 0 && (
        <div className="alert alert-info pp-alert pp-standing-ask" role="status">
          <b>{t.legacyNotice.bannerTitle}</b> {t.legacyNotice.bannerBody}
          <button className="btn pp-act-btn" onClick={() => setLegacyOpen(true)}>
            {t.legacyNotice.read}
          </button>
        </div>
      )}
    </>
  )

  return shell(
    <>
      {banners}
      <div id="pp-panel" role="tabpanel" aria-labelledby={`pp-tab-${section}`}>
        {panel}
      </div>
      {reConsentOpen && (
        <PortalReConsentDialog
          t={t}
          token={token}
          lang={lang}
          purposes={pendingReConsent}
          onChanged={loadOverview}
          onClose={() => setReConsentOpen(false)}
        />
      )}
      {legacyOpen && (
        <PortalLegacyNoticeDialog
          t={t}
          token={token}
          notices={unreadLegacyNotices}
          onAcknowledged={(updated) =>
            setNotices((prev) => prev.map((n) => (n.id === updated.id ? updated : n)))
          }
          onWithdraw={() => {
            // Straight to the control that actually withdraws, not to a page
            // about withdrawing. s.5(2) lets processing continue only until
            // she withdraws, so the path there has to be one press from the
            // notice that told her so.
            setLegacyOpen(false)
            setSection('consents')
          }}
          onClose={() => setLegacyOpen(false)}
        />
      )}
    </>,
    nav,
  )
}

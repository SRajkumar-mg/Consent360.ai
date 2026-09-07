import { useId, useState } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { getErrorMessage } from '../../api/client'
import { fill, type PortalStrings } from '../../translations/portal'
import { gpcSignalDetected, localizePurpose, portalApi, type PortalPurpose } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
  lang: string
  purposes: PortalPurpose[]
  onChanged: () => Promise<void>
  onClose: () => void
}

const SCREEN_ID = 'principal-portal-reconsent'

// One press each way, exactly as in PortalConsents. Named for the same reason
// it is named there: if either flow ever grows a second step, this constant
// has to be replaced rather than silently misreporting the interaction.
const SINGLE_CLICK_INTERACTION_STEP = 1

/**
 * Plain-language labels for the field names the classifier writes into its own
 * reason string (`app/services/material_change.py::FIELD_RULES`).
 *
 * The rest of each clause is already written for a person; only the leading
 * token is a database column name, and "retention_period_days:" in front of an
 * otherwise readable sentence is exactly the jargon a s.5(1) notice is not
 * allowed to contain.
 *
 * This ONLY renames. It never drops a clause, never reorders them and never
 * drops one the map does not know about — an unmapped field falls through with
 * its raw name, visible and ugly, rather than disappearing. A prompt that
 * silently omitted part of the basis for asking would be worse than one that
 * shows a column name.
 */
const FIELD_LABELS: Record<string, string> = {
  data_category_ids: 'What personal data we collect',
  processing_activity_ids: 'What we do with it',
  data_items: 'The itemised list of what we collect',
  retention_period_days: 'How long we keep it',
  legal_basis: 'The legal basis we rely on',
  requires_consent: 'Whether your consent is needed',
  child_restricted: 'Whether this is restricted for children',
  description: 'The description of this purpose',
  services_enabled: 'What this enables for you',
  consent_text: 'The wording you agreed to',
  name: 'The name of this purpose',
  translations: 'Translations',
  checklist: 'The review checklist',
}

/**
 * Splits the classifier's `field: WHY; field: WHY` string back into clauses so
 * each can be labelled.
 *
 * The split only fires on a semicolon that is followed by another
 * `snake_case:` field name. A plain `split('; ')` broke a clause in half the
 * first time a reason contained one internally — "…consent is to a specified
 * purpose; s.5 notice must itemise the personal data" is one statutory citation
 * and cutting it produced two half-sentences on a consent screen.
 *
 * Kept dumb otherwise: a clause whose prefix is not a bare snake_case token is
 * returned whole and unlabelled rather than guessed at.
 */
function basisClauses(basis: string): Array<{ label: string; text: string }> {
  if (!basis.trim()) return []
  return basis.split(/;\s+(?=[a-z_]+:\s)/).map((clause) => {
    const at = clause.indexOf(': ')
    if (at <= 0) return { label: '', text: clause }
    const field = clause.slice(0, at)
    if (!/^[a-z_]+$/.test(field)) return { label: '', text: clause }
    return { label: FIELD_LABELS[field] ?? field, text: clause.slice(at + 2) }
  })
}

/**
 * The re-consent prompt (R1-09 / R2-11, DPDP s.6(1)).
 *
 * A consent is agreement to a *specified* purpose. When the specification
 * changes materially the old agreement stops covering the new processing, the
 * decision engine refuses to rely on it, and the only thing still missing is
 * that somebody asks the principal. This dialog is that ask, and it appears on
 * her next visit rather than waiting for her to go looking.
 *
 * It is a consent moment, so it is held to the same bar as the cookie banner
 * elsewhere in this repo, and these properties must survive any edit:
 *
 *  - **Nothing is pre-selected.** There is no checkbox and no default at all;
 *    the two outcomes are two buttons, and neither is pressed for her.
 *  - **Refusing is exactly as easy as agreeing.** Same size, same shape, side
 *    by side, one press each, no confirmation on the refuse side and none on
 *    the agree side, and no reason asked for either way (s.6(4)'s "ease ...
 *    comparable" applies to a re-ask as much as to the original).
 *  - **The substantive notice is in the accessible description.** The dialog's
 *    `aria-describedby` points at the block that says what changed and why it
 *    is material, not at a summary. A screen-reader user is told what she is
 *    agreeing to before she reaches the buttons; "something changed" would not
 *    be an informed consent moment.
 *  - **Closing is not agreement, and says so.** Escape and "Decide later" both
 *    work (WCAG 2.1.2 - a consent dialog that cannot be dismissed is a
 *    keyboard trap), and the copy states the consequence: processing stays
 *    stopped and she will be asked again. Dismissal must never be read as a
 *    choice, in either direction.
 */
export function PortalReConsentDialog({ t, token, lang, purposes, onChanged, onClose }: Props) {
  const [busyCode, setBusyCode] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const titleId = useId()
  const descId = useId()
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  const context = (kind: 'grant' | 'withdraw', code: string) => ({
    language: lang,
    ui_control_id: `reconsent-${kind === 'grant' ? 'agree' : 'refuse'}-${code}`,
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
      setStatus(fill(kind === 'grant' ? t.reConsent.agreed : t.reConsent.refused, { name }))
      await onChanged()
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusyCode(null)
    }
  }

  return (
    <div className="modal-overlay">
      {/* No click-to-dismiss on the overlay. An accidental click outside must
          not close a consent moment as if a decision had been made; the
          explicit "Decide later" button and Escape are the ways out, and both
          say what they mean. */}
      <div
        ref={dialogRef}
        className="modal pp-notice-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
      >
        <div className="pp-notice-head">
          <h2 id={titleId}>{t.reConsent.dialogTitle}</h2>
        </div>

        <div className="pp-notice-body">
          <div id={descId}>
            <p className="pp-lede">{t.reConsent.intro}</p>

            {purposes.length === 0 ? (
              <p className="pp-empty">{t.reConsent.allDone}</p>
            ) : (
              <ul className="pp-purpose-list">
                {purposes.map((p) => {
                  const l = localizePurpose(p, lang)
                  const busy = busyCode === p.code
                  return (
                    <li key={p.code} className="pp-purpose pp-purpose-reconsent">
                      <div className="pp-purpose-head">
                        <h3 className="pp-purpose-name">{l.name}</h3>
                        <span className="pp-chip pp-chip-warn">{t.reConsent.blockedNote}</span>
                      </div>
                      {l.description && <p className="pp-purpose-desc">{l.description}</p>}
                      {l.consent_text && <p className="pp-purpose-quote">{l.consent_text}</p>}

                      <h4 className="pp-notice-h">{t.reConsent.whatChanged}</h4>
                      {/* The classifier's own words, clause for clause, with
                          only the leading column name renamed. This IS the
                          notice; paraphrasing it into "we updated our terms"
                          is what this screen exists not to do, and an auditor
                          can line each clause up against the same change in
                          the published change log. */}
                      <ul className="pp-reconsent-basis">
                        {basisClauses(p.re_consent_reason).map((c, i) => (
                          <li key={`${p.code}-basis-${i}`}>
                            {c.label && <strong>{c.label}: </strong>}
                            {c.text}
                          </li>
                        ))}
                      </ul>
                      {p.purpose_version_number != null && (
                        <p className="pp-purpose-meta-line">
                          {fill(t.reConsent.versionLine, { n: p.purpose_version_number })}
                        </p>
                      )}

                      <div className="pp-purpose-actions">
                        <button
                          className="btn btn-primary pp-act-btn"
                          onClick={() => act('grant', p)}
                          disabled={busy}
                          aria-label={`${t.reConsent.agree}: ${l.name}`}
                        >
                          {busy ? t.reConsent.agreeing : t.reConsent.agree}
                        </button>
                        <button
                          className="btn btn-danger pp-act-btn"
                          onClick={() => act('withdraw', p)}
                          disabled={busy}
                          aria-label={`${t.reConsent.refuse}: ${l.name}`}
                        >
                          {busy ? t.reConsent.refusing : t.reConsent.refuse}
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          <p className="pp-bulk-note pp-bulk-symmetry">{t.reConsent.equalNote}</p>
          <p className="pp-sr-status" role="status">{status}</p>
          {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

          <div className="pp-purpose-actions pp-reconsent-footer">
            <button className="btn btn-ghost" onClick={onClose}>{t.reConsent.later}</button>
          </div>
          <p className="pp-bulk-note">{t.reConsent.laterHelp}</p>
        </div>
      </div>
    </div>
  )
}

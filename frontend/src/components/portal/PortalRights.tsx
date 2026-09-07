import { fill, type PortalStrings } from '../../translations/portal'
import type { PublicPrivacyContact, PublicRights } from './portalApi'

interface Props {
  t: PortalStrings
  contact: PublicPrivacyContact | null
  rights: PublicRights | null
}

/**
 * DPO contact and rights information, rendered on EVERY view of the portal -
 * including the verification screen, before the principal has proved anything.
 *
 * This is not a footer nicety. Publishing the contact of the person who
 * answers questions on behalf of the Data Fiduciary (DPDP s.5(1)(iii), s.13(3))
 * and the means of exercising rights and of complaining to the Board
 * (s.13(1)-(2)) is a statutory duty, and the duty is defeated if a principal
 * has to authenticate first or navigate to find it. Both come from the
 * unauthenticated `/public/{tenant_code}/...` endpoints for exactly that
 * reason.
 *
 * When the tenant has published nothing, this says so plainly rather than
 * rendering empty - an organisation with no published grievance contact is a
 * compliance failure the principal is entitled to see and act on, not a blank
 * space that reads as a page-loading glitch.
 */
export function PortalRights({ t, contact, rights }: Props) {
  const hasContact = Boolean(contact && (contact.dpo_name || contact.dpo_email || contact.dpo_phone))
  const links: Array<{ href: string; label: string }> = []
  if (rights?.rights_url) links.push({ href: rights.rights_url, label: t.rights.linkRights })
  if (rights?.withdraw_url) links.push({ href: rights.withdraw_url, label: t.rights.linkWithdraw })
  if (rights?.grievance_url) links.push({ href: rights.grievance_url, label: t.rights.linkGrievance })

  return (
    <aside className="pp-rights" aria-labelledby="pp-rights-title">
      <h2 id="pp-rights-title" className="pp-rights-title">{t.rights.panelTitle}</h2>
      <p className="pp-rights-intro">{t.rights.panelIntro}</p>

      <section className="pp-rights-block" aria-labelledby="pp-rights-contact">
        <h3 id="pp-rights-contact" className="pp-rights-h">{t.rights.contactTitle}</h3>
        {hasContact && contact ? (
          <>
            {contact.dpo_name && <p className="pp-rights-name">{contact.dpo_name}</p>}
            <p className="pp-rights-role">{t.rights.dpoRole}</p>
            <dl className="pp-rights-dl">
              {contact.dpo_email && (
                <div>
                  <dt>{t.rights.email}</dt>
                  <dd><a href={`mailto:${contact.dpo_email}`}>{contact.dpo_email}</a></dd>
                </div>
              )}
              {contact.dpo_phone && (
                <div>
                  <dt>{t.rights.phone}</dt>
                  <dd><a href={`tel:${contact.dpo_phone.replace(/\s/g, '')}`}>{contact.dpo_phone}</a></dd>
                </div>
              )}
            </dl>
          </>
        ) : (
          <p className="pp-rights-missing">{t.rights.contactUnavailable}</p>
        )}
      </section>

      <section className="pp-rights-block" aria-labelledby="pp-rights-list">
        <h3 id="pp-rights-list" className="pp-rights-h">{t.rights.rightsTitle}</h3>
        <ul className="pp-rights-ul">
          <li>{t.rights.rWithdraw}</li>
          <li>{t.rights.rAccess}</li>
          <li>{t.rights.rCorrection}</li>
          <li>{t.rights.rErasure}</li>
          <li>{t.rights.rNominate}</li>
          <li>{t.rights.rGrievance}</li>
        </ul>
        {rights && rights.grievance_response_days > 0 && (
          <p className="pp-rights-days">
            {fill(t.rights.respondWithin, { n: rights.grievance_response_days })}
          </p>
        )}
      </section>

      {links.length > 0 && (
        <section className="pp-rights-block" aria-labelledby="pp-rights-links">
          <h3 id="pp-rights-links" className="pp-rights-h">{t.rights.linksTitle}</h3>
          <ul className="pp-rights-links">
            {links.map((l) => (
              <li key={l.href}>
                <a href={l.href} target="_blank" rel="noreferrer noopener">{l.label}</a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="pp-rights-block pp-rights-board" aria-labelledby="pp-rights-board">
        <h3 id="pp-rights-board" className="pp-rights-h">{t.rights.boardTitle}</h3>
        <p>{t.rights.boardBody}</p>
        {rights?.board_complaint_url && (
          <a className="btn btn-sm pp-board-btn" href={rights.board_complaint_url} target="_blank" rel="noreferrer noopener">
            {t.rights.boardLink}
          </a>
        )}
      </section>
    </aside>
  )
}

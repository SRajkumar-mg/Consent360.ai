import { IconAlert } from '../icons'
import type { PortalStrings } from '../../translations/portal'

interface Props {
  t: PortalStrings
  /** Sends the principal to the grievance form with this category selected. */
  onRaiseAs: (category: string) => void
  onGoToExport: () => void
}

/**
 * Access / correction / erasure requests and nominee capture.
 *
 * **These forms are not built.** The rights-request module that backs them
 * (`/rights/me/requests`, `/rights/me/nominations`) exists in the backend tree
 * but its router is not mounted, so there is no endpoint to submit to. Rather
 * than ship four forms that POST into a 404, this section states plainly that
 * the feature has not shipped, describes what each form will do so a reader
 * can tell whether it is what they need, and hands them the route that works
 * today.
 *
 * The deliberate design choice here is that nothing on this screen LOOKS
 * operable. There is no disabled "Submit" button, no greyed-out field, no
 * "coming soon" badge on a form that could be mistaken for one that will work
 * after a refresh. A principal who cannot tell the difference between "this
 * form is broken" and "this form does not exist yet" will sit and wait for an
 * answer that is never coming - which is exactly the "needs staff help"
 * failure this whole page is meant to remove.
 *
 * When the rights module is mounted, replace the four `pp-req-card` blocks
 * with real forms and delete the `pp-req-pending` banner and the "In the
 * meantime" block. Nothing else on this page needs to change.
 */
export function PortalRequests({ t, onRaiseAs, onGoToExport }: Props) {
  const cards = [
    { key: 'ACCESS_REQUEST', title: t.requests.accessTitle, body: t.requests.accessBody },
    { key: 'CORRECTION_REQUEST', title: t.requests.correctionTitle, body: t.requests.correctionBody },
    { key: 'ERASURE_REQUEST', title: t.requests.erasureTitle, body: t.requests.erasureBody },
    { key: 'NOMINATION', title: t.requests.nomineeTitle, body: t.requests.nomineeBody },
  ]

  return (
    <section aria-labelledby="pp-req-title">
      <h1 id="pp-req-title" className="pp-h1">{t.requests.title}</h1>
      <p className="pp-lede">{t.requests.intro}</p>

      <div className="pp-req-pending" role="note">
        <p className="pp-req-pending-title">
          <IconAlert size={16} aria-hidden="true" />
          {t.requests.pendingTitle}
        </p>
        <p>{t.requests.pendingBody}</p>
      </div>

      <ul className="pp-req-cards">
        {cards.map((c) => (
          <li key={c.key} className="pp-req-card">
            <h2 className="pp-req-card-title">{c.title}</h2>
            <p className="pp-req-card-body">{c.body}</p>
            <p className="pp-req-card-slot" aria-hidden="true">{t.requests.pendingTitle}</p>
          </li>
        ))}
      </ul>

      <div className="pp-req-meanwhile card">
        <h2 className="pp-h2 pp-h2-first">{t.requests.meanwhileTitle}</h2>
        <p className="pp-lede">{t.requests.meanwhileBody}</p>
        <div className="pp-req-meanwhile-actions">
          {cards.map((c) => (
            <button key={c.key} className="btn" onClick={() => onRaiseAs(c.key)}>
              {t.requests.meanwhileAction}: {c.title}
            </button>
          ))}
        </div>
        <p className="pp-hint pp-hint-note">
          <button className="pp-linkbtn" onClick={onGoToExport}>{t.requests.exportAction}</button>
        </p>
      </div>
    </section>
  )
}

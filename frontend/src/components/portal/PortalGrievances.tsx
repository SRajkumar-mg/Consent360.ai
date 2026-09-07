import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Badge, formatDate, formatDateTime, Spinner } from '../ui'
import { getErrorMessage } from '../../api/client'
import { fill, type PortalStrings } from '../../translations/portal'
import type { Grievance } from '../../types'
import { localizePurpose, portalApi, type GrievanceAcknowledgement, type PortalPurpose } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
  lang: string
  purposes: PortalPurpose[]
  /** Preselected category, set when the principal arrives here from the
   *  rights-requests section (which cannot submit its own forms yet). */
  presetCategory?: string
  onPresetConsumed?: () => void
}

/** The categories `GrievanceSelfIn.category` accepts, in the order a principal
 *  is most likely to want them - the three rights requests first, because
 *  until the rights module ships this form is the only route for them. */
const CATEGORIES = [
  'ACCESS_REQUEST',
  'CORRECTION_REQUEST',
  'ERASURE_REQUEST',
  'NOMINATION',
  'CONSENT_NOT_HONOURED',
  'UNAUTHORISED_PROCESSING',
  'EXCESSIVE_COLLECTION',
  'DATA_ACCURACY',
  'SECURITY_INCIDENT',
  'NOTICE_UNCLEAR',
  'OTHER',
] as const

type Category = (typeof CATEGORIES)[number]

function toneFor(status: string): string {
  if (status === 'RESOLVED' || status === 'CLOSED') return 'ACTIVE'
  if (status === 'ESCALATED') return 'DENIED'
  return 'PENDING'
}

/**
 * Submit a grievance, get a reference number, and follow it to a resolution -
 * the DPDP s.13 duty made operable by the person it exists for.
 *
 * The acknowledgement is not a toast. It is a persistent panel carrying the
 * reference number, the published response period and the date an answer is
 * owed by, with the Board escalation route named on it - because that panel is
 * the only artefact the principal has if the organisation later does nothing,
 * and a message that disappears after four seconds is not an artefact.
 */
export function PortalGrievances({ t, token, lang, purposes, presetCategory, onPresetConsumed }: Props) {
  const [list, setList] = useState<Grievance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [ack, setAck] = useState<GrievanceAcknowledgement | null>(null)
  const [openRef, setOpenRef] = useState<string | null>(null)

  const [category, setCategory] = useState<Category>('CONSENT_NOT_HONOURED')
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [purposeCode, setPurposeCode] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [feedbackFor, setFeedbackFor] = useState<string | null>(null)
  const [rating, setRating] = useState(5)
  const [comment, setComment] = useState('')
  const [feedbackBusy, setFeedbackBusy] = useState(false)

  const ackRef = useRef<HTMLDivElement>(null)
  const categoryRef = useRef<HTMLSelectElement>(null)

  const load = async () => {
    try {
      const res = await portalApi.grievances(token)
      setList(res.data)
      setError('')
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let live = true
    portalApi
      .grievances(token)
      .then((r) => { if (live) { setList(r.data); setError('') } })
      .catch((e) => { if (live) setError(getErrorMessage(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [token])

  // Arriving from "Raise it as a grievance" on the rights-requests section:
  // preselect the category and put focus on the form, so the principal lands
  // where they meant to go instead of at the top of another page.
  useEffect(() => {
    if (!presetCategory) return
    if ((CATEGORIES as readonly string[]).includes(presetCategory)) {
      setCategory(presetCategory as Category)
    }
    categoryRef.current?.focus()
    onPresetConsumed?.()
  }, [presetCategory, onPresetConsumed])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!description.trim()) return
    setSubmitting(true)
    setError('')
    try {
      const res = await portalApi.submitGrievance(token, {
        category,
        subject: subject.trim(),
        description: description.trim(),
        purpose_code: purposeCode || null,
      })
      setAck(res.data)
      setSubject('')
      setDescription('')
      setPurposeCode('')
      await load()
      // Move focus to the acknowledgement: the reference number is the one
      // thing on this page the principal must not miss.
      setTimeout(() => ackRef.current?.focus(), 0)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  const sendFeedback = async (ref: string) => {
    setFeedbackBusy(true)
    try {
      const res = await portalApi.grievanceFeedback(token, ref, rating, comment.trim())
      setList((prev) => prev.map((g) => (g.reference_no === ref ? res.data : g)))
      setFeedbackFor(null)
      setComment('')
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setFeedbackBusy(false)
    }
  }

  return (
    <section aria-labelledby="pp-griev-title">
      <h1 id="pp-griev-title" className="pp-h1">{t.grievance.title}</h1>
      <p className="pp-lede">{t.grievance.intro}</p>

      {ack && (
        <div className="pp-ack" ref={ackRef} tabIndex={-1} role="status" aria-labelledby="pp-ack-title">
          <h2 id="pp-ack-title" className="pp-ack-title">{t.grievance.ackTitle}</h2>
          <p className="pp-ack-reflabel">{t.grievance.ackReference}</p>
          <p className="pp-ack-ref"><code>{ack.reference_no}</code></p>
          <p className="pp-ack-keep">{t.grievance.ackKeepSafe}</p>
          <dl className="pp-kv">
            <div>
              <dt>{t.grievance.ackDueBy}</dt>
              <dd>{formatDate(ack.due_at)}</dd>
            </div>
            <div>
              <dt>{fill(t.grievance.ackResponseDays, { n: ack.response_days })}</dt>
              <dd>{ack.grievance_officer ? `${t.grievance.ackOfficer}: ${ack.grievance_officer}` : ''}</dd>
            </div>
          </dl>
          {ack.acknowledgement_message && <p className="pp-ack-msg">{ack.acknowledgement_message}</p>}
          <p className="pp-ack-board">
            {t.grievance.ackBoard}
            {ack.board_complaint_url && (
              <>
                {' '}
                <a href={ack.board_complaint_url} target="_blank" rel="noreferrer noopener">
                  {t.rights.boardLink}
                </a>
              </>
            )}
          </p>
        </div>
      )}

      {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

      <form className="pp-form card" onSubmit={submit} aria-labelledby="pp-griev-form-title">
        <h2 id="pp-griev-form-title" className="pp-h2 pp-h2-first">{t.grievance.formTitle}</h2>

        <div className="form-group">
          <label htmlFor="pp-g-cat">{t.grievance.category}</label>
          <select
            id="pp-g-cat"
            ref={categoryRef}
            className="input"
            value={category}
            aria-describedby="pp-g-cat-hint"
            onChange={(e) => setCategory(e.target.value as Category)}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{t.grievance.cat[c]}</option>
            ))}
          </select>
          <p id="pp-g-cat-hint" className="pp-hint">{t.grievance.categoryHint}</p>
        </div>

        <div className="form-group">
          <label htmlFor="pp-g-subject">{t.grievance.subject}</label>
          <input
            id="pp-g-subject"
            className="input"
            maxLength={256}
            value={subject}
            aria-describedby="pp-g-subject-hint"
            onChange={(e) => setSubject(e.target.value)}
          />
          <p id="pp-g-subject-hint" className="pp-hint">{t.grievance.subjectHint}</p>
        </div>

        <div className="form-group">
          <label htmlFor="pp-g-desc">{t.grievance.description}</label>
          <textarea
            id="pp-g-desc"
            className="input pp-textarea"
            rows={5}
            required
            maxLength={20000}
            value={description}
            aria-describedby="pp-g-desc-hint"
            onChange={(e) => setDescription(e.target.value)}
          />
          <p id="pp-g-desc-hint" className="pp-hint">{t.grievance.descriptionHint}</p>
        </div>

        {purposes.length > 0 && (
          <div className="form-group">
            <label htmlFor="pp-g-purpose">{t.grievance.aboutPurpose}</label>
            <select
              id="pp-g-purpose"
              className="input"
              value={purposeCode}
              onChange={(e) => setPurposeCode(e.target.value)}
            >
              <option value="">{t.grievance.aboutPurposeNone}</option>
              {purposes.map((p) => (
                <option key={p.code} value={p.code}>{localizePurpose(p, lang).name}</option>
              ))}
            </select>
          </div>
        )}

        <button className="btn btn-primary pp-btn-wide" disabled={submitting || !description.trim()}>
          {submitting ? t.grievance.submitting : t.grievance.submit}
        </button>
      </form>

      <h2 className="pp-h2">{t.grievance.listTitle}</h2>
      {loading && <div className="center-load"><Spinner /></div>}
      {!loading && list.length === 0 && <p className="pp-empty">{t.grievance.listEmpty}</p>}

      {!loading && list.length > 0 && (
        <ul className="pp-griev-list">
          {list.map((g) => {
            const open = openRef === g.reference_no
            const detailId = `pp-griev-${g.reference_no}`
            return (
              <li key={g.reference_no} className="pp-griev">
                <div className="pp-griev-head">
                  <div>
                    <code className="pp-code">{g.reference_no}</code>
                    <p className="pp-griev-subject">{g.subject || t.grievance.cat[g.category as Category] || g.category}</p>
                  </div>
                  <Badge status={toneFor(g.status)}>{g.status.replace(/_/g, ' ')}</Badge>
                </div>
                <p className="pp-griev-meta">
                  {t.grievance.raisedOn} <time dateTime={g.received_at}>{formatDate(g.received_at)}</time>
                  {' · '}{t.grievance.dueOn} <time dateTime={g.due_at}>{formatDate(g.due_at)}</time>
                  {' · '}
                  {g.overdue ? (
                    <strong className="pp-overdue">{t.grievance.overdue}</strong>
                  ) : (
                    fill(t.grievance.daysLeft, { n: g.days_remaining })
                  )}
                </p>

                <button
                  className="btn btn-sm"
                  aria-expanded={open}
                  aria-controls={detailId}
                  onClick={() => setOpenRef(open ? null : g.reference_no)}
                >
                  {open ? t.grievance.hideDetail : t.grievance.viewDetail}
                </button>

                <div id={detailId} hidden={!open} className="pp-griev-detail">
                  <p className="pp-griev-desc">{g.description}</p>
                  {g.escalated_to && (
                    <p className="pp-griev-esc">{t.grievance.escalatedTo}: {g.escalated_to}</p>
                  )}
                  {g.resolution_summary && (
                    <>
                      <h3 className="pp-h3">{t.grievance.resolution}</h3>
                      <p className="pp-griev-desc">{g.resolution_summary}</p>
                    </>
                  )}
                  {g.events?.length > 0 && (
                    <>
                      <h3 className="pp-h3">{t.grievance.timeline}</h3>
                      <ol className="pp-timeline pp-timeline-sm">
                        {g.events.map((ev) => (
                          <li key={ev.id} className="pp-tl-item">
                            <div className="pp-tl-dot" aria-hidden="true" />
                            <div className="pp-tl-body">
                              <div className="pp-tl-head">
                                <span className="pp-tl-action">{ev.event.replace(/_/g, ' ')}</span>
                                <time className="pp-tl-time" dateTime={ev.created_at}>{formatDateTime(ev.created_at)}</time>
                              </div>
                              {ev.note && <p className="pp-tl-reason">{ev.note}</p>}
                            </div>
                          </li>
                        ))}
                      </ol>
                    </>
                  )}

                  {(g.status === 'RESOLVED' || g.status === 'CLOSED') && (
                    g.feedback_at ? (
                      <p className="pp-griev-fb-done">{t.grievance.feedbackDone}</p>
                    ) : feedbackFor === g.reference_no ? (
                      <div className="pp-feedback">
                        <h3 className="pp-h3">{t.grievance.feedbackTitle}</h3>
                        <p className="pp-hint">{t.grievance.feedbackIntro}</p>
                        <div className="form-group">
                          <label htmlFor={`pp-fb-rating-${g.reference_no}`}>{t.grievance.feedbackRating}</label>
                          <select
                            id={`pp-fb-rating-${g.reference_no}`}
                            className="input"
                            value={rating}
                            onChange={(e) => setRating(Number(e.target.value))}
                          >
                            {[1, 2, 3, 4, 5].map((n) => (
                              <option key={n} value={n}>{n}</option>
                            ))}
                          </select>
                        </div>
                        <div className="form-group">
                          <label htmlFor={`pp-fb-comment-${g.reference_no}`}>{t.grievance.feedbackComment}</label>
                          <textarea
                            id={`pp-fb-comment-${g.reference_no}`}
                            className="input pp-textarea"
                            rows={3}
                            maxLength={4000}
                            value={comment}
                            onChange={(e) => setComment(e.target.value)}
                          />
                        </div>
                        <button
                          className="btn btn-primary"
                          onClick={() => sendFeedback(g.reference_no)}
                          disabled={feedbackBusy}
                        >
                          {t.grievance.feedbackSubmit}
                        </button>
                      </div>
                    ) : (
                      <button className="btn btn-sm" onClick={() => setFeedbackFor(g.reference_no)}>
                        {t.grievance.feedbackTitle}
                      </button>
                    )
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

import { useEffect, useState } from 'react'
import { Badge, formatDateTime, Spinner } from '../ui'
import { getErrorMessage } from '../../api/client'
import { fill, type PortalStrings } from '../../translations/portal'
import type { NotificationRow } from '../../types'
import { portalApi, type PrincipalRecord } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
}

/**
 * The principal's own lifecycle record (GET /portal/history) plus the messages
 * the organisation sent them (GET /portal/notifications).
 *
 * Two separate lists rather than one interleaved timeline, deliberately: the
 * API's `ConsentHistoryOut` and `ConsentEvidenceOut` do not carry `consent_id`
 * (the model and the JSON export both do - see
 * services/principal_records.py::record_to_json_bytes - only the response
 * schemas omit it), so there is no key on which a change and its evidence row
 * can honestly be joined here. Pairing them on "timestamps look close enough"
 * would produce a screen that is right most of the time and silently wrong
 * when two purposes are changed in the same second - on a record whose whole
 * job is to be evidence. Until the schemas expose the id, they stay side by
 * side and the reader can see they are two views of the same events.
 */
export function PortalHistory({ t, token }: Props) {
  const [record, setRecord] = useState<PrincipalRecord | null>(null)
  const [messages, setMessages] = useState<NotificationRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    Promise.all([portalApi.history(token), portalApi.notifications(token).catch(() => null)])
      .then(([hist, notes]) => {
        if (!live) return
        setRecord(hist.data)
        setMessages(notes?.data ?? [])
        setError('')
      })
      .catch((e) => { if (live) setError(getErrorMessage(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [token])

  const acknowledge = async (id: number) => {
    setBusyId(id)
    try {
      const res = await portalApi.acknowledgeNotification(token, id)
      setMessages((prev) => prev.map((m) => (m.id === id ? res.data : m)))
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) return <div className="center-load"><Spinner /></div>

  const history = [...(record?.history ?? [])].reverse()
  const evidence = [...(record?.evidence ?? [])].reverse()

  return (
    <section aria-labelledby="pp-history-title">
      <h1 id="pp-history-title" className="pp-h1">{t.history.title}</h1>
      <p className="pp-lede">{t.history.intro}</p>
      {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

      {history.length === 0 ? (
        <p className="pp-empty">{t.history.empty}</p>
      ) : (
        <ol className="pp-timeline">
          {history.map((h) => (
            <li key={h.id} className="pp-tl-item">
              <div className="pp-tl-dot" aria-hidden="true" />
              <div className="pp-tl-body">
                <div className="pp-tl-head">
                  <span className="pp-tl-action">{h.action.replace(/_/g, ' ')}</span>
                  <time className="pp-tl-time" dateTime={h.created_at}>{formatDateTime(h.created_at)}</time>
                </div>
                {(h.from_status || h.to_status) && (
                  <p className="pp-tl-status">
                    {h.from_status && <Badge status={h.from_status} />}
                    {h.from_status && h.to_status && <span className="pp-tl-arrow" aria-hidden="true">→</span>}
                    {h.to_status && <Badge status={h.to_status} />}
                    <span className="pp-sr-only">
                      {fill(t.history.statusChange, { from: h.from_status ?? '', to: h.to_status ?? '' })}
                    </span>
                  </p>
                )}
                {h.reason && <p className="pp-tl-reason">{h.reason}</p>}
                <p className="pp-tl-meta">
                  {t.history.recordedBy}: {h.actor_username} · {h.source_app}
                  {h.request_id && <> · {t.history.requestId} <code>{h.request_id}</code></>}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}

      {evidence.length > 0 && (
        <>
          <h2 className="pp-h2">{t.history.evidenceRef}</h2>
          <ul className="pp-evidence-list">
            {evidence.map((e) => (
              <li key={e.id} className="pp-evidence">
                <code className="pp-code">{e.evidence_ref}</code>
                <span className="pp-evidence-meta">
                  <time dateTime={e.collected_at}>{formatDateTime(e.collected_at)}</time>
                  {' · '}{e.collection_method}
                  {e.language && <> · {t.history.language}: {e.language}</>}
                </span>
                {e.gpc_signal && <span className="pp-chip pp-chip-info">{t.history.gpcRecorded}</span>}
              </li>
            ))}
          </ul>
        </>
      )}

      <h2 className="pp-h2">{t.history.messagesTitle}</h2>
      <p className="pp-lede pp-lede-sm">{t.history.messagesIntro}</p>
      {messages.length === 0 ? (
        <p className="pp-empty">{t.history.messagesEmpty}</p>
      ) : (
        <ul className="pp-message-list">
          {messages.map((m) => (
            <li key={m.id} className="pp-message">
              <div className="pp-message-text">
                <p className="pp-message-subject">{m.subject || m.event_type.replace(/_/g, ' ')}</p>
                <p className="pp-message-meta">
                  {t.history.sentOn} <time dateTime={m.created_at}>{formatDateTime(m.sent_at || m.created_at)}</time>
                  {' · '}{m.channel}
                </p>
              </div>
              {m.acknowledged_at ? (
                <span className="pp-chip pp-chip-ok">{t.history.acknowledged}</span>
              ) : (
                <button
                  className="btn btn-sm"
                  onClick={() => acknowledge(m.id)}
                  disabled={busyId === m.id}
                  aria-label={`${t.history.acknowledge}: ${m.subject || m.event_type}`}
                >
                  {t.history.acknowledge}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

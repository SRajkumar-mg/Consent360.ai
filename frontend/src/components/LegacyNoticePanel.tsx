import { useCallback, useEffect, useState } from 'react'
import { legacyNoticeApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, EmptyState, MetricCard, Modal, Spinner, formatDateTime, formatPct, useToast } from './ui'
import { RefusalNotice } from './GuardedAction'
import { IconAlert, IconCheck, IconClock, IconInbox } from './icons'
import type {
  LegacyCohort,
  LegacyNoticeCampaign,
  LegacyNoticeDelivery,
  LegacyNoticeMetrics,
  Organization,
} from '../types'

/**
 * R2-11 / gap A-08 (K-26): the s.5(2) legacy-notice campaign, in admin.
 *
 * s.5(2) says a fiduciary processing data on consent given before the Act
 * commenced must give the principal a notice "as soon as reasonably
 * practicable", and may keep processing "until the Data Principal withdraws
 * her consent". This screen is the operator's side of that: pick the cohort,
 * send the notice, and — the part that is actually load-bearing — be able to
 * show, per principal, what was sent and whether it arrived. Under s.6(10)
 * the burden of proving consent-and-notice sits with the fiduciary, so a
 * campaign screen that only says "sent 2,000 emails" proves nothing.
 *
 * Two rules this screen keeps, and must keep:
 *
 *  1. **Queued is never rendered as delivered.** The four delivery states come
 *     straight off `notifications.status` and are shown as four separate
 *     numbers. A notice sitting in PENDING is counted as pending, in its own
 *     column, with its own colour, and "Send queued notices now" exists
 *     precisely so an operator can *act* on that rather than be told a
 *     comfortable aggregate. FAILED is likewise never folded into anything.
 *  2. **The cut-off is the operator's, not ours.** The date that counts as
 *     "the commencement of this Act" is a legal determination. The field is
 *     pre-filled with the platform's engineering default and says so in
 *     words; it is editable, and whatever the operator ran with is stamped on
 *     every notice and shown back on the campaign.
 */

const DEFAULT_CUTOFF = '2027-05-13'

function deliveryBadge(status: string): string {
  // Maps onto the badge palette the rest of the console already uses. PENDING
  // and FAILED deliberately do NOT get a positive tone.
  if (status === 'DELIVERED' || status === 'SENT') return 'ACTIVE'
  if (status === 'ACKNOWLEDGED') return 'GRANTED'
  if (status === 'FAILED') return 'DENIED'
  return 'PENDING'
}

interface Props {
  canManage: boolean
  tenants: Organization[]
}

export function LegacyNoticePanel({ canManage, tenants }: Props) {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [cutoff, setCutoff] = useState(DEFAULT_CUTOFF)
  const [sourceApp, setSourceApp] = useState('')
  const [cohort, setCohort] = useState<LegacyCohort | null>(null)
  const [metrics, setMetrics] = useState<LegacyNoticeMetrics | null>(null)
  const [campaigns, setCampaigns] = useState<LegacyNoticeCampaign[]>([])
  const [detail, setDetail] = useState<LegacyNoticeCampaign | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [note, setNote] = useState('')
  const [includeNotified, setIncludeNotified] = useState(false)

  const load = useCallback(async () => {
    const params = { cutoff, ...(sourceApp ? { source_app: sourceApp } : {}) }
    const [c, m, cs] = await Promise.all([
      legacyNoticeApi.cohort({ ...params, limit: 500 }),
      legacyNoticeApi.metrics(params),
      legacyNoticeApi.campaigns({ limit: 200 }),
    ])
    setCohort(c.data)
    setMetrics(m.data)
    setCampaigns(cs.data)
  }, [cutoff, sourceApp])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true)
    setRefusal('')
    try {
      await fn()
      toast('success', message)
      await load()
      if (detail) {
        const fresh = await legacyNoticeApi.campaign(detail.campaign_ref)
        setDetail(fresh.data)
      }
    } catch (e) {
      setRefusal(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const openDetail = async (ref: string) => {
    try {
      const res = await legacyNoticeApi.campaign(ref)
      setDetail(res.data)
    } catch (e) {
      setRefusal(getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  const outstanding = cohort?.outstanding ?? 0
  const totalPending = campaigns.reduce((n, c) => n + c.pending, 0)

  return (
    <div>
      {metrics && (
        <div className="metric-grid mb">
          <MetricCard
            label="Pre-Act consents"
            value={metrics.pre_act_consents}
            tone="info"
            icon={<IconInbox size={20} />}
            sub={`${metrics.pre_act_principals} principal(s) last affirmed before ${metrics.cutoff}`}
          />
          <MetricCard
            label="Legacy notice delivered (K-26)"
            value={formatPct(metrics.legacy_notice_delivery_pct)}
            tone={
              metrics.legacy_notice_delivery_pct == null
                ? 'slate'
                : metrics.legacy_notice_delivery_pct >= 100
                  ? 'success'
                  : 'warning'
            }
            icon={<IconCheck size={20} />}
            sub={
              metrics.legacy_notice_delivery_pct == null
                ? 'no pre-Act cohort — unknown, not 100%'
                : `${metrics.consents_notified} of ${metrics.pre_act_consents} consents behind a notice that reached its principal`
            }
          />
          <MetricCard
            label="Still to notify"
            value={outstanding}
            tone={outstanding ? 'warning' : 'success'}
            icon={<IconAlert size={20} />}
            sub={`${metrics.principals_acknowledged} principal(s) have confirmed reading theirs`}
          />
          <MetricCard
            label="Queued, not yet sent"
            value={totalPending}
            tone={totalPending ? 'warning' : 'slate'}
            icon={<IconClock size={20} />}
            sub={
              totalPending
                ? 'nothing here has been delivered — dispatch it or start the scheduler'
                : 'no notice is waiting in the queue'
            }
          />
        </div>
      )}

      <RefusalNotice title="The legacy-notice API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      <div className="card mb">
        <div className="card-header"><h3>Who is owed a s.5(2) notice</h3></div>
        <div className="card-body">
          <p className="text-sm text-secondary">
            A principal is in this cohort when the consent being relied on today was last affirmed
            <b> before the cut-off</b> — that is, she was never shown the notice s.5(1) now requires. A
            renewal after the cut-off takes her out of it, because she did consent again, under the
            current notice. Sending the notice does <b>not</b> stop processing: s.5(2) permits it to
            continue until she withdraws, which is why every notice carries a live withdrawal path
            and lands in her portal next to the withdraw button.
          </p>
          <div className="form-row" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 12 }}>
            <div className="form-group">
              <label htmlFor="legacy-cutoff">Act commencement (cut-off)</label>
              <input
                id="legacy-cutoff"
                type="date"
                className="input"
                value={cutoff}
                onChange={(e) => setCutoff(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="legacy-source">Tenant</label>
              <select
                id="legacy-source"
                className="input"
                value={sourceApp}
                onChange={(e) => setSourceApp(e.target.value)}
              >
                <option value="">All tenants</option>
                {tenants.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
              </select>
            </div>
            <div className="form-group" style={{ flex: 1, minWidth: 260 }}>
              <label htmlFor="legacy-note">Why this campaign is being run (recorded)</label>
              <input
                id="legacy-note"
                className="input"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. s.5(2) notice to every pre-Act principal ahead of the 13 May 2027 date"
              />
            </div>
            {canManage && (
              <button
                className="btn btn-primary"
                disabled={busy || !cohort || (includeNotified ? cohort.cohort_size : outstanding) === 0}
                onClick={() => setConfirmOpen(true)}
              >
                Send the notice…
              </button>
            )}
          </div>
          <label className="flex text-sm" style={{ gap: 8, alignItems: 'center', marginTop: 4 }}>
            <input
              type="checkbox"
              checked={includeNotified}
              onChange={(e) => setIncludeNotified(e.target.checked)}
            />
            Re-notify principals whose notice already reached them (use after correcting the notice itself)
          </label>
          <p className="text-xs text-muted" style={{ marginTop: 10 }}>
            {DEFAULT_CUTOFF} is this platform's engineering default: the 18-month tranche of the Act's
            commencement notification (G.S.R. 843(E)) under which s.5 becomes enforceable. It is not
            legal advice, some advisers compute the anniversary as 12 May 2027, and the date you run
            with is recorded on every notice sent.
          </p>
        </div>

        {!cohort || cohort.members.length === 0 ? (
          <div className="card-body">
            <EmptyState message={`No consent in this scope was last affirmed before ${cutoff}. Nobody is owed a s.5(2) notice on that date.`} />
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <caption className="table-caption-hidden">
                Principals whose consent pre-dates the cut-off, and whether a legacy notice has reached them
              </caption>
              <thead>
                <tr>
                  <th>Principal</th><th>Tenant</th><th>Consents</th><th>Purposes</th>
                  <th>Consent given</th><th>Notice</th>
                </tr>
              </thead>
              <tbody>
                {cohort.members.slice(0, 100).map((m) => (
                  <tr key={m.customer_external_id}>
                    <td className="mono text-xs">{m.customer_external_id}</td>
                    <td className="text-xs">{m.source_app || '—'}</td>
                    <td>{m.consent_count}</td>
                    <td className="text-xs text-secondary">{m.purpose_names.join(', ') || '—'}</td>
                    <td className="text-xs">{formatDateTime(m.oldest_consent_at)}</td>
                    <td className="text-xs">
                      {m.last_notice_status ? (
                        <>
                          <Badge status={deliveryBadge(m.last_notice_status)}>{m.last_notice_status}</Badge>
                          <div className="text-muted">
                            {m.notified_at ? formatDateTime(m.notified_at) : 'not yet delivered'}
                            {m.last_campaign_ref ? ` · ${m.last_campaign_ref}` : ''}
                          </div>
                        </>
                      ) : (
                        <span className="text-danger">Never notified</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(cohort.truncated || cohort.members.length > 100) && (
              <div className="card-body">
                <p className="text-xs text-muted">
                  Showing the 100 oldest of {cohort.cohort_size}. The campaign covers the whole cohort,
                  not this page.
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Campaigns and delivery evidence</h3>
          {canManage && totalPending > 0 && (
            <button
              className="btn btn-sm"
              disabled={busy}
              onClick={() => run(
                () => legacyNoticeApi.dispatch(),
                'Dispatch attempted. Delivery state below is what the transport actually reported.',
              )}
            >
              Send {totalPending} queued notice(s) now
            </button>
          )}
        </div>
        <div className="card-body">
          <p className="text-sm text-secondary">
            Every number below is counted from the notification rows themselves on each read, so a
            retry that succeeded an hour later moves them. <b>Queued</b> means written to the queue and
            never yet attempted — it is not delivery, and is never added to the sent or delivered
            columns. Because the interim transport reports no delivery callback, <b>delivered</b> means
            the channel accepted the message; <b>read</b> is the only column a principal herself put
            there, by acknowledging the notice in her portal.
          </p>
        </div>
        {campaigns.length === 0 ? (
          <div className="card-body"><EmptyState message="No legacy notice has been sent yet." /></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Campaign</th><th>Cut-off</th><th>Principals</th>
                  <th>Queued</th><th>Sent</th><th>Delivered</th><th>Failed</th><th>Read</th>
                  <th>Started</th><th />
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.campaign_ref}>
                    <td className="mono text-xs">
                      {c.campaign_ref}
                      {c.note && <div className="text-muted">{c.note}</div>}
                      {c.source_apps.length > 0 && (
                        <div className="text-muted">{c.source_apps.join(', ')}</div>
                      )}
                    </td>
                    <td className="text-xs">{c.cutoff || '—'}</td>
                    <td>
                      {c.principals_reached}/{c.recipients}
                      <div className="text-xs text-muted">reached</div>
                    </td>
                    <td className={c.pending ? 'text-warning' : ''}>{c.pending}</td>
                    <td>{c.sent}</td>
                    <td>{c.delivered}</td>
                    <td className={c.failed ? 'text-danger' : ''}>{c.failed}</td>
                    <td>{c.acknowledged}</td>
                    <td className="text-xs">
                      {formatDateTime(c.started_at)}
                      <div className="text-muted">{c.started_by}</div>
                    </td>
                    <td>
                      <button className="btn btn-sm" onClick={() => openDetail(c.campaign_ref)}>Evidence</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={confirmOpen}
        title="Send the s.5(2) legacy notice"
        onClose={() => setConfirmOpen(false)}
        footer={
          <>
            <button className="btn" onClick={() => setConfirmOpen(false)}>Cancel</button>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => {
                setConfirmOpen(false)
                run(
                  () => legacyNoticeApi.send({
                    cutoff,
                    source_app: sourceApp || null,
                    note,
                    include_notified: includeNotified,
                  }),
                  'Notices queued. Nothing has been delivered until they are dispatched.',
                )
              }}
            >
              Queue the notice
            </button>
          </>
        }
      >
        <p className="text-sm mb">
          This queues one notice per principal, on every channel she has an address for, plus an
          in-portal copy that always reaches her. It sends to{' '}
          <b>{includeNotified ? cohort?.cohort_size ?? 0 : outstanding} principal(s)</b> whose consent
          was last affirmed before <b>{cutoff}</b>
          {sourceApp ? <> in tenant <b>{sourceApp}</b></> : ' across every tenant'}.
        </p>
        <p className="text-sm text-secondary mb">
          Each notice states what is processed and why, her rights, and that processing continues
          until she withdraws — with the withdrawal path in the message. It asks her to do nothing to
          keep her consent in place, because a notice that treated silence as agreement would be the
          failure s.5(2) exists to prevent.
        </p>
        <p className="text-sm text-secondary">
          Queuing is recorded now; delivery is recorded when the dispatcher runs. Both end up on this
          screen as separate facts.
        </p>
      </Modal>

      <Modal
        open={!!detail}
        wide
        title={detail ? `Delivery evidence — ${detail.campaign_ref}` : ''}
        onClose={() => setDetail(null)}
      >
        {detail && <DeliveryEvidence campaign={detail} />}
      </Modal>
    </div>
  )
}

function DeliveryEvidence({ campaign }: { campaign: LegacyNoticeCampaign }) {
  return (
    <>
      <dl className="detail-grid mb">
        <div className="detail-item"><dt>Cut-off used</dt><dd>{campaign.cutoff || '—'}</dd></div>
        <div className="detail-item"><dt>Started</dt><dd>{formatDateTime(campaign.started_at)} by {campaign.started_by}</dd></div>
        <div className="detail-item"><dt>Principals</dt><dd>{campaign.principals_reached} reached of {campaign.recipients}</dd></div>
        <div className="detail-item"><dt>Confirmed read</dt><dd>{campaign.principals_acknowledged}</dd></div>
      </dl>
      {campaign.note && <p className="quote mb">{campaign.note}</p>}

      {campaign.pending > 0 && (
        <div className="alert alert-warning mb">
          <b>{campaign.pending} notice(s) are still only queued.</b> They have not been attempted, so
          they have not been delivered, and this campaign cannot yet be offered as evidence for the
          principals they belong to.
        </div>
      )}
      {campaign.failed > 0 && (
        <div className="alert alert-error mb">
          <b>{campaign.failed} notice(s) failed after every retry.</b> Each row below carries the last
          error. A principal reachable on no channel still has the in-portal copy, but a failed email
          is not a notice given.
        </div>
      )}

      <h4 className="kpi-section-title">Per recipient</h4>
      <div className="table-wrap" style={{ maxHeight: 420, overflow: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>Principal</th><th>Channel</th><th>To</th><th>State</th>
              <th>What happened, and when</th>
            </tr>
          </thead>
          <tbody>
            {campaign.deliveries.map((d: LegacyNoticeDelivery) => (
              <tr key={d.notification_id}>
                <td className="mono text-xs">{d.customer_external_id || '—'}</td>
                <td className="text-xs">{d.channel}</td>
                <td className="text-xs mono">{d.recipient_masked || '—'}</td>
                <td>
                  <Badge status={deliveryBadge(d.status)}>{d.status}</Badge>
                  {/* Attempts sit under the state rather than in a column of
                      their own: they only ever qualify the state ("FAILED,
                      after 5 tries"), and as a sixth column they were pushed
                      off the edge of the dialog. */}
                  <div className="text-xs text-muted">{d.retry_count}/{d.max_retries} attempts</div>
                  {d.last_error && <div className="text-xs text-danger">{d.last_error}</div>}
                </td>
                {/* All four timestamps in one cell rather than four columns.
                    They were four, and the table then scrolled sideways inside
                    the dialog — so the "delivered" and "read" columns, the two
                    that carry the evidentiary weight, were the ones off-screen.
                    A stage that has not happened shows a dash: an empty
                    "Delivered" line is a fact about this notice, not a gap in
                    the table. */}
                <td className="text-xs">
                  <dl className="delivery-stages">
                    <div><dt>Queued</dt><dd>{formatDateTime(d.queued_at)}</dd></div>
                    <div><dt>Sent</dt><dd>{formatDateTime(d.sent_at)}</dd></div>
                    <div><dt>Delivered</dt><dd>{formatDateTime(d.delivered_at)}</dd></div>
                    <div><dt>Read</dt><dd>{formatDateTime(d.acknowledged_at)}</dd></div>
                  </dl>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted" style={{ marginTop: 10 }}>
        Addresses are masked; the principal is identified by her external id. Every row here is also
        an append-only audit entry (LEGACY_NOTICE_SENT, NOTIFICATION_DELIVERED /
        NOTIFICATION_SEND_FAILED) that cannot be edited after the fact.
      </p>
    </>
  )
}

/**
 * R1-09: re-consent on material change (`/re-consent`).
 *
 * The premise: consent is to a *specified* purpose (s.6(1)), so when the thing
 * consented to changes materially, the old consent no longer covers the new
 * processing and has to be refreshed. The backend classifies every purpose,
 * policy and cookie-policy change as MATERIAL, NARROWING or COSMETIC and the
 * decision engine blocks on the material ones.
 *
 * This screen exists to make that classification legible *before* someone
 * argues with it. The Materiality rules tab is the published rulebook — field
 * by field, why a change of that field is material, and whether it can be
 * overridden at all. Reading the rule is what makes a campaign explicable to
 * an auditor; a campaign with no visible basis is just a mass email.
 *
 * R2-11 / gap A-08 adds the **Legacy notices** tab, which is the other half of
 * the same question. Re-consent is for a principal whose consent no longer
 * covers what is being done; a legacy notice is for one who was never given a
 * compliant notice in the first place, because her consent pre-dates the Act.
 * s.5(2) does not grandfather that consent — it requires the notice to be
 * given now, and permits processing to continue only "until the Data Principal
 * withdraws her consent". Both tabs answer "who have we had to go back to, and
 * what happened when we did", which is why they live on one screen rather than
 * two.
 */
import { useCallback, useEffect, useState } from 'react'
import { organizationsApi, reConsentApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDateTime, formatPct, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { LegacyNoticePanel } from '../components/LegacyNoticePanel'
import { IconAlert, IconCheck, IconHistory, IconInbox, IconRefresh } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type {
  CookiePolicyVersion, MaterialityRule, Organization, PolicyChange, ReConsentCampaign, ReConsentMetrics,
} from '../types'

type Tab = 'campaigns' | 'changes' | 'cookie' | 'legacy' | 'rules'

function materialityBadge(m: string): 'DENIED' | 'REQUESTED' | 'ACTIVE' {
  if (m === 'MATERIAL') return 'DENIED'
  if (m === 'NARROWING') return 'REQUESTED'
  return 'ACTIVE'
}

export function ReConsentPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('policy.manage')

  const [tab, setTab] = useState<Tab>('campaigns')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [campaigns, setCampaigns] = useState<ReConsentCampaign[]>([])
  const [changes, setChanges] = useState<PolicyChange[]>([])
  const [rules, setRules] = useState<MaterialityRule[]>([])
  const [metrics, setMetrics] = useState<ReConsentMetrics | null>(null)
  const [cookieVersions, setCookieVersions] = useState<CookiePolicyVersion[]>([])
  const [tenants, setTenants] = useState<Organization[]>([])
  const [tenantCode, setTenantCode] = useState('')
  const [materialityFilter, setMaterialityFilter] = useState('')
  const [changeDetail, setChangeDetail] = useState<PolicyChange | null>(null)

  const load = useCallback(async () => {
    const [c, ch, r, m, cv] = await Promise.all([
      reConsentApi.campaigns({ limit: 200 }),
      reConsentApi.changes(materialityFilter ? { materiality: materialityFilter, limit: 200 } : { limit: 200 }),
      reConsentApi.rules(),
      reConsentApi.metrics(),
      reConsentApi.cookiePolicyVersions(tenantCode ? { tenant_code: tenantCode } : {}).catch(() => ({ data: [] as CookiePolicyVersion[] })),
    ])
    setCampaigns(c.data); setChanges(ch.data); setRules(r.data); setMetrics(m.data); setCookieVersions(cv.data)
  }, [materialityFilter, tenantCode])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  useEffect(() => { organizationsApi.list().then((r) => setTenants(r.data)).catch(() => setTenants([])) }, [])

  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true); setRefusal('')
    try { await fn(); toast('success', message); await load() } catch (e) { setRefusal(getErrorMessage(e)) } finally { setBusy(false) }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title="Re-consent & change log"
        subtitle="Every change to a purpose, policy or cookie policy, how it was classified, and the campaigns the material ones opened"
      />

      {!canManage && <ReadOnlyBanner permission="policy.manage" what="Closing a re-consent campaign and publishing a cookie policy" />}

      <RefusalNotice title="The re-consent API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      {metrics && (
        <div className="metric-grid mb">
          <MetricCard label="Open campaigns" value={metrics.open_campaigns} tone={metrics.open_campaigns ? 'warning' : 'success'}
            icon={<IconRefresh size={20} />} sub={`${metrics.campaigns} campaign(s) in total`} />
          <MetricCard label="Consents flagged" value={metrics.consents_flagged} tone="info" icon={<IconAlert size={20} />}
            sub={`${metrics.consents_blocked_now} currently blocked by the decision engine`} />
          <MetricCard
            label="Re-consent rate"
            value={formatPct(metrics.re_consent_rate_pct)}
            tone={metrics.re_consent_rate_pct == null ? 'slate' : metrics.re_consent_rate_pct >= 80 ? 'success' : 'warning'}
            icon={<IconCheck size={20} />}
            sub={metrics.re_consent_rate_pct == null
              ? 'nothing flagged yet — unknown, not zero'
              : `${metrics.fresh_consents} fresh consents against ${metrics.consents_flagged} flagged`} />
          <MetricCard label="Material changes" value={metrics.material_changes} tone="primary" icon={<IconHistory size={20} />}
            sub={`${metrics.changes_logged} changes classified in total`} />
        </div>
      )}

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'campaigns', label: 'Campaigns', count: campaigns.length },
          { id: 'changes', label: 'Change log', count: changes.length },
          { id: 'cookie', label: 'Cookie policy', count: cookieVersions.length },
          {
            id: 'legacy',
            label: 'Legacy notices',
            hint: 's.5(2): principals whose consent pre-dates the Act, the notice they are owed, and whether it arrived',
          },
          { id: 'rules', label: 'Materiality rules', count: rules.length },
        ]}
      />

      {tab === 'campaigns' && (
        <div className="card">
          <div className="card-header"><h3>Re-consent campaigns</h3></div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              A campaign opens automatically when a material change lands. It is not a mailing: while it is open, the
              decision engine refuses to rely on the flagged consents, so closing one is a statement that the affected
              principals have been asked again — not a way to silence the block.
            </p>
          </div>
          {campaigns.length === 0 ? (
            <div className="card-body"><EmptyState message="No campaign has been opened. No material change has landed yet." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Campaign</th><th>Entity</th><th>Versions</th><th>Status</th><th>Flagged</th><th>Fresh</th><th>Started</th><th /></tr></thead>
                <tbody>
                  {campaigns.map((c) => (
                    <tr key={c.campaign_ref}>
                      <td className="mono text-xs">{c.campaign_ref}<div className="text-muted">{c.reason}</div></td>
                      <td>{c.entity_type} <span className="mono text-xs">{c.entity_code}</span></td>
                      <td>v{c.from_version ?? '—'} → v{c.to_version}</td>
                      <td><Badge status={c.status === 'OPEN' ? 'PENDING' : c.status === 'CANCELLED' ? 'EXPIRED' : 'ACTIVE'}>{c.status}</Badge></td>
                      <td>{c.consents_flagged}<div className="text-xs text-muted">{c.notifications_queued} notified</div></td>
                      <td>{c.fresh_consents}</td>
                      <td className="text-xs">{formatDateTime(c.started_at)}<div className="text-muted">{c.started_by}</div></td>
                      <td>
                        {canManage && c.status === 'OPEN' && (
                          <div className="flex" style={{ gap: 6 }}>
                            <button className="btn btn-sm" disabled={busy} onClick={() => {
                              const reason = window.prompt('Why is this campaign complete? (recorded)')
                              if (reason == null) return
                              run(() => reConsentApi.closeCampaign(c.campaign_ref, { status: 'COMPLETED', reason }), 'Campaign completed.')
                            }}>Complete</button>
                            <button className="btn btn-ghost-danger btn-sm" disabled={busy} onClick={() => {
                              const reason = window.prompt('Why is this campaign being cancelled? (recorded)')
                              if (reason == null) return
                              run(() => reConsentApi.closeCampaign(c.campaign_ref, { status: 'CANCELLED', reason }), 'Campaign cancelled.')
                            }}>Cancel</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'changes' && (
        <div className="card">
          <div className="card-header">
            <h3>Change log</h3>
            <select className="input" style={{ width: 'auto' }} value={materialityFilter}
              onChange={(e) => setMaterialityFilter(e.target.value)} aria-label="Materiality">
              <option value="">All classifications</option>
              {['MATERIAL', 'NARROWING', 'COSMETIC'].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          {changes.length === 0 ? (
            <div className="card-body"><EmptyState message="No change has been classified yet." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Change</th><th>Entity</th><th>Versions</th><th>Classified</th><th>Affected consents</th><th>Override</th><th>When</th><th /></tr></thead>
                <tbody>
                  {changes.map((c) => (
                    <tr key={c.change_ref}>
                      <td className="mono text-xs">{c.change_ref}</td>
                      <td>{c.entity_type} <span className="mono text-xs">{c.entity_code}</span></td>
                      <td>v{c.from_version ?? '—'} → v{c.to_version}</td>
                      <td><Badge status={materialityBadge(c.materiality)}>{c.materiality}</Badge></td>
                      <td>{c.affected_consents}</td>
                      <td className="text-xs">
                        {c.overridden_by
                          ? <><b>{c.overridden_by}</b><div className="text-muted">{c.override_justification}</div></>
                          : <span className="text-muted">—</span>}
                      </td>
                      <td className="text-xs">{formatDateTime(c.created_at)}<div className="text-muted">{c.actor_username}</div></td>
                      <td><button className="btn btn-sm" onClick={() => setChangeDetail(c)}>Why</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'cookie' && (
        <div className="card">
          <div className="card-header">
            <h3>Cookie policy versions</h3>
            <select className="input" style={{ width: 'auto' }} value={tenantCode} onChange={(e) => setTenantCode(e.target.value)} aria-label="Tenant">
              <option value="">Default tenant</option>
              {tenants.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
            </select>
          </div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              Publishing a cookie policy is a change like any other: it is classified, and a material one invalidates
              stored preferences rather than quietly keeping them. The <b>preferences invalidated</b> column is that
              consequence made countable.
            </p>
          </div>
          {cookieVersions.length === 0 ? (
            <div className="card-body"><EmptyState message="No cookie policy has been published for this tenant." /></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Version</th><th>Categories</th><th>Summary</th><th>Current</th><th>Preferences invalidated</th><th>Published</th><th>Hash</th></tr></thead>
                <tbody>
                  {cookieVersions.map((v) => (
                    <tr key={v.id}>
                      <td>v{v.version_number}</td>
                      <td>{v.categories.length}</td>
                      <td className="text-sm">{v.summary || <span className="text-muted">—</span>}</td>
                      <td>{v.is_current ? <Badge status="ACTIVE">Current</Badge> : <span className="text-muted text-xs">superseded</span>}</td>
                      <td>{v.preferences_invalidated}</td>
                      <td className="text-xs">{formatDateTime(v.published_at)}<div className="text-muted">{v.published_by}</div></td>
                      <td className="mono text-xs" title={v.content_hash || ''}>{v.content_hash ? `${v.content_hash.slice(0, 12)}…` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'legacy' && <LegacyNoticePanel canManage={canManage} tenants={tenants} />}

      {tab === 'rules' && (
        <div className="card">
          <div className="card-header"><h3>Materiality rules</h3></div>
          <div className="card-body">
            <p className="text-sm text-secondary">
              The rulebook the classifier applies, field by field. <b>Always material</b> means a change to that field
              can never be downgraded; <b>overridable</b> means an operator may record a justified override, which the
              change log then shows by name. Publishing this list is what makes a classification arguable rather than
              arbitrary.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Field</th><th>Kind</th><th>Always material</th><th>Overridable</th><th>Why a change is material</th><th>When it is only narrowing</th></tr></thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.field}>
                    <td className="mono text-xs">{r.field}</td>
                    <td>{r.kind}</td>
                    <td>{r.always_material ? <b className="text-danger">Yes</b> : 'No'}</td>
                    <td>{r.overridable ? 'Yes' : <b>No</b>}</td>
                    <td className="text-sm text-secondary">{r.why_material}</td>
                    <td className="text-sm text-secondary">{r.why_narrowing || <span className="text-muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab !== 'legacy' && (
        <FooterRow left={metrics ? `${metrics.changes_logged} changes classified · ${metrics.material_changes} material` : ''} />
      )}

      <Modal open={!!changeDetail} wide title={changeDetail ? `Change ${changeDetail.change_ref}` : ''} onClose={() => setChangeDetail(null)}>
        {changeDetail && (
          <>
            <div className={`alert ${changeDetail.materiality === 'MATERIAL' ? 'alert-error' : 'alert-info'} mb`}>
              <b>Classified {changeDetail.materiality}.</b> {changeDetail.materiality_basis}
            </div>
            <dl className="detail-grid mb">
              <div className="detail-item"><dt>Entity</dt><dd>{changeDetail.entity_type} {changeDetail.entity_code}</dd></div>
              <div className="detail-item"><dt>Versions</dt><dd>v{changeDetail.from_version ?? '—'} → v{changeDetail.to_version}</dd></div>
              <div className="detail-item"><dt>Affected consents</dt><dd>{changeDetail.affected_consents}</dd></div>
              <div className="detail-item"><dt>Made by</dt><dd>{changeDetail.actor_username} on {formatDateTime(changeDetail.created_at)}</dd></div>
              {changeDetail.overridden_by && (
                <div className="detail-item"><dt>Overridden by</dt><dd>{changeDetail.overridden_by} — {changeDetail.override_justification}</dd></div>
              )}
            </dl>
            <h4 className="kpi-section-title">Fields that changed</h4>
            <pre className="code-block" style={{ maxHeight: 320, overflow: 'auto' }}>
              {JSON.stringify(changeDetail.changed_fields, null, 2)}
            </pre>
            <h4 className="kpi-section-title">The rules that applied</h4>
            {Object.keys(changeDetail.changed_fields || {}).map((f) => {
              const rule = rules.find((r) => r.field === f)
              return (
                <div className="quote" key={f}>
                  <b className="mono">{f}</b>{' — '}
                  {rule
                    ? <>{rule.always_material ? 'always material. ' : ''}{rule.why_material}</>
                    : <span className="text-muted">no published rule for this field; it did not drive the classification.</span>}
                </div>
              )
            })}
            {Object.keys(changeDetail.changed_fields || {}).length === 0 && (
              <EmptyState icon={<IconInbox size={22} />} message="The change log recorded no per-field diff for this change." />
            )}
          </>
        )}
      </Modal>
    </div>
  )
}

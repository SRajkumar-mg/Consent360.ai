import { useEffect, useState } from 'react'
import { organizationsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Avatar, Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, TableSkeleton, formatDate, formatDateTime, useToast,
} from '../components/ui'
import {
  IconAudit, IconCheck, IconCustomers, IconGlobe, IconPlus, IconShield, IconEye,
} from '../components/icons'
import { LanguageSelector, usePersistedLang } from '../components/LanguageSelector'
import { useAuth } from '../context/AuthContext'
import { useTranslation } from '../hooks/useTranslation'
import type { AuditEvent, Customer, Organization, OrganizationDashboard, OrganizationUser } from '../types'

const EMPTY_ORG = { name: '', code: '', domain: '', description: '' }

function statusBadge(status: string) {
  const s = status.toUpperCase()
  if (s.includes('GRANTED') || s.includes('ACTIVE')) return 'b-success'
  if (s.includes('PENDING')) return 'b-warning'
  return 'b-info'
}

export function OrganizationsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('user.manage')
  const [lang, setLang] = usePersistedLang()
  const t = useTranslation('organizations', lang)

  const [orgs, setOrgs] = useState<Organization[]>([])
  const [customersByOrg, setCustomersByOrg] = useState<Record<string, number>>({})
  const [dashByOrg, setDashByOrg] = useState<Record<number, OrganizationDashboard>>({})
  const [loading, setLoading] = useState(true)

  const [showOrgModal, setShowOrgModal] = useState(false)
  const [editOrg, setEditOrg] = useState<Organization | null>(null)
  const [orgForm, setOrgForm] = useState(EMPTY_ORG)
  const [orgActive, setOrgActive] = useState(true)

  const [detailOrg, setDetailOrg] = useState<Organization | null>(null)
  const [detailData, setDetailData] = useState<OrganizationDashboard | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [tenantForm, setTenantForm] = useState({
    dpo_name: '', dpo_email: '', dpo_phone: '', withdraw_url: '', rights_url: '',
    grievance_url: '', board_complaint_url: '', grievance_response_days: 90,
    default_language: 'en', environment: 'DEV',
  })
  const [savingTenant, setSavingTenant] = useState(false)

  const load = async () => {
    const [o, c] = await Promise.all([organizationsApi.list(), organizationsApi.getDashboard(0).catch(() => null)])
    const orgList = o.data
    setOrgs(orgList)

    const custCounts: Record<string, number> = {}
    for (const org of orgList) {
      try {
        const r = await organizationsApi.getDashboard(org.id)
        custCounts[org.code.toUpperCase()] = r.data.total_customers
      } catch {
        custCounts[org.code.toUpperCase()] = 0
      }
    }
    setCustomersByOrg(custCounts)

    const dashEntries = await Promise.all(
      orgList.map(async (org) => {
        try {
          const r = await organizationsApi.getDashboard(org.id)
          return [org.id, r.data] as const
        } catch {
          return [org.id, null] as const
        }
      }),
    )
    const dmap: Record<number, OrganizationDashboard> = {}
    for (const [id, d] of dashEntries) if (d) dmap[id] = d
    setDashByOrg(dmap)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const openCreate = () => {
    setEditOrg(null)
    setOrgForm(EMPTY_ORG)
    setOrgActive(true)
    setShowOrgModal(true)
  }

  const openEdit = (org: Organization) => {
    setEditOrg(org)
    setOrgForm({ name: org.name, code: org.code, domain: org.domain || '', description: org.description || '' })
    setOrgActive(org.is_active)
    setShowOrgModal(true)
  }

  const closeOrgModal = () => {
    setShowOrgModal(false)
    setEditOrg(null)
  }

  const saveOrg = async () => {
    if (!orgForm.name.trim() || !orgForm.code.trim()) {
      toast('warning', 'Name and code are required')
      return
    }
    try {
      if (editOrg) {
        await organizationsApi.update(editOrg.id, { ...orgForm, is_active: orgActive })
        toast('success', t.updatedToast)
      } else {
        await organizationsApi.create(orgForm)
        toast('success', t.createdToast)
      }
      closeOrgModal()
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const saveTenantSettings = async () => {
    if (!detailOrg) return
    setSavingTenant(true)
    try {
      await organizationsApi.updateSettings(detailOrg.id, tenantForm)
      toast('success', t.tenantSettingsSaved)
      await openDetail(detailOrg)
    } catch (e) {
      toast('error', getErrorMessage(e))
    } finally {
      setSavingTenant(false)
    }
  }

  const openDetail = async (org: Organization) => {
    setDetailOrg(org)
    setDetailLoading(true)
    try {
      const r = await organizationsApi.getDashboard(org.id)
      setDetailData(r.data)
      setTenantForm({
        dpo_name: r.data.organization.dpo_name, dpo_email: r.data.organization.dpo_email,
        dpo_phone: r.data.organization.dpo_phone, withdraw_url: r.data.organization.withdraw_url,
        rights_url: r.data.organization.rights_url, grievance_url: r.data.organization.grievance_url,
        board_complaint_url: r.data.organization.board_complaint_url,
        grievance_response_days: r.data.organization.grievance_response_days,
        default_language: r.data.organization.default_language, environment: r.data.organization.environment,
      })
    } catch (e) {
      toast('error', getErrorMessage(e))
      setDetailData(null)
    } finally {
      setDetailLoading(false)
    }
  }

  if (loading) return <Spinner />

  const activeCount = orgs.filter((o) => o.is_active).length
  const totalCustomers = Object.values(customersByOrg).reduce((n, v) => n + v, 0)
  const totalConsents = Object.values(dashByOrg).reduce((n, d) => n + (d.total_consents || 0), 0)
  const oldestCreated = orgs.length
    ? orgs.reduce((min, o) => (new Date(o.created_at) < new Date(min) ? o.created_at : min), orgs[0].created_at)
    : null

  return (
    <div>
      <PageHeading
        title={t.pageTitle}
        subtitle={t.pageSubtitle}
        actions={
          <>
            <LanguageSelector value={lang} onChange={setLang} />
            {canManage && (
              <button className="btn btn-primary" onClick={openCreate}><IconPlus size={14} /> {t.newOrganization}</button>
            )}
          </>
        }
      />

      <div className="metric-grid mb">
        <MetricCard label={t.totalOrgs} value={orgs.length} icon={<IconShield size={20} />} tone="primary" sub={t.totalOrgsSub} />
        <MetricCard label={t.activeOrgs} value={activeCount} icon={<IconCheck size={20} />} tone="success" sub={`${orgs.length - activeCount} inactive`} />
        <MetricCard label={t.orgCustomers} value={totalCustomers} icon={<IconCustomers size={20} />} tone="warning" sub={t.orgCustomersSub} />
        <MetricCard label={t.orgConsents} value={totalConsents} icon={<IconAudit size={20} />} tone="info" sub={t.orgConsentsSub} />
      </div>

      {orgs.length === 0 ? (
        <div className="card">
          <div className="card-body">
            <EmptyState title={t.noOrgsTitle} message={t.noOrgsMsg} />
          </div>
        </div>
      ) : (
        <div className="grid-3 mb">
          {orgs.map((org) => {
            const dash = dashByOrg[org.id]
            const consentCount = dash?.total_consents ?? 0
            const activeConsentCount = dash?.active_consents ?? 0
            return (
              <div key={org.id} className="card card-hover" style={{ display: 'flex', flexDirection: 'column' }}>
                <div className="card-body" style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
                  <div className="flex-between" style={{ marginBottom: 10 }}>
                    <div className="flex" style={{ gap: 12, alignItems: 'center' }}>
                      <Avatar name={org.name} size={40} />
                      <span style={{ fontWeight: 600 }}>{org.name}</span>
                      <span className="chip mono">{org.code}</span>
                    </div>
                    <Badge status={org.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{org.is_active ? 'Active' : 'Inactive'}</Badge>
                  </div>

                  {org.description && (
                    <p className="text-sm text-secondary" style={{ marginBottom: 10 }}>{org.description}</p>
                  )}

                  {org.domain && (
                    <div className="flex" style={{ gap: 6, alignItems: 'center', marginBottom: 10 }}>
                      <IconGlobe size={13} />
                      <span className="text-sm text-muted">{org.domain}</span>
                    </div>
                  )}

                  <div className="flex" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                    <span className="chip"><IconCustomers size={12} /> {customersByOrg[org.code.toUpperCase()] || 0} customers</span>
                    <span className="chip" style={{ fontWeight: 600 }}>
                      <IconAudit size={12} /> {consentCount} consents ({activeConsentCount} active)
                    </span>
                  </div>

                  <div style={{ flex: 1 }} />

                  <div className="flex" style={{ gap: 8 }}>
                    <button className="btn btn-sm" onClick={() => openDetail(org)}><IconEye size={13} /> {t.viewDetails}</button>
                    {canManage && <button className="btn btn-sm btn-ghost" onClick={() => openEdit(org)}>{t.edit}</button>}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <FooterRow left={`Directory active: ${oldestCreated ? formatDate(oldestCreated) : '—'}`} />

      <Modal
        open={showOrgModal}
        title={editOrg ? `${t.modalEditTitle} ${editOrg.name}` : t.modalCreateTitle}
        onClose={closeOrgModal}
        footer={
          <>
            <button className="btn" onClick={closeOrgModal}>{t.cancel}</button>
            <button className="btn btn-primary" onClick={saveOrg}>{editOrg ? t.saveChanges : t.createOrganization}</button>
          </>
        }>
        <div className="form-group">
          <label>{t.nameLabel} *</label>
          <input className="input" placeholder="Acme Retail Group" value={orgForm.name} onChange={(e) => setOrgForm({ ...orgForm, name: e.target.value })} />
        </div>
        <div className="form-group">
          <label>{t.codeLabel} *</label>
          <input className="input" placeholder="ACME" value={orgForm.code} onChange={(e) => setOrgForm({ ...orgForm, code: e.target.value })} />
        </div>
        <div className="form-group">
          <label>{t.domainLabel}</label>
          <input className="input" placeholder="acme.example.com" value={orgForm.domain} onChange={(e) => setOrgForm({ ...orgForm, domain: e.target.value })} />
        </div>
        <div className="form-group">
          <label>{t.descriptionLabel}</label>
          <textarea className="textarea" rows={3} placeholder="What this organization does..." value={orgForm.description} onChange={(e) => setOrgForm({ ...orgForm, description: e.target.value })} />
        </div>
        {editOrg && (
          <label className="flex text-sm" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={orgActive} onChange={(e) => setOrgActive(e.target.checked)} /> {t.activeLabel}
          </label>
        )}
      </Modal>

      <Modal
        open={!!detailOrg}
        title={`${detailOrg?.name ?? ''} - ${t.detailTitle}`}
        onClose={() => { setDetailOrg(null); setDetailData(null) }}
        wide
        footer={
          <button className="btn" onClick={() => { setDetailOrg(null); setDetailData(null) }}>{t.cancel}</button>
        }>
        {detailLoading ? (
          <TableSkeleton rows={3} cols={4} />
        ) : !detailData ? (
          <EmptyState title="No data" message="Could not load details for this organization." />
        ) : (
          <>
            <div className="metric-grid mb">
              <MetricCard label={t.detailCustomers} value={detailData.total_customers} tone="primary" sub={detailOrg?.code?.toUpperCase() ?? ''} />
              <MetricCard label={t.detailConsents} value={detailData.total_consents} tone="info" sub="all time" />
              <MetricCard label={t.detailActiveConsents} value={detailData.active_consents} tone="success" sub="currently valid" />
            </div>

            {canManage && (
              <div className="card" style={{ marginBottom: 16 }}>
                <div className="card-header">{t.tenantSettingsTitle}</div>
                <div className="card-body">
                  <div className="grid-2">
                    <div className="form-group">
                      <label>{t.dpoNameLabel}</label>
                      <input className="input" value={tenantForm.dpo_name} onChange={(e) => setTenantForm({ ...tenantForm, dpo_name: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.dpoEmailLabel}</label>
                      <input className="input" type="email" value={tenantForm.dpo_email} onChange={(e) => setTenantForm({ ...tenantForm, dpo_email: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.dpoPhoneLabel}</label>
                      <input className="input" value={tenantForm.dpo_phone} onChange={(e) => setTenantForm({ ...tenantForm, dpo_phone: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.grievanceDaysLabel}</label>
                      <input className="input" type="number" min={1} max={90} value={tenantForm.grievance_response_days} onChange={(e) => setTenantForm({ ...tenantForm, grievance_response_days: Number(e.target.value) })} />
                    </div>
                    <div className="form-group">
                      <label>{t.withdrawUrlLabel}</label>
                      <input className="input" value={tenantForm.withdraw_url} onChange={(e) => setTenantForm({ ...tenantForm, withdraw_url: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.rightsUrlLabel}</label>
                      <input className="input" value={tenantForm.rights_url} onChange={(e) => setTenantForm({ ...tenantForm, rights_url: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.grievanceUrlLabel}</label>
                      <input className="input" value={tenantForm.grievance_url} onChange={(e) => setTenantForm({ ...tenantForm, grievance_url: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>{t.boardComplaintUrlLabel}</label>
                      <input className="input" value={tenantForm.board_complaint_url} onChange={(e) => setTenantForm({ ...tenantForm, board_complaint_url: e.target.value })} />
                    </div>
                  </div>
                  <button className="btn btn-primary" disabled={savingTenant} onClick={saveTenantSettings}>{t.saveTenantSettings}</button>
                </div>
              </div>
            )}

            {detailData.users && detailData.users.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <h4 style={{ marginBottom: 8, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  {t.orgUsersTable} ({detailData.users.length})
                </h4>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t.nameCol}</th>
                        <th>Username</th>
                        <th>Role</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailData.users.map((u: OrganizationUser) => (
                        <tr key={u.id}>
                          <td>
                            <div className="flex" style={{ gap: 10 }}>
                              <Avatar name={u.full_name || u.username || '?'} size={28} />
                              <span>{u.full_name}</span>
                            </div>
                          </td>
                          <td className="mono">{u.username}</td>
                          <td>
                            <span className={`badge ${u.role.includes('admin') ? 'b-success' : 'b-info'}`}>
                              {u.role.replace('_', ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())}
                            </span>
                          </td>
                          <td><Badge status={u.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{u.is_active ? 'Active' : 'Inactive'}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {detailData.portal_users && detailData.portal_users.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <h4 style={{ marginBottom: 8, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  {t.portalUsersTable} ({detailData.portal_users.length})
                </h4>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t.nameCol}</th>
                        <th>{t.emailCol}</th>
                        <th>{t.phoneCol}</th>
                        <th>{t.signupDateCol}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detailData.portal_users as Customer[]).map((c) => (
                        <tr key={c.id}>
                          <td>
                            <div className="flex" style={{ gap: 10 }}>
                              <Avatar name={c.name || '?'} size={28} />
                              <span>{c.name}</span>
                            </div>
                          </td>
                          <td className="mono">{c.email || '—'}</td>
                          <td>{c.phone || '—'}</td>
                          <td className="muted">{formatDateTime(c.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {detailData.portal_users && detailData.portal_users.length === 0 && (
              <div style={{ marginBottom: 16 }}>
                <p className="text-sm muted">{t.portalUsersEmpty}</p>
              </div>
            )}

            {detailData.consent_summary.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <h4 style={{ marginBottom: 8, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{t.consentStatusBreakdown}</h4>
                <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
                  {detailData.consent_summary.map((s) => (
                    <div key={s.status} className="card card-body" style={{ padding: '10px 16px', textAlign: 'center', minWidth: 100 }}>
                      <div className="text-xs text-muted">{s.status}</div>
                      <div style={{ fontWeight: 700, fontSize: '1.2rem' }}>{s.count}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {detailData.recent_activity.length > 0 && (
              <div>
                <h4 style={{ marginBottom: 8, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  {t.recentActivity} ({detailData.recent_activity.length})
                </h4>
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Reason</th></tr></thead>
                    <tbody>
                      {(detailData.recent_activity as AuditEvent[]).slice(0, 10).map((ev) => (
                        <tr key={ev.id}>
                          <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(ev.created_at)}</td>
                          <td className="mono" style={{ fontWeight: 600 }}>{ev.actor_username}</td>
                          <td><span className={`badge ${statusBadge(ev.event)}`}>{ev.event}</span></td>
                          <td className="muted">{ev.reason || '\u2014'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  )
}

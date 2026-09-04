import { useEffect, useState } from 'react'
import { tenantsApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, Modal, PageHead, Spinner, StatCard, formatDateTime, useToast,
} from '../components/ui'
import {
  IconCheck, IconCopy, IconKey, IconPlus, IconRefresh, IconShield, IconTrash, IconX,
} from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { ApiKey, ApiKeyCreateResponse, Tenant } from '../types'

const EMPTY_TENANT = { name: '', code: '', domain: '', default_language: 'en' }
const DEFAULT_SCOPES = 'integration.use,context.use'

export function TenantsPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('user.manage')

  const [tenants, setTenants] = useState<Tenant[]>([])
  const [loading, setLoading] = useState(true)

  const [showTenantModal, setShowTenantModal] = useState(false)
  const [tenantForm, setTenantForm] = useState(EMPTY_TENANT)

  const [detailTenant, setDetailTenant] = useState<Tenant | null>(null)
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  const [showKeyModal, setShowKeyModal] = useState(false)
  const [keyForm, setKeyForm] = useState({ name: '', scopes: DEFAULT_SCOPES, expires_in_days: '' })

  const [createdKey, setCreatedKey] = useState<string | null>(null)

  const [confirmRotate, setConfirmRotate] = useState<ApiKey | null>(null)
  const [confirmRevoke, setConfirmRevoke] = useState<ApiKey | null>(null)

  const load = async () => {
    const r = await tenantsApi.list()
    setTenants(r.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const loadKeys = async (tenantId: number) => {
    try {
      const r = await tenantsApi.listApiKeys(tenantId)
      setApiKeys(r.data)
    } catch (e) {
      toast('error', getErrorMessage(e))
      setApiKeys([])
    }
  }

  const openCreateTenant = () => {
    setTenantForm(EMPTY_TENANT)
    setShowTenantModal(true)
  }

  const saveTenant = async () => {
    if (!tenantForm.name.trim() || !tenantForm.code.trim()) {
      toast('warning', 'Name and code are required')
      return
    }
    try {
      await tenantsApi.create(tenantForm)
      toast('success', 'Tenant created')
      setShowTenantModal(false)
      await load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const openDetail = async (tenant: Tenant) => {
    setDetailTenant(tenant)
    setDetailLoading(true)
    setCreatedKey(null)
    try {
      await loadKeys(tenant.id)
    } finally {
      setDetailLoading(false)
    }
  }

  const closeDetail = () => {
    setDetailTenant(null)
    setApiKeys([])
    setCreatedKey(null)
  }

  const openCreateKey = () => {
    setKeyForm({ name: '', scopes: DEFAULT_SCOPES, expires_in_days: '' })
    setCreatedKey(null)
    setShowKeyModal(true)
  }

  const saveApiKey = async () => {
    if (!detailTenant) return
    try {
      const payload: { name?: string; scopes?: string; expires_in_days?: number | null } = {
        scopes: keyForm.scopes || undefined,
      }
      if (keyForm.name.trim()) payload.name = keyForm.name.trim()
      if (keyForm.expires_in_days) payload.expires_in_days = parseInt(keyForm.expires_in_days, 10)

      const r = await tenantsApi.createApiKey(detailTenant.id, payload)
      setCreatedKey(r.data.key)
      toast('success', 'API key created')
      setShowKeyModal(false)
      await loadKeys(detailTenant.id)
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const doRotate = async () => {
    if (!detailTenant || !confirmRotate) return
    try {
      const r = await tenantsApi.rotateApiKey(detailTenant.id, confirmRotate.id)
      setCreatedKey(r.data.key)
      toast('success', 'API key rotated')
      setConfirmRotate(null)
      await loadKeys(detailTenant.id)
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const doRevoke = async () => {
    if (!detailTenant || !confirmRevoke) return
    try {
      await tenantsApi.revokeApiKey(detailTenant.id, confirmRevoke.id)
      toast('success', 'API key revoked')
      setConfirmRevoke(null)
      await loadKeys(detailTenant.id)
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const copyKey = () => {
    if (!createdKey) return
    navigator.clipboard.writeText(createdKey).then(
      () => toast('success', 'Key copied to clipboard'),
      () => toast('error', 'Failed to copy'),
    )
  }

  if (loading) return <Spinner />

  const activeTenants = tenants.filter((t) => t.is_active).length

  return (
    <div>
      <PageHead
        title="Tenants & API Keys"
        subtitle="Manage tenant organizations and their integration API keys"
        actions={
          canManage && (
            <button className="btn btn-primary" onClick={openCreateTenant}><IconPlus size={14} /> New Tenant</button>
          )
        }
      />

      <div className="stat-grid mb">
        <StatCard label="Total Tenants" value={tenants.length} icon={<IconShield size={20} />} tone="primary" sub="registered organizations" />
        <StatCard label="Active Tenants" value={activeTenants} icon={<IconCheck size={20} />} tone="success" sub={`${tenants.length - activeTenants} inactive`} />
        <StatCard label="Total API Keys" value={tenants.reduce((n, t) => n + ((t as unknown as Record<string, unknown>).key_count as number ?? 0), 0)} icon={<IconKey size={20} />} tone="warning" sub="across all tenants" />
        <StatCard label="Active API Keys" value={tenants.reduce((n, t) => n + ((t as unknown as Record<string, unknown>).active_key_count as number ?? 0), 0)} icon={<IconRefresh size={20} />} tone="info" sub="currently valid" />
      </div>

      {tenants.length === 0 ? (
        <div className="card">
          <div className="card-body">
            <EmptyState title="No tenants" message="No tenant organizations have been created yet." />
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16, marginBottom: 24 }}>
          {tenants.map((tenant) => (
            <div key={tenant.id} className="card card-hover" style={{ display: 'flex', flexDirection: 'column' }}>
              <div className="card-body" style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
                <div className="flex-between" style={{ marginBottom: 10 }}>
                  <div className="flex" style={{ gap: 12 }}>
                    <span className="avatar-sm" style={{ background: 'var(--primary-soft)', color: 'var(--primary)' }}>
                      {(tenant.name || '?').slice(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <div style={{ fontWeight: 650, fontSize: '1.05rem' }}>{tenant.name}</div>
                      <div className="text-xs text-muted mono">{tenant.code}</div>
                    </div>
                  </div>
                  <Badge status={tenant.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{tenant.is_active ? 'Active' : 'Inactive'}</Badge>
                </div>

                {tenant.domain && (
                  <div className="text-sm text-muted" style={{ marginBottom: 10 }}>
                    {tenant.domain}
                  </div>
                )}

                <div style={{ flex: 1 }} />

                <div className="flex" style={{ gap: 8 }}>
                  <button className="btn btn-sm" onClick={() => openDetail(tenant)}><IconKey size={13} /> API Keys</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={showTenantModal}
        title="Create Tenant"
        onClose={() => setShowTenantModal(false)}
        footer={
          <>
            <button className="btn" onClick={() => setShowTenantModal(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={saveTenant}>Create Tenant</button>
          </>
        }>
        <div className="form-group">
          <label>Name *</label>
          <input className="input" placeholder="Acme Corp" value={tenantForm.name} onChange={(e) => setTenantForm({ ...tenantForm, name: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Code *</label>
          <input className="input" placeholder="ACME" value={tenantForm.code} onChange={(e) => setTenantForm({ ...tenantForm, code: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Domain</label>
          <input className="input" placeholder="acme.example.com" value={tenantForm.domain} onChange={(e) => setTenantForm({ ...tenantForm, domain: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Default Language</label>
          <select className="select" value={tenantForm.default_language} onChange={(e) => setTenantForm({ ...tenantForm, default_language: e.target.value })}>
            <option value="en">English</option>
            <option value="fr">French</option>
            <option value="de">German</option>
            <option value="es">Spanish</option>
            <option value="nl">Dutch</option>
          </select>
        </div>
      </Modal>

      <Modal
        open={!!detailTenant}
        title={detailTenant ? `${detailTenant.name} - API Keys` : ''}
        onClose={closeDetail}
        wide
        footer={
          <button className="btn" onClick={closeDetail}>Close</button>
        }>
        {detailLoading ? (
          <Spinner />
        ) : (
          <>
            <div className="detail-grid" style={{ marginBottom: 20 }}>
              <div className="detail-item">
                <div className="text-xs text-muted">Name</div>
                <div style={{ fontWeight: 600 }}>{detailTenant?.name}</div>
              </div>
              <div className="detail-item">
                <div className="text-xs text-muted">Code</div>
                <div className="mono">{detailTenant?.code}</div>
              </div>
              <div className="detail-item">
                <div className="text-xs text-muted">Domain</div>
                <div>{detailTenant?.domain || '—'}</div>
              </div>
              <div className="detail-item">
                <div className="text-xs text-muted">Default Language</div>
                <div>{detailTenant?.default_language || 'en'}</div>
              </div>
            </div>

            {createdKey && (
              <div style={{
                padding: '14px 16px', marginBottom: 16, borderRadius: 8,
                background: 'var(--warning-soft, #fef3cd)', border: '1px solid var(--warning, #e0a800)',
              }}>
                <div style={{ fontWeight: 650, fontSize: '0.85rem', marginBottom: 6 }}>
                  Save this key now - it will not be shown again
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code className="mono" style={{ flex: 1, fontSize: '0.85rem', wordBreak: 'break-all' }}>{createdKey}</code>
                  <button className="btn btn-sm" onClick={copyKey}><IconCopy size={13} /> Copy</button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setCreatedKey(null)}><IconX size={13} /></button>
                </div>
              </div>
            )}

            <div className="flex-between" style={{ marginBottom: 12 }}>
              <h4 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>API Keys ({apiKeys.length})</h4>
              {canManage && (
                <button className="btn btn-sm btn-primary" onClick={openCreateKey}><IconPlus size={13} /> Create API Key</button>
              )}
            </div>

            {apiKeys.length === 0 ? (
              <EmptyState title="No API keys" message="Create an API key to enable integration access." />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Scopes</th>
                      <th>Created</th>
                      <th>Expires</th>
                      <th>Last Used</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {apiKeys.map((key) => (
                      <tr key={key.id}>
                        <td style={{ fontWeight: 600 }}>{key.name || <span className="muted">Unnamed</span>}</td>
                        <td className="mono text-xs">{key.scopes}</td>
                        <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(key.created_at)}</td>
                        <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(key.expires_at)}</td>
                        <td className="muted" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(key.last_used_at)}</td>
                        <td>
                          <Badge status={key.is_active && !key.revoked_at ? 'ACTIVE' : 'WITHDRAWN'}>
                            {key.revoked_at ? 'Revoked' : key.is_active ? 'Active' : 'Inactive'}
                          </Badge>
                        </td>
                        <td>
                          <div className="flex" style={{ gap: 6 }}>
                            {canManage && key.is_active && !key.revoked_at && (
                              <>
                                <button className="btn btn-sm btn-ghost" onClick={() => setConfirmRotate(key)} title="Rotate key">
                                  <IconRefresh size={13} />
                                </button>
                                <button className="btn btn-sm btn-ghost" onClick={() => setConfirmRevoke(key)} title="Revoke key" style={{ color: 'var(--danger)' }}>
                                  <IconTrash size={13} />
                                </button>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Modal>

      <Modal
        open={showKeyModal}
        title="Create API Key"
        onClose={() => setShowKeyModal(false)}
        footer={
          <>
            <button className="btn" onClick={() => setShowKeyModal(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={saveApiKey}>Create Key</button>
          </>
        }>
        <div className="form-group">
          <label>Name</label>
          <input className="input" placeholder="Production integration" value={keyForm.name} onChange={(e) => setKeyForm({ ...keyForm, name: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Scopes</label>
          <input className="input mono" placeholder={DEFAULT_SCOPES} value={keyForm.scopes} onChange={(e) => setKeyForm({ ...keyForm, scopes: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Expires in days (optional)</label>
          <input className="input" type="number" placeholder="Leave empty for no expiration" value={keyForm.expires_in_days} onChange={(e) => setKeyForm({ ...keyForm, expires_in_days: e.target.value })} />
        </div>
      </Modal>

      <Modal
        open={!!confirmRotate}
        title="Rotate API Key"
        onClose={() => setConfirmRotate(null)}
        footer={
          <>
            <button className="btn" onClick={() => setConfirmRotate(null)}>Cancel</button>
            <button className="btn btn-primary" onClick={doRotate}>Rotate</button>
          </>
        }>
        <p>Rotating this key will immediately invalidate the old key and generate a new one. This action cannot be undone.</p>
        {confirmRotate && <p className="mono text-sm">{confirmRotate.name || `Key #${confirmRotate.id}`}</p>}
      </Modal>

      <Modal
        open={!!confirmRevoke}
        title="Revoke API Key"
        onClose={() => setConfirmRevoke(null)}
        footer={
          <>
            <button className="btn" onClick={() => setConfirmRevoke(null)}>Cancel</button>
            <button className="btn btn-danger" onClick={doRevoke}>Revoke</button>
          </>
        }>
        <p>Revoking this key will immediately disable it. This action cannot be undone.</p>
        {confirmRevoke && <p className="mono text-sm">{confirmRevoke.name || `Key #${confirmRevoke.id}`}</p>}
      </Modal>
    </div>
  )
}

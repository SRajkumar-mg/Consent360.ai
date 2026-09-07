/**
 * Administration: staff users, roles and the periodic access review.
 *
 * R2-08 adds the two halves that were missing: role management
 * (`POST/PUT/DELETE /admin/roles`) and the R3-02/R-04 access review.
 *
 * Roles come in two kinds and the screen keeps them apart, because they are
 * governed differently. **System roles** (admin, dpo, auditor, operator,
 * viewer, the org-scoped *_admin ones) are defined in `app/core/rbac.py` and
 * re-synced into the database at startup; the API refuses to edit or delete
 * them, and editing them here would be undone by the next seed. **Custom
 * roles** are rows this screen owns. Presenting both as equally editable would
 * invite a change that silently reverts.
 *
 * The permission list offered by the editor is derived from the roles the API
 * actually returns rather than hardcoded here, so it cannot drift from
 * `ALL_PERMISSIONS` the way a second copy would.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { adminApi, rolesApi } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Avatar, Badge, EmptyState, FooterRow, Modal, PageHeading, Spinner, formatDate, formatDateTime, useToast,
} from '../components/ui'
import { RefusalNotice, Tabs } from '../components/GuardedAction'
import { IconAlert, IconPlus, IconShield } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import type { AccessReview, AdminUser, Role } from '../types'

type Tab = 'users' | 'roles' | 'review'

/** Grouping only affects presentation; the strings themselves come from the API. */
function permGroup(perm: string): string {
  const head = perm.split('.')[0]
  return ({
    dashboard: 'Dashboards', customer: 'Principals', consent: 'Consent', purpose: 'Purposes',
    category: 'Purposes', activity: 'Purposes', policy: 'Policies & processors', audit: 'Audit & evidence',
    user: 'Administration', integration: 'Integration', context: 'Integration',
    grievance: 'Rights & grievances', breach: 'Breach register', erasure: 'Retention & erasure',
  } as Record<string, string>)[head] || 'Other'
}

export function AdministrationPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('user.manage')

  const [tab, setTab] = useState<Tab>('users')
  const [roles, setRoles] = useState<Role[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [review, setReview] = useState<AccessReview | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')

  const [newUser, setNewUser] = useState({ username: '', full_name: '', email: '', password: '', role_id: 0, is_active: true })
  const [showUserModal, setShowUserModal] = useState(false)

  const [roleForm, setRoleForm] = useState<null | { id: number | null; name: string; description: string; permissions: string[] }>(null)

  const load = useCallback(async () => {
    const [r, u] = await Promise.all([adminApi.roles(), adminApi.users()])
    setRoles(r.data); setUsers(u.data)
    const rev = await rolesApi.accessReview().catch(() => ({ data: null }))
    setReview(rev.data as AccessReview | null)
  }, [])

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [load])

  const run = async (fn: () => Promise<unknown>, message: string): Promise<boolean> => {
    setBusy(true); setRefusal('')
    try { await fn(); toast('success', message); await load(); return true }
    catch (e) { setRefusal(getErrorMessage(e)); return false }
    finally { setBusy(false) }
  }

  /** Every permission the API has ever handed back, deduped and grouped. The
   *  admin role carries ALL_PERMISSIONS, so this is the real list rather than a
   *  copy of rbac.py that could quietly fall behind it. */
  const allPermissions = useMemo(() => {
    const set = new Set<string>()
    for (const r of roles) for (const p of r.permissions) if (p !== '*') set.add(p)
    for (const r of review?.roles || []) for (const p of r.permissions) if (p !== '*') set.add(p)
    return [...set].sort()
  }, [roles, review])

  const grouped = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const p of allPermissions) {
      const g = permGroup(p)
      map.set(g, [...(map.get(g) || []), p])
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [allPermissions])

  const usersByRole = useMemo(() => {
    const map = new Map<number, number>()
    for (const u of users) map.set(u.role_id, (map.get(u.role_id) || 0) + 1)
    return map
  }, [users])

  const createUser = async () => {
    const ok = await run(() => adminApi.createUser(newUser), 'User created')
    if (ok) {
      setShowUserModal(false)
      setNewUser({ username: '', full_name: '', email: '', password: '', role_id: 0, is_active: true })
    }
  }

  const toggleUser = (u: AdminUser) =>
    run(() => adminApi.updateUser(u.id, { is_active: !u.is_active }), `User ${u.username} ${u.is_active ? 'disabled' : 'enabled'}`)

  if (loading) return <Spinner />

  const oldestUserDate = users.reduce<string | null>(
    (oldest, u) => (!oldest || u.created_at < oldest ? u.created_at : oldest),
    null,
  )
  const neverLoggedIn = (review?.users || []).filter((u) => u.is_active && !u.last_login_at)
  const inactive = (review?.users || []).filter((u) => !u.is_active)

  return (
    <div>
      <PageHeading title="Administration" subtitle="Staff users, the roles that grant them permissions, and the periodic access review" />

      <RefusalNotice title="The administration API refused this" detail={refusal} onDismiss={() => setRefusal('')} />

      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'users', label: 'Platform users', count: users.length },
          { id: 'roles', label: 'Roles & permissions', count: roles.length },
          { id: 'review', label: 'Access review', count: review?.users.length ?? null },
        ]}
      />

      {tab === 'users' && (
        <div className="card">
          <div className="card-header">
            <div className="flex" style={{ gap: 10 }}>
              <h3>Platform Users</h3>
              <span className="chip">{users.length} Total</span>
            </div>
            {canManage && <button className="btn btn-primary btn-sm" onClick={() => setShowUserModal(true)}><IconPlus size={13} /> New user</button>}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>User</th><th>Name</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div className="flex" style={{ gap: 10 }}>
                        <Avatar name={u.full_name || u.username} size={28} />
                        <span className="mono">{u.username}</span>
                      </div>
                    </td>
                    <td>{u.full_name}</td>
                    <td><span className="badge b-primary">{u.role_name.replace(/_/g, ' ')}</span></td>
                    <td><Badge status={u.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{u.is_active ? 'Active' : 'Disabled'}</Badge></td>
                    <td>
                      {canManage && (u.is_active ? (
                        <button className="btn btn-ghost-danger btn-sm" disabled={busy} onClick={() => toggleUser(u)}>Disable</button>
                      ) : (
                        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => toggleUser(u)}>Enable</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'roles' && (
        <>
          <div className="card mb">
            <div className="card-header">
              <div className="flex" style={{ gap: 10 }}>
                <h3>Roles &amp; permissions</h3>
                <span className="chip">{roles.length} defined</span>
              </div>
              {canManage && (
                <button className="btn btn-primary btn-sm" onClick={() => setRoleForm({ id: null, name: '', description: '', permissions: [] })}>
                  <IconPlus size={13} /> New custom role
                </button>
              )}
            </div>
            <div className="card-body">
              <p className="text-sm text-secondary" style={{ margin: 0 }}>
                <b>System roles are not editable here.</b> They are defined in <span className="mono">app/core/rbac.py</span> and
                re-synced into the database at every startup, so a change made here would be silently reverted — the API
                refuses it for that reason. Custom roles are owned by this screen. Changing a role&rsquo;s permissions takes
                effect the next time the affected user&rsquo;s token is refreshed.
              </p>
            </div>
          </div>

          {roles.map((r) => (
            <div key={r.id} className="card mb">
              <div className="card-header">
                <div className="flex" style={{ gap: 10 }}>
                  <h3>{r.name.replace(/_/g, ' ')}</h3>
                  {r.is_system
                    ? <span className="chip" title="Defined in rbac.py and re-synced at startup"><IconShield size={12} /> System</span>
                    : <span className="chip">Custom</span>}
                  <span className="text-xs text-muted">{r.permissions.length} permissions · {usersByRole.get(r.id) || 0} user(s)</span>
                </div>
                {canManage && !r.is_system && (
                  <div className="flex" style={{ gap: 6 }}>
                    <button className="btn btn-sm" onClick={() => setRoleForm({ id: r.id, name: r.name, description: r.description, permissions: [...r.permissions] })}>Edit</button>
                    <button className="btn btn-ghost-danger btn-sm" disabled={busy || (usersByRole.get(r.id) || 0) > 0}
                      title={(usersByRole.get(r.id) || 0) > 0 ? 'Still assigned to a staff user' : undefined}
                      onClick={() => run(() => rolesApi.remove(r.id), `Role ${r.name} deleted.`)}>Delete</button>
                  </div>
                )}
              </div>
              <div className="card-body">
                <div className="text-sm text-secondary">{r.description || <span className="text-muted">No description</span>}</div>
                <div className="chip-group mt-sm">
                  {r.permissions.length === 0
                    ? <span className="text-muted text-sm">No permissions — this role can sign in and see nothing.</span>
                    : r.permissions.map((p) => <span key={p} className="chip mono">{p}</span>)}
                </div>
              </div>
            </div>
          ))}
        </>
      )}

      {tab === 'review' && (
        !review ? (
          <EmptyState title="Access review unavailable" message="The access review requires the user.manage permission." />
        ) : (
          <>
            {(neverLoggedIn.length > 0 || inactive.length > 0) && (
              <div className="alert alert-info mb">
                <IconAlert size={15} />{' '}
                <b>Worth a second look.</b>{' '}
                {neverLoggedIn.length > 0 && <>{neverLoggedIn.length} active account{neverLoggedIn.length === 1 ? ' has' : 's have'} never signed in ({neverLoggedIn.map((u) => u.username).join(', ')}). </>}
                {inactive.length > 0 && <>{inactive.length} account{inactive.length === 1 ? ' is' : 's are'} disabled but still on the register.</>}
              </div>
            )}
            <div className="card">
              <div className="card-header">
                <h3>Access review</h3>
                <span className="text-sm text-muted">Generated {formatDateTime(review.generated_at)}</span>
              </div>
              <div className="card-body">
                <p className="text-sm text-secondary">
                  Every staff account with the permissions its role actually grants — the join a reviewer would
                  otherwise have to do by hand between the user list and the role list. Reading this page is itself
                  audited.
                </p>
              </div>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>User</th><th>Role</th><th>Permissions</th><th>Status</th><th>Last sign-in</th><th>Created</th></tr></thead>
                  <tbody>
                    {review.users.map((u) => (
                      <tr key={u.username}>
                        <td><b className="mono">{u.username}</b><div className="text-xs text-muted">{u.full_name}</div></td>
                        <td>{u.role || <span className="text-danger">no role</span>}</td>
                        <td className="text-xs">
                          {u.permissions.length}
                          <div className="text-muted">{u.permissions.slice(0, 6).join(', ')}{u.permissions.length > 6 ? ` +${u.permissions.length - 6}` : ''}</div>
                        </td>
                        <td><Badge status={u.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{u.is_active ? 'Active' : 'Disabled'}</Badge></td>
                        <td className="text-xs">{u.last_login_at ? formatDateTime(u.last_login_at) : <span className="text-danger">never</span>}</td>
                        <td className="text-xs">{formatDate(u.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )
      )}

      <FooterRow left={`Directory active: ${formatDate(oldestUserDate)}`} />

      <Modal open={showUserModal} title="Create User" onClose={() => setShowUserModal(false)}
        footer={<><button className="btn" onClick={() => setShowUserModal(false)}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={createUser}>Create user</button></>}>
        <div className="form-group"><label>Username</label><input className="input" value={newUser.username} onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} /></div>
        <div className="form-group"><label>Full name</label><input className="input" value={newUser.full_name} onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })} /></div>
        <div className="form-group"><label>Email</label><input className="input" type="email" value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} /></div>
        <div className="form-group"><label>Password</label><input className="input" type="text" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} /></div>
        <div className="form-group"><label>Role</label>
          <select className="select" value={newUser.role_id} onChange={(e) => setNewUser({ ...newUser, role_id: Number(e.target.value) })}>
            <option value={0}>Select…</option>
            {roles.map((r) => <option key={r.id} value={r.id}>{r.name.replace(/_/g, ' ')}</option>)}
          </select>
        </div>
      </Modal>

      <Modal open={!!roleForm} wide title={roleForm?.id ? `Edit role — ${roleForm.name}` : 'New custom role'} onClose={() => setRoleForm(null)}
        footer={
          <>
            <button className="btn" onClick={() => setRoleForm(null)} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !roleForm?.name} onClick={async () => {
              if (!roleForm) return
              const ok = roleForm.id
                ? await run(() => rolesApi.update(roleForm.id as number, { description: roleForm.description, permissions: roleForm.permissions }), 'Role updated.')
                : await run(() => rolesApi.create({ name: roleForm.name, description: roleForm.description, permissions: roleForm.permissions }), 'Role created.')
              if (ok) setRoleForm(null)
            }}>Save</button>
          </>
        }>
        {roleForm && (
          <>
            <div className="form-row">
              <div className="form-group">
                <label>Name</label>
                <input className="input mono" value={roleForm.name} disabled={!!roleForm.id}
                  onChange={(e) => setRoleForm({ ...roleForm, name: e.target.value })} placeholder="lowercase_with_underscores" />
                {!roleForm.id && <div className="text-xs text-muted" style={{ marginTop: 4 }}>Lower-case letters, digits and underscores; must start with a letter.</div>}
              </div>
              <div className="form-group">
                <label>Description</label>
                <input className="input" value={roleForm.description} onChange={(e) => setRoleForm({ ...roleForm, description: e.target.value })} />
              </div>
            </div>

            <h4 className="kpi-section-title">Permissions ({roleForm.permissions.length} of {allPermissions.length})</h4>
            <p className="text-sm text-secondary">
              Two of these deliberately carry more weight than the rest: <span className="mono">breach.manage</span> files
              with a regulator, and <span className="mono">erasure.manage</span> destroys a principal&rsquo;s data. In the
              built-in roles both are held only by <b>admin</b> and <b>dpo</b>.
            </p>
            {grouped.map(([group, perms]) => (
              <div className="perm-group" key={group}>
                <div className="perm-group-title">{group}</div>
                <div className="perm-grid">
                  {perms.map((p) => {
                    const on = roleForm.permissions.includes(p)
                    const heavy = p === 'breach.manage' || p === 'erasure.manage'
                    return (
                      <label key={p} className={`perm-item${on ? ' on' : ''}${heavy ? ' heavy' : ''}`}>
                        <input type="checkbox" checked={on} onChange={(e) => setRoleForm({
                          ...roleForm,
                          permissions: e.target.checked
                            ? [...roleForm.permissions, p]
                            : roleForm.permissions.filter((x) => x !== p),
                        })} />
                        <span className="mono">{p}</span>
                      </label>
                    )
                  })}
                </div>
              </div>
            ))}
          </>
        )}
      </Modal>
    </div>
  )
}

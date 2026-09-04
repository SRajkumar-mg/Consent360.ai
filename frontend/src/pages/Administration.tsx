import { useEffect, useState } from 'react'
import { adminApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, Modal, PageHead, Spinner, useToast } from '../components/ui'
import { IconPlus } from '../components/icons'
import type { AdminUser, Role } from '../types'

function roleTone(role: string): string {
  if (role === 'admin' || role === 'consent_admin' || role.includes('privacy')) return 'b-success'
  if (role.includes('auditor')) return 'b-info'
  if (role.includes('customer') || role.includes('service')) return 'b-purple'
  if (role.includes('consent')) return 'b-primary'
  return 'b-info'
}

export function AdministrationPage() {
  const toast = useToast()
  const [roles, setRoles] = useState<Role[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)

  const [newUser, setNewUser] = useState({ username: '', full_name: '', email: '', password: '', role_id: 0, is_active: true })
  const [showUserModal, setShowUserModal] = useState(false)

  const load = async () => {
    const [r, u] = await Promise.all([adminApi.roles(), adminApi.users()])
    setRoles(r.data); setUsers(u.data)
  }

  useEffect(() => {
    setLoading(true)
    load().catch((e) => toast('error', getErrorMessage(e))).finally(() => setLoading(false))
  }, [])

  const createUser = async () => {
    try {
      await adminApi.createUser(newUser)
      toast('success', 'User created')
      setShowUserModal(false)
      setNewUser({ username: '', full_name: '', email: '', password: '', role_id: 0, is_active: true })
      adminApi.users().then((r) => setUsers(r.data))
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const toggleUser = async (u: AdminUser) => {
    try {
      await adminApi.updateUser(u.id, { is_active: !u.is_active })
      toast('success', `User ${u.username} ${u.is_active ? 'disabled' : 'enabled'}`)
      adminApi.users().then((r) => setUsers(r.data))
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead title="Administration" subtitle="Users, roles, and permissions" />

      <div className="grid-2" style={{ gridTemplateColumns: '1.4fr 1fr' }}>
        <div className="card">
          <div className="card-header">
            <h3>Users</h3>
            <button className="btn btn-primary btn-sm" onClick={() => setShowUserModal(true)}><IconPlus size={13} /> New user</button>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>User</th><th>Name</th><th>Role</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div className="flex" style={{ gap: 10 }}>
                        <span className="avatar-sm">{(u.full_name || u.username).slice(0, 2).toUpperCase()}</span>
                        <span className="mono">{u.username}</span>
                      </div>
                    </td>
                    <td>{u.full_name}</td>
                    <td><span className={`badge ${roleTone(u.role_name)}`}>{u.role_name.replace(/_/g, ' ')}</span></td>
                    <td><Badge status={u.is_active ? 'ACTIVE' : 'WITHDRAWN'}>{u.is_active ? 'Active' : 'Disabled'}</Badge></td>
                    <td>
                      <button className="btn btn-sm btn-ghost" onClick={() => toggleUser(u)}>
                        {u.is_active ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><h3>Roles & Permissions</h3></div>
          <div className="card-body">
            {roles.map((r) => (
              <div key={r.id} className="mb" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
                <div className="flex-between">
                  <b>{r.name.replace(/_/g, ' ')}</b>
                  <span className="text-xs text-muted">{r.permissions.length} permissions</span>
                </div>
                <div className="text-xs text-secondary mt-sm">{r.description}</div>
                <div className="flex mt-sm" style={{ flexWrap: 'wrap', gap: 4 }}>
                  {r.permissions.slice(0, 6).map((p) => <span key={p} className="badge b-info">{p.replace(/\./g, ' ')}</span>)}
                  {r.permissions.length > 6 && <span className="text-xs text-muted">+{r.permissions.length - 6} more</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Modal open={showUserModal} title="Create User" onClose={() => setShowUserModal(false)}
        footer={<><button className="btn" onClick={() => setShowUserModal(false)}>Cancel</button><button className="btn btn-primary" onClick={createUser}>Create user</button></>}>
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
    </div>
  )
}
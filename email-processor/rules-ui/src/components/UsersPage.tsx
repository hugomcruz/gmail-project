import { useEffect, useState } from 'react'
import type { UserInfo, UserCreatePayload, UserUpdatePayload } from '../api'
import { getUsers, createUser, updateUser, deleteUser } from '../api'

interface Props {
  showToast: (msg: string) => void
  currentUserId: number
}

interface FormState {
  username: string
  password: string
  role: 'admin' | 'viewer'
  is_active: boolean
}

const EMPTY_FORM: FormState = { username: '', password: '', role: 'viewer', is_active: true }

export default function UsersPage({ showToast, currentUserId }: Props) {
  const [users, setUsers]       = useState<UserInfo[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [modal, setModal]       = useState<'create' | UserInfo | null>(null)
  const [form, setForm]         = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving]     = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const load = async () => {
    try {
      setError(null)
      const data = await getUsers()
      setUsers(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setFormError(null)
    setModal('create')
  }

  const openEdit = (user: UserInfo) => {
    setForm({ username: user.username, password: '', role: user.role, is_active: user.is_active })
    setFormError(null)
    setModal(user)
  }

  const closeModal = () => setModal(null)

  const handleSave = async () => {
    setFormError(null)
    setSaving(true)
    try {
      if (modal === 'create') {
        if (!form.password) { setFormError('Password is required.'); return }
        const payload: UserCreatePayload = {
          username: form.username,
          password: form.password,
          role: form.role,
          is_active: form.is_active,
        }
        const created = await createUser(payload)
        setUsers(u => [...u, created])
        showToast(`User "${created.username}" created.`)
      } else if (modal !== null) {
        const payload: UserUpdatePayload = {
          username: form.username,
          role: form.role,
          is_active: form.is_active,
        }
        if (form.password) payload.password = form.password
        const updated = await updateUser((modal as UserInfo).id, payload)
        setUsers(u => u.map(x => x.id === updated.id ? updated : x))
        showToast(`User "${updated.username}" updated.`)
      }
      closeModal()
    } catch (e: unknown) {
      const msg = String(e)
      if (msg.includes('409')) {
        setFormError('Username already exists.')
      } else {
        setFormError(msg)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (user: UserInfo) => {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return
    try {
      await deleteUser(user.id)
      setUsers(u => u.filter(x => x.id !== user.id))
      showToast(`User "${user.username}" deleted.`)
    } catch (e) {
      showToast(`Error: ${e}`)
    }
  }

  if (loading) return (
    <div className="flex items-center justify-center py-24 text-gray-400 text-sm">Loading…</div>
  )

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-white">
          Users
          {users.length > 0 && (
            <span className="ml-2 text-sm font-normal text-gray-500">{users.length} total</span>
          )}
        </h2>
        <button
          onClick={openCreate}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors"
        >
          + New User
        </button>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
      )}

      {/* Table */}
      {users.length === 0 ? (
        <div className="text-center py-24 text-gray-500">
          <p className="text-lg">No users yet.</p>
        </div>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wide">
                <th className="text-left px-5 py-3">Username</th>
                <th className="text-left px-5 py-3">Role</th>
                <th className="text-left px-5 py-3">Status</th>
                <th className="text-left px-5 py-3">Created</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody>
              {users.map(user => (
                <tr key={user.id} className="border-b border-gray-800/50 last:border-0 hover:bg-gray-800/30 transition-colors">
                  <td className="px-5 py-3.5 text-white font-medium">
                    {user.username}
                    {user.id === currentUserId && (
                      <span className="ml-2 text-xs text-gray-500">(you)</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                      user.role === 'admin'
                        ? 'bg-purple-900/60 text-purple-300'
                        : 'bg-gray-700 text-gray-300'
                    }`}>
                      {user.role}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                      user.is_active
                        ? 'bg-green-900/50 text-green-300'
                        : 'bg-red-900/40 text-red-400'
                    }`}>
                      {user.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-gray-400">
                    {new Date(user.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => openEdit(user)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
                      >
                        Edit
                      </button>
                      {user.id !== currentUserId && (
                        <button
                          onClick={() => handleDelete(user)}
                          className="px-3 py-1.5 text-xs rounded-lg bg-red-900/50 hover:bg-red-800/70 text-red-300 transition-colors"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal */}
      {modal !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-md mx-4 p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-white mb-5">
              {modal === 'create' ? 'Create User' : `Edit User — ${(modal as UserInfo).username}`}
            </h3>

            {formError && (
              <div className="mb-4 px-3 py-2.5 bg-red-900/40 border border-red-700/60 rounded-lg text-red-300 text-sm">
                {formError}
              </div>
            )}

            <div className="space-y-4">
              {/* Username */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Username</label>
                <input
                  type="text"
                  value={form.username}
                  onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                  className="w-full px-3.5 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="johndoe"
                />
              </div>

              {/* Password */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">
                  Password
                  {modal !== 'create' && (
                    <span className="ml-1 text-gray-600 font-normal">(leave blank to keep current)</span>
                  )}
                </label>
                <input
                  type="password"
                  value={form.password}
                  onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                  className="w-full px-3.5 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="••••••••"
                />
              </div>

              {/* Role */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Role</label>
                <select
                  value={form.role}
                  onChange={e => setForm(f => ({ ...f, role: e.target.value as 'admin' | 'viewer' }))}
                  className="w-full px-3.5 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  <option value="viewer">Viewer — read-only access</option>
                  <option value="admin">Admin — full access</option>
                </select>
              </div>

              {/* Active toggle */}
              <div className="flex items-center justify-between py-1">
                <span className="text-sm text-gray-300">Active</span>
                <button
                  type="button"
                  onClick={() => setForm(f => ({ ...f, is_active: !f.is_active }))}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${form.is_active ? 'bg-blue-600' : 'bg-gray-700'}`}
                >
                  <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${form.is_active ? 'translate-x-6' : 'translate-x-1'}`} />
                </button>
              </div>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={closeModal}
                className="px-4 py-2 text-sm rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !form.username}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium transition-colors"
              >
                {saving ? 'Saving…' : modal === 'create' ? 'Create' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

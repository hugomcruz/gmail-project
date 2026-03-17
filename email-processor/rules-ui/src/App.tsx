import React, { useEffect, useState } from 'react'
import type { Rule, ConditionType, ActionType, Connection, UserInfo } from './api'
import {
  getRules, deleteRule, toggleRule, reloadEngine,
  getConditionTypes, getActionTypes, getConnections,
  getMe, clearAuth, getStoredToken, getStoredUser,
} from './api'
import RuleEditor from './components/RuleEditor'
import ConnectionsPage from './components/ConnectionsPage'
import LoginPage from './components/LoginPage'
import UsersPage from './components/UsersPage'
import ActivityPage from './components/ActivityPage'
import HeatLogo from './components/HeatLogo'

type Tab = 'rules' | 'connections' | 'users' | 'activity'

export default function App() {
  // ── Auth state ──────────────────────────────────────────────────────────
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(null)
  const [authChecked, setAuthChecked] = useState(false)

  // ── App state ───────────────────────────────────────────────────────────
  const [tab, setTab] = useState<Tab>('rules')
  const [rules, setRules] = useState<Rule[]>([])
  const [conditionTypes, setConditionTypes] = useState<ConditionType[]>([])
  const [actionTypes, setActionTypes] = useState<ActionType[]>([])
  const [connections, setConnections] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [editing, setEditing] = useState<Rule | null | 'new'>(null)

  // ── Verify stored token on mount ────────────────────────────────────────
  useEffect(() => {
    const token = getStoredToken()
    const stored = getStoredUser()
    if (!token || !stored) {
      setAuthChecked(true)
      return
    }
    // Validate token is still valid
    setCurrentUser(stored)
    getMe()
      .then(user => setCurrentUser(user))
      .catch(() => {
        clearAuth()
        setCurrentUser(null)
      })
      .finally(() => setAuthChecked(true))
  }, [])

  const handleLogin = (user: UserInfo) => setCurrentUser(user)

  const handleLogout = () => {
    clearAuth()
    setCurrentUser(null)
    setRules([])
    setLoading(true)
  }

  // ── Load data once authenticated ────────────────────────────────────────
  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  const loadConnections = async () => {
    try {
      setConnections(await getConnections())
    } catch { /* non-fatal */ }
  }

  const load = async () => {
    try {
      const [r, ct, at, cx] = await Promise.all([
        getRules(), getConditionTypes(), getActionTypes(), getConnections(),
      ])
      setRules(r)
      setConditionTypes(ct)
      setActionTypes(at)
      setConnections(cx)
      setError(null)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (currentUser) load()
  }, [currentUser])

  const handleToggle = async (id: number) => {
    const updated = await toggleRule(id)
    setRules(rs => rs.map(r => r.id === id ? updated : r))
  }

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete rule "${name}"?`)) return
    await deleteRule(id)
    setRules(rs => rs.filter(r => r.id !== id))
    showToast(`Rule "${name}" deleted.`)
  }

  const handleReload = async () => {
    try {
      const res = await reloadEngine()
      showToast(res.message)
    } catch (e: unknown) {
      showToast(`Reload failed: ${String(e)}`)
    }
  }

  const handleSaved = async () => {
    setEditing(null)
    await load()
    showToast('Rule saved.')
  }

  // ── Auth gate ────────────────────────────────────────────────────────────
  if (!authChecked) return (
    <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">Loading…</div>
  )

  if (!currentUser) return <LoginPage onLogin={handleLogin} />

  if (loading) return (
    <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">Loading…</div>
  )

  const isAdmin = currentUser.role === 'admin'

  const navItems: { id: Tab; label: string; icon: React.ReactNode; adminOnly?: boolean }[] = [
    {
      id: 'rules', label: 'Rules',
      icon: (
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
          <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd" />
        </svg>
      ),
    },
    {
      id: 'connections', label: 'Connections',
      icon: (
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
          <path d="M5 4a1 1 0 00-2 0v7.268a2 2 0 000 3.464V16a1 1 0 102 0v-1.268a2 2 0 000-3.464V4zM11 4a1 1 0 10-2 0v1.268a2 2 0 000 3.464V16a1 1 0 102 0V8.732a2 2 0 000-3.464V4zM16 3a1 1 0 011 1v7.268a2 2 0 010 3.464V16a1 1 0 11-2 0v-1.268a2 2 0 010-3.464V4a1 1 0 011-1z" />
        </svg>
      ),
    },
    {
      id: 'users', label: 'Users', adminOnly: true,
      icon: (
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
          <path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z" />
        </svg>
      ),
    },
    {
      id: 'activity', label: 'Activity',
      icon: (
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
          <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clipRule="evenodd" />
        </svg>
      ),
    },
  ]

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100 overflow-hidden">

      {/* ── Left sidebar ───────────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
        {/* Logo / title */}
        <div className="px-4 py-4 border-b border-gray-800">
          <HeatLogo size="sm" withText layout="row" />
          <p className="text-xs text-gray-500 mt-2 capitalize pl-0.5">{currentUser.role}</p>
        </div>

        {/* Nav items */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems
            .filter(item => !item.adminOnly || isAdmin)
            .map(item => (
              <button
                key={item.id}
                onClick={() => setTab(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left ${
                  tab === item.id
                    ? 'bg-gray-800 text-white'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
                }`}
              >
                <span className="flex w-4 h-4 items-center justify-center shrink-0">{item.icon}</span>
                {item.label}
              </button>
            ))}
        </nav>

        {/* User info + logout */}
        <div className="px-4 py-4 border-t border-gray-800">
          <div className="flex items-center gap-2.5 mb-3">
            <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-xs font-bold text-white shrink-0">
              {currentUser.username[0].toUpperCase()}
            </div>
            <span className="text-sm text-gray-300 truncate">{currentUser.username}</span>
          </div>
          <button
            onClick={handleLogout}
            className="w-full text-left px-3 py-2 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 rounded-lg transition-colors"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Main content ───────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        <main className="max-w-5xl mx-auto p-6">
          {error && (
            <div className="mb-4 p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">
              {error}
            </div>
          )}

          {/* ── Rules tab ─────────────────────────────────────────────── */}
          {tab === 'rules' && (
            <>
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-semibold text-white">
                  Rules
                  {rules.length > 0 && (
                    <span className="ml-2 text-sm font-normal text-gray-500">{rules.length} total</span>
                  )}
                </h2>
                {isAdmin && (
                  <div className="flex gap-3">
                    <button
                      onClick={handleReload}
                      className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm font-medium transition-colors"
                    >↻ Reload Engine</button>
                    <button
                      onClick={() => setEditing('new')}
                      className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors"
                    >+ New Rule</button>
                  </div>
                )}
              </div>
              {rules.length === 0 ? (
                <div className="text-center py-24 text-gray-500">
                  <p className="text-lg">No rules yet.</p>
                  {isAdmin && <p className="text-sm mt-2">Click <strong>+ New Rule</strong> to create one.</p>}
                </div>
              ) : (
                <div className="space-y-3">
                  {rules.map(rule => (
                    <RuleCard
                      key={rule.id}
                      rule={rule}
                      isAdmin={isAdmin}
                      onToggle={() => handleToggle(rule.id)}
                      onEdit={() => setEditing(rule)}
                      onDelete={() => handleDelete(rule.id, rule.name)}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {/* ── Connections tab ───────────────────────────────────────── */}
          {tab === 'connections' && (
            <ConnectionsPage showToast={showToast} onConnectionsChanged={loadConnections} />
          )}

          {/* ── Users tab ─────────────────────────────────────────────── */}
          {tab === 'users' && isAdmin && (
            <UsersPage showToast={showToast} currentUserId={currentUser.id} />
          )}

          {/* ── Activity tab ──────────────────────────────────────────── */}
          {tab === 'activity' && (
            <ActivityPage showToast={showToast} />
          )}
        </main>
      </div>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 bg-gray-800 border border-gray-700 text-sm px-4 py-3 rounded-lg shadow-lg z-40">
          {toast}
        </div>
      )}

      {/* Rule editor modal */}
      {editing !== null && isAdmin && (
        <RuleEditor
          rule={editing === 'new' ? null : editing}
          conditionTypes={conditionTypes}
          actionTypes={actionTypes}
          connections={connections}
          onSave={handleSaved}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

function RuleCard({
  rule, isAdmin, onToggle, onEdit, onDelete,
}: { rule: Rule; isAdmin: boolean; onToggle: () => void; onEdit: () => void; onDelete: () => void }) {
  const actionColors: Record<string, string> = {
    upload_to_s3: 'bg-orange-900/50 text-orange-300',
    upload_to_onedrive: 'bg-blue-900/50 text-blue-300',
    create_jira_task: 'bg-indigo-900/50 text-indigo-300',
    forward_email: 'bg-green-900/50 text-green-300',
  }

  return (
    <div className={`bg-gray-900 border rounded-xl p-5 transition-all ${rule.enabled ? 'border-gray-700' : 'border-gray-800 opacity-60'}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-medium text-white truncate">{rule.name}</h2>
            <span className="text-xs px-2 py-0.5 rounded-full bg-gray-700 text-gray-300">
              match: {rule.match}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <span className="text-xs text-gray-500">
              {rule.conditions.length} condition{rule.conditions.length !== 1 ? 's' : ''}
            </span>
            <span className="text-gray-700">·</span>
            {rule.actions.map((a, i) => (
              <span key={i} className={`text-xs px-2 py-0.5 rounded-full font-medium ${actionColors[a.type] ?? 'bg-gray-700 text-gray-300'}`}>
                {a.type.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </div>

        {isAdmin && (
          <div className="flex items-center gap-2 shrink-0">
            {/* Enable/disable toggle */}
            <button
              onClick={onToggle}
              title={rule.enabled ? 'Disable rule' : 'Enable rule'}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${rule.enabled ? 'bg-blue-600' : 'bg-gray-700'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${rule.enabled ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
            <button
              onClick={onEdit}
              className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            >
              Edit
            </button>
            <button
              onClick={onDelete}
              className="px-3 py-1.5 text-xs rounded-lg bg-red-900/50 hover:bg-red-800/70 text-red-300 transition-colors"
            >
              Delete
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import type { ConnectionDetail, OneDriveAuthState, GoogleAuthState } from '../api'
import {
  getConnectionDetails, createConnection, updateConnection, deleteConnection,
  startOneDriveAuth, getOneDriveAuthStatus, clearOneDriveAuthStatus,
  getGoogleAuthStatus, startGoogleAuth, resetGoogleAuthStatus,
} from '../api'

// ── Per-type field schema ────────────────────────────────────────────────────

interface FieldDef {
  key: string
  label: string
  required?: boolean
  secret?: boolean
  placeholder?: string
}

const FIELD_SCHEMA: Record<string, FieldDef[]> = {
  s3: [
    { key: 'bucket',            label: 'Bucket',            required: true,  placeholder: 'my-bucket' },
    { key: 'region',            label: 'Region',                             placeholder: 'us-east-1' },
    { key: 'access_key_id',     label: 'Access Key ID',     secret: true },
    { key: 'secret_access_key', label: 'Secret Access Key', secret: true },
    { key: 'endpoint_url',      label: 'Endpoint URL',                       placeholder: 'https://s3.nl-ams.scw.cloud (leave blank for AWS)' },
    { key: 'storage_class',     label: 'Storage Class',                      placeholder: 'STANDARD' },
    { key: 'prefix',            label: 'Default Prefix',                     placeholder: 'attachments/' },
  ],
  jira: [
    { key: 'url',               label: 'Jira URL',         required: true,  placeholder: 'https://your-org.atlassian.net' },
    { key: 'user',              label: 'User (email)',      required: true },
    { key: 'token',             label: 'API Token',        required: true,  secret: true },
    { key: 'default_project',   label: 'Default Project',                   placeholder: 'ENG' },
    { key: 'default_issue_type',label: 'Default Issue Type',                placeholder: 'Task' },
  ],
  onedrive: [],  // No user-editable fields — client ID is configured server-side; token is stored automatically.
  mailgun: [
    { key: 'api_key',           label: 'API Key',          required: true,  secret: true },
    { key: 'domain',            label: 'Domain',           required: true,  placeholder: 'mg.example.com' },
    { key: 'sender_address',    label: 'Sender Address',   required: true,  placeholder: 'noreply@example.com' },
    { key: 'api_base',          label: 'API Base URL',                      placeholder: 'https://api.eu.mailgun.net/v3 (EU only)' },
  ],
}

const CONNECTION_TYPES = Object.keys(FIELD_SCHEMA)

const TYPE_COLORS: Record<string, string> = {
  s3:       'bg-orange-900/50 text-orange-300',
  jira:     'bg-indigo-900/50 text-indigo-300',
  onedrive: 'bg-blue-900/50 text-blue-300',
  mailgun:  'bg-green-900/50 text-green-300',
}

// ── Helper — summary of non-secret fields to show in the list ───────────────
function connectionSummary(conn: ConnectionDetail): string {
  const schema = FIELD_SCHEMA[conn.type] ?? []
  return schema
    .filter(f => !f.secret && conn.fields[f.key])
    .map(f => `${f.label}: ${conn.fields[f.key]}`)
    .join('  ·  ')
}

// ── OneDrive Auth Panel ───────────────────────────────────────────────────────

function OneDriveAuthPanel({ connId }: {
  connId: string
}) {
  const [state, setState] = useState<OneDriveAuthState>({ status: 'idle' })
  const [checking, setChecking] = useState(true)
  const [copied, setCopied] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  useEffect(() => () => stopPolling(), [])

  // On mount, silently probe whether the token is already valid.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await startOneDriveAuth(connId)
        if (!cancelled) setState(s)
      } catch { /* no token yet — leave as idle */ }
      if (!cancelled) setChecking(false)
    })()
    return () => { cancelled = true }
  }, [connId])

  const startPolling = () => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await getOneDriveAuthStatus(connId)
        setState(s)
        if (s.status !== 'pending') stopPolling()
      } catch { /* ignore poll errors */ }
    }, 3000)
  }

  const handleStart = async () => {
    try {
      const s = await startOneDriveAuth(connId)
      setState(s)
      if (s.status === 'pending') startPolling()
    } catch (e: unknown) {
      setState({ status: 'error', message: String(e) })
    }
  }

  const handleReset = async () => {
    stopPolling()
    await clearOneDriveAuthStatus(connId)
    setChecking(false)
    setState({ status: 'idle' })
  }

  const copyCode = () => {
    if (state.user_code) {
      navigator.clipboard.writeText(state.user_code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <div className="mt-1 rounded-xl border border-gray-700 bg-gray-800/50 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-gray-300">OneDrive Sign-In</p>
        {(state.status === 'success' || state.status === 'error') && (
          <button onClick={handleReset} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">
            Re-authenticate
          </button>
        )}
      </div>

      {checking && (
        <div className="flex items-center gap-2 text-xs text-gray-500 py-1">
          <svg className="animate-spin h-3.5 w-3.5 text-gray-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
          </svg>
          Checking token…
        </div>
      )}

      {!checking && state.status === 'idle' && (
        <button
          onClick={handleStart}
          className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors"
        >
          Connect OneDrive
        </button>
      )}

      {state.status === 'pending' && (
        <div className="space-y-3">
          <p className="text-xs text-gray-400">
            Open the link below, enter the code, and sign in with your Microsoft account.
          </p>
          {/* Code */}
          <div className="flex items-center justify-between gap-2 bg-gray-900 rounded-lg px-4 py-3">
            <span className="text-2xl font-mono font-bold tracking-widest text-white">
              {state.user_code}
            </span>
            <button
              onClick={copyCode}
              className="text-xs px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-300"
            >
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
          {/* Link */}
          <a
            href={state.verification_url}
            target="_blank"
            rel="noopener noreferrer"
            className="block w-full text-center py-2 rounded-lg border border-gray-600 hover:border-gray-400 text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            Open {state.verification_url} ↗
          </a>
          {/* Spinner */}
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <svg className="animate-spin h-3.5 w-3.5 text-blue-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
            Waiting for sign-in…
          </div>
        </div>
      )}

      {state.status === 'success' && (
        <div className="flex items-center gap-2 text-green-400 text-sm">
          <span className="text-lg">✓</span>
          {state.message ?? 'Authentication successful.'}
        </div>
      )}

      {state.status === 'error' && (
        <div className="text-red-400 text-sm">
          <span className="text-lg mr-1">✗</span>
          {state.message ?? 'Authentication failed.'}
        </div>
      )}
    </div>
  )
}

// ── Modal ────────────────────────────────────────────────────────────────────

interface ModalProps {
  conn: ConnectionDetail | null   // null = new
  onSave: () => void
  onClose: () => void
}

function ConnectionModal({ conn, onSave, onClose }: ModalProps) {
  const [id, setId] = useState(conn?.id ?? '')
  const [type, setType] = useState(conn?.type ?? 's3')
  const [fields, setFields] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(conn?.fields ?? {}).map(([k, v]) => [k, String(v ?? '')])
    )
  )
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = conn !== null

  const schema = FIELD_SCHEMA[type] ?? []

  const setField = (key: string, val: string) =>
    setFields(f => ({ ...f, [key]: val }))

  const handleTypeChange = (newType: string) => {
    setType(newType)
    setFields({})   // clear fields when type changes
    setRevealed(new Set())
  }

  const handleSubmit = async () => {
    if (!id.trim()) { setError('Connection ID is required.'); return }
    // Validate required fields
    const missing = schema.filter(f => f.required && !fields[f.key]?.trim()).map(f => f.label)
    if (missing.length) { setError(`Required: ${missing.join(', ')}`); return }

    const payload: ConnectionDetail = {
      id: id.trim(),
      type,
      fields: Object.fromEntries(
        Object.entries(fields).filter(([, v]) => v.trim() !== '')
      ),
    }

    setSaving(true)
    setError(null)
    try {
      if (isEditing) {
        await updateConnection(conn.id, payload)
      } else {
        try {
          await createConnection(payload)
        } catch (e: unknown) {
          // Auth flow may have already created a stub record — fall back to update.
          if (String(e).includes('409')) {
            await updateConnection(payload.id, payload)
          } else {
            throw e
          }
        }
      }
      onSave()
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  // Close on Escape
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm p-4 sm:p-8">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl my-auto">
        {/* Title */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold text-white">
            {isEditing ? `Edit "${conn.id}"` : 'New Connection'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl leading-none">✕</button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {error && (
            <div className="p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
          )}

          {/* ID */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
              Connection ID <span className="text-red-400">*</span>
            </label>
            <input
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
              value={id}
              onChange={e => setId(e.target.value)}
              disabled={isEditing}
              placeholder="e.g. my-s3-bucket"
            />
            {isEditing && <p className="text-xs text-gray-600">ID cannot be changed after creation.</p>}
          </div>

          {/* Type */}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
              Type <span className="text-red-400">*</span>
            </label>
            <select
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
              value={type}
              onChange={e => handleTypeChange(e.target.value)}
              disabled={isEditing}
            >
              {CONNECTION_TYPES.map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          {/* Dynamic fields */}
          {schema.map(f => {
            const isSecret = !!f.secret
            const show = revealed.has(f.key)
            return (
              <div key={f.key} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
                  {f.label}
                  {f.required && <span className="text-red-400 ml-1">*</span>}
                </label>
                <div className="flex gap-2">
                  <input
                    className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                    type={isSecret && !show ? 'password' : 'text'}
                    value={fields[f.key] ?? ''}
                    placeholder={f.placeholder ?? ''}
                    onChange={e => setField(f.key, e.target.value)}
                  />
                  {isSecret && (
                    <button
                      type="button"
                      onClick={() => setRevealed(r => {
                        const s = new Set(r)
                        s.has(f.key) ? s.delete(f.key) : s.add(f.key)
                        return s
                      })}
                      className="px-3 py-2 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
                    >
                      {show ? 'Hide' : 'Show'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}

          {/* OneDrive auth panel — only available after the connection is saved */}
          {type === 'onedrive' && (
            isEditing
              ? <OneDriveAuthPanel connId={conn!.id} />
              : <p className="text-xs text-gray-500 text-center py-1 border border-gray-700 rounded-lg p-3">
                  Save the connection first, then re-open it to authenticate with OneDrive.
                </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm font-medium transition-colors"
          >Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium transition-colors"
          >{saving ? 'Saving…' : 'Save Connection'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Google Auth Section ──────────────────────────────────────────────────────

const TOKEN_STATUS_STYLES: Record<string, string> = {
  valid:   'bg-green-900/50 text-green-300 border-green-700',
  expired: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  missing: 'bg-red-900/50 text-red-300 border-red-700',
  invalid: 'bg-red-900/50 text-red-300 border-red-700',
  error:   'bg-red-900/50 text-red-300 border-red-700',
}

function GoogleAuthSection({ showToast }: { showToast: (msg: string) => void }) {
  const [authState, setAuthState] = useState<GoogleAuthState | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  useEffect(() => () => stopPolling(), [])

  const fetchStatus = async () => {
    try {
      setAuthState(await getGoogleAuthStatus())
    } catch { /* non-fatal */ }
    setLoading(false)
  }

  useEffect(() => { fetchStatus() }, [])

  const startPolling = () => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await getGoogleAuthStatus()
        setAuthState(s)
        if (s.flow_status !== 'pending') {
          stopPolling()
          if (s.flow_status === 'success') showToast('Google authorization successful!')
          if (s.flow_status === 'error')   showToast(`Google auth error: ${s.flow_message}`)
        }
      } catch { /* ignore */ }
    }, 2500)
  }

  const handleStartAuth = async () => {
    setStarting(true)
    try {
      const res = await startGoogleAuth()
      window.open(res.auth_url, '_blank', 'noopener,noreferrer')
      startPolling()
      setAuthState(s => s ? { ...s, flow_status: 'pending' } : null)
    } catch (e: unknown) {
      showToast(`Failed to start Google auth: ${e}`)
    } finally {
      setStarting(false)
    }
  }

  const handleReset = async () => {
    stopPolling()
    await resetGoogleAuthStatus()
    await fetchStatus()
  }

  const ts = authState?.token_status ?? 'missing'
  const fs = authState?.flow_status ?? 'idle'

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 mb-8">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          {/* Google logo mark */}
          <svg viewBox="0 0 24 24" className="w-5 h-5 shrink-0" aria-hidden>
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          <div>
            <h3 className="text-sm font-semibold text-white">Google / Gmail Authorization</h3>
            <p className="text-xs text-gray-500 mt-0.5">OAuth2 token used by the notification receiver to watch your inbox.</p>
          </div>
        </div>
        {fs !== 'idle' && (
          <button onClick={handleReset} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">
            {fs === 'pending' ? 'Cancel' : 'Reset'}
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-xs text-gray-500 py-2">Loading…</p>
      ) : (
        <>
          {/* Token status badge */}
          <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs mb-4 ${TOKEN_STATUS_STYLES[ts] ?? 'bg-gray-800 text-gray-400 border-gray-700'}`}>
            <span className="font-semibold uppercase tracking-wide">Token: {ts}</span>
            {authState?.token_expiry && (
              <span className="opacity-70">· expires {new Date(authState.token_expiry).toLocaleString()}</span>
            )}
          </div>

          {/* Scopes */}
          {authState?.scopes && authState.scopes.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {authState.scopes.map(s => (
                <span key={s} className="text-xs bg-gray-800 text-gray-400 rounded px-2 py-0.5 truncate max-w-xs">
                  {s.replace('https://www.googleapis.com/auth/', '')}
                </span>
              ))}
            </div>
          )}

          {/* Flow state */}
          {fs === 'idle' && (
            <button
              onClick={handleStartAuth}
              disabled={starting}
              className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium transition-colors"
            >
              {ts === 'valid' ? 'Re-authorize Google Account' : 'Authorize Google Account'}
            </button>
          )}

          {fs === 'pending' && (
            <div className="flex items-center gap-3 rounded-lg bg-gray-800 border border-gray-700 px-4 py-3">
              <svg className="animate-spin h-4 w-4 text-blue-400 shrink-0" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
              </svg>
              <span className="text-sm text-gray-300">A browser window opened — complete the sign-in there.</span>
            </div>
          )}

          {fs === 'success' && (
            <div className="flex items-center gap-2 text-green-400 text-sm">
              <span className="text-lg">✓</span>
              Authorization successful. Token saved.
            </div>
          )}

          {fs === 'error' && (
            <div className="text-red-400 text-sm">
              <span className="text-lg mr-1">✗</span>
              {authState?.flow_message ?? 'Authorization failed.'}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

interface Props {
  showToast: (msg: string) => void
  onConnectionsChanged?: () => void
}

export default function ConnectionsPage({ showToast, onConnectionsChanged }: Props) {
  const [connections, setConnections] = useState<ConnectionDetail[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<ConnectionDetail | null | 'new'>(null)

  const load = async () => {
    setLoading(true)
    try {
      setConnections(await getConnectionDetails())
      setError(null)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleDelete = async (id: string) => {
    if (!confirm(`Delete connection "${id}"? Rules referencing it will stop working.`)) return
    try {
      await deleteConnection(id)
      setConnections(cs => cs.filter(c => c.id !== id))
      showToast(`Connection "${id}" deleted.`)
      onConnectionsChanged?.()
    } catch (e: unknown) {
      showToast(`Error: ${e}`)
    }
  }

  const handleSaved = async () => {
    setEditing(null)
    await load()
    showToast('Connection saved.')
    onConnectionsChanged?.()
  }

  if (loading) return (
    <div className="flex items-center justify-center py-24 text-gray-400">Loading…</div>
  )

  return (
    <div>
      <GoogleAuthSection showToast={showToast} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-white">Connections</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            Credentials for S3, JIRA, OneDrive, and Mailgun — referenced by rules.
          </p>
        </div>
        <button
          onClick={() => setEditing('new')}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors"
        >+ New Connection</button>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
      )}

      {connections.length === 0 ? (
        <div className="text-center py-24 text-gray-500">
          <p className="text-lg">No connections configured.</p>
          <p className="text-sm mt-2">Click <strong>+ New Connection</strong> to add one.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {connections.map(conn => (
            <div key={conn.id} className="bg-gray-900 border border-gray-700 rounded-xl px-5 py-4 flex items-center gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3">
                  <span className="text-base font-medium text-white">{conn.id}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${TYPE_COLORS[conn.type] ?? 'bg-gray-700 text-gray-300'}`}>
                    {conn.type}
                  </span>
                </div>
                {connectionSummary(conn) && (
                  <p className="text-xs text-gray-500 mt-1 truncate">{connectionSummary(conn)}</p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => setEditing(conn)}
                  className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
                >Edit</button>
                <button
                  onClick={() => handleDelete(conn.id)}
                  className="px-3 py-1.5 text-xs rounded-lg bg-red-900/50 hover:bg-red-800/70 text-red-300 transition-colors"
                >Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <ConnectionModal
          conn={editing === 'new' ? null : editing}
          onSave={handleSaved}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

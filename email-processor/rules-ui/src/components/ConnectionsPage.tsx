import { useEffect, useRef, useState } from 'react'
import type { ConnectionDetail, ConnectionDirection, InboundAuthState, OneDriveAuthState } from '../api'
import {
  getConnectionDetails, createConnection, updateConnection, deleteConnection,
  startOneDriveAuth, getOneDriveAuthStatus, clearOneDriveAuthStatus,
  startInboundAuth, getInboundAuthStatus, clearInboundAuthStatus, resetInboundAuth, syncInboundConnection,
} from '../api'

// ── Per-type field schema ────────────────────────────────────────────────────

interface FieldDef {
  key: string
  label: string
  required?: boolean
  secret?: boolean
  placeholder?: string
}

const INBOUND_FIELD_SCHEMA: Record<string, FieldDef[]> = {
  gmail: [],
  outlook: [],
  outlook365: [
    { key: 'tenant_id', label: 'Tenant ID / Domain (optional — leave blank for any account)', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx or contoso.onmicrosoft.com' },
  ],
}

const OUTBOUND_FIELD_SCHEMA: Record<string, FieldDef[]> = {
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
  onedrive365: [
    { key: 'tenant_id', label: 'Tenant ID / Domain (optional — leave blank for any work account)', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx or contoso.onmicrosoft.com' },
    { key: 'site_url',  label: 'SharePoint Site URL (optional — leave blank for OneDrive for Business)', placeholder: 'https://contoso.sharepoint.com/sites/MyTeam' },
  ],
  mailgun: [
    { key: 'api_key',           label: 'API Key',          required: true,  secret: true },
    { key: 'domain',            label: 'Domain',           required: true,  placeholder: 'mg.example.com' },
    { key: 'sender_address',    label: 'Sender Address',   required: true,  placeholder: 'noreply@example.com' },
    { key: 'api_base',          label: 'API Base URL',                      placeholder: 'https://api.eu.mailgun.net/v3 (EU only)' },
  ],
}

const INBOUND_CONNECTION_TYPES = Object.keys(INBOUND_FIELD_SCHEMA)
const OUTBOUND_CONNECTION_TYPES = Object.keys(OUTBOUND_FIELD_SCHEMA)

function getTypesForDirection(direction: ConnectionDirection): string[] {
  return direction === 'inbound' ? INBOUND_CONNECTION_TYPES : OUTBOUND_CONNECTION_TYPES
}

function getFieldSchema(direction: ConnectionDirection, type: string): FieldDef[] {
  if (direction === 'inbound') return INBOUND_FIELD_SCHEMA[type] ?? []
  return OUTBOUND_FIELD_SCHEMA[type] ?? []
}

const TYPE_COLORS: Record<string, string> = {
  gmail:      'bg-emerald-900/50 text-emerald-300',
  outlook:    'bg-cyan-900/50 text-cyan-300',
  outlook365: 'bg-sky-900/50 text-sky-300',
  s3:         'bg-orange-900/50 text-orange-300',
  jira:       'bg-indigo-900/50 text-indigo-300',
  onedrive:    'bg-blue-900/50 text-blue-300',
  onedrive365: 'bg-violet-900/50 text-violet-300',
  mailgun:     'bg-green-900/50 text-green-300',
}

function isInboundEnabled(conn: ConnectionDetail): boolean {
  if (conn.direction !== 'inbound') return true
  const raw = conn.fields?.enabled
  if (raw === undefined || raw === null) return true
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'string') {
    const normalized = raw.trim().toLowerCase()
    return !['false', '0', 'no', 'off', ''].includes(normalized)
  }
  return Boolean(raw)
}

// ── Helper — summary of non-secret fields to show in the list ───────────────
function connectionSummary(conn: ConnectionDetail): string {
  const schema = getFieldSchema(conn.direction, conn.type)
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

  // On mount, check existing status (poll state or already-authenticated).
  // Use GET /status first to avoid accidentally starting a new device flow.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await getOneDriveAuthStatus(connId)
        if (!cancelled) {
          if (s.status === 'pending') {
            // A flow is already in progress — resume polling
            setState(s)
            startPolling()
          } else if (s.status === 'success') {
            setState(s)
          }
          // 'idle' or 'error' → leave as idle so user must click Connect
        }
      } catch { /* status endpoint unavailable — leave as idle */ }
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
  direction: ConnectionDirection
  conn: ConnectionDetail | null   // null = new
  onSave: () => void
  onClose: () => void
}

function ConnectionModal({ direction, conn, onSave, onClose }: ModalProps) {
  const [id, setId] = useState(conn?.id ?? '')
  const [type, setType] = useState(conn?.type ?? getTypesForDirection(direction)[0])
  const [fields, setFields] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(conn?.fields ?? {}).map(([k, v]) => [k, String(v ?? '')])
    )
  )
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = conn !== null

  const schema = getFieldSchema(direction, type)

  const setField = (key: string, val: string) =>
    setFields(f => ({ ...f, [key]: val }))

  const handleTypeChange = (newType: string) => {
    setType(newType)
    setFields({})   // clear fields when type changes
    setRevealed(new Set())
  }

  const handleSubmit = async () => {
    if (!id.trim()) { setError('Connection ID is required.'); return }
    const missing = schema.filter(f => {
      if (!f.required) return false
      return !fields[f.key]?.trim()
    }).map(f => f.label)
    if (missing.length) { setError(`Required: ${missing.join(', ')}`); return }

    const payload: ConnectionDetail = {
      id: id.trim(),
      direction,
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
            {isEditing ? `Edit "${conn.id}"` : `New ${direction === 'inbound' ? 'Inbound' : 'Outbound'} Connection`}
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
              {getTypesForDirection(direction).map(t => (
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
          {(type === 'onedrive' || type === 'onedrive365') && (
            isEditing
              ? <OneDriveAuthPanel connId={conn!.id} />
              : <p className="text-xs text-gray-500 text-center py-1 border border-gray-700 rounded-lg p-3">
                  Save the connection first, then re-open it to authenticate.
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

// ── Inbound Auth Section (Gmail / Outlook) ──────────────────────────────────

const TOKEN_STATUS_STYLES: Record<string, string> = {
  valid:   'bg-green-900/50 text-green-300 border-green-700',
  expired: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  missing: 'bg-red-900/50 text-red-300 border-red-700',
  invalid: 'bg-red-900/50 text-red-300 border-red-700',
  error:   'bg-red-900/50 text-red-300 border-red-700',
}

function InboundAuthSection({ conn, showToast }: { conn: ConnectionDetail; showToast: (msg: string) => void }) {
  const [authState, setAuthState] = useState<InboundAuthState | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [copied, setCopied] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isGmail = conn.type === 'gmail'
  const isOutlook = conn.type === 'outlook' || conn.type === 'outlook365'

  const copyCode = () => {
    if (authState?.user_code) {
      navigator.clipboard.writeText(authState.user_code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  useEffect(() => () => stopPolling(), [])

  const fetchStatus = async () => {
    try {
      setAuthState(await getInboundAuthStatus(conn.id))
    } catch { /* non-fatal */ }
    setLoading(false)
  }

  useEffect(() => { fetchStatus() }, [conn.id])

  const startPolling = () => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await getInboundAuthStatus(conn.id)
        setAuthState(s)
        if (s.status !== 'pending') {
          stopPolling()
          if (s.status === 'success') showToast(`${conn.type} authorization successful!`)
          if (s.status === 'error')   showToast(`${conn.type} auth error: ${s.message ?? 'Unknown error'}`)
        }
      } catch { /* ignore */ }
    }, 2500)
  }

  const handleStartAuth = async () => {
    setStarting(true)
    try {
      const res = await startInboundAuth(conn.id, '')
      if (res.auth_url) {
        window.open(res.auth_url, '_blank', 'noopener,noreferrer')
      }
      if (res.status === 'pending') startPolling()
      setAuthState(res)
    } catch (e: unknown) {
      showToast(`Failed to start ${conn.type} auth: ${e}`)
    } finally {
      setStarting(false)
    }
  }

  const handleReset = async () => {
    stopPolling()
    await clearInboundAuthStatus(conn.id)
    await fetchStatus()
  }

  const handleResetAuth = async () => {
    if (!confirm(`Reset authentication for "${conn.id}"? The stored token will be wiped and you will need to re-authorize.`)) return
    stopPolling()
    try {
      await resetInboundAuth(conn.id)
      showToast('Authentication reset. Re-authorize to reconnect.')
      await fetchStatus()
    } catch (e: unknown) {
      showToast(`Reset failed: ${e}`)
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    try {
      const res = await syncInboundConnection(conn.id)
      if (isOutlook && typeof res.processed === 'number') {
        showToast(`Outlook sync complete: ${res.processed} message(s) processed.`)
      } else {
        showToast('Sync request sent successfully.')
      }
      await fetchStatus()
    } catch (e: unknown) {
      showToast(`Sync failed: ${e}`)
    } finally {
      setSyncing(false)
    }
  }

  const ts = authState?.token_status ?? 'missing'
  const fs = authState?.status ?? 'idle'

  return (
    <div className="space-y-3 mt-3">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <div>
            <h3 className="text-sm font-semibold text-white">
              {isGmail ? 'Google / Gmail Authorization' : 'Microsoft Outlook Authorization'}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {isGmail
                ? 'OAuth token used by notif receiver to watch your Gmail inbox.'
                : 'OAuth token used to pull new messages from Outlook inbox.'}
            </p>
          </div>
        </div>
        {fs !== 'idle' && fs !== 'success' && (
          <button
            onClick={handleReset}
            className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
          >
            {fs === 'pending' ? 'Cancel' : 'Reset'}
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-xs text-gray-500 py-2">Loading…</p>
      ) : (
        <>
          <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs mb-4 ${TOKEN_STATUS_STYLES[ts] ?? 'bg-gray-800 text-gray-400 border-gray-700'}`}>
            <span className="font-semibold uppercase tracking-wide">Token: {ts}</span>
            {(authState?.token_expiry || authState?.expires_at) && (
              <span className="opacity-70">· expires {new Date(authState?.token_expiry ?? authState?.expires_at ?? '').toLocaleString()}</span>
            )}
          </div>

          {authState?.scopes && authState.scopes.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {authState.scopes.map(s => (
                <span key={s} className="text-xs bg-gray-800 text-gray-400 rounded px-2 py-0.5 truncate max-w-xs">
                  {s.replace('https://www.googleapis.com/auth/', '')}
                </span>
              ))}
            </div>
          )}

          {fs === 'idle' && (
            <button
              onClick={handleStartAuth}
              disabled={starting}
              className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white transition-colors"
            >
              {ts === 'valid' ? `Re-authorize ${conn.type}` : `Authorize ${conn.type}`}
            </button>
          )}

          {fs === 'pending' && (
            <div className="space-y-3">
              <p className="text-xs text-gray-400">
                Open the link below, enter the code, and sign in with your Microsoft account.
              </p>
              {/* Code */}
              {authState?.user_code && (
                <div className="flex items-center justify-between gap-2 bg-gray-900 rounded-lg px-4 py-3">
                  <span className="text-2xl font-mono font-bold tracking-widest text-white">
                    {authState.user_code}
                  </span>
                  <button
                    onClick={copyCode}
                    className="text-xs px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-300 shrink-0"
                  >
                    {copied ? '✓ Copied' : 'Copy'}
                  </button>
                </div>
              )}
              {/* Link */}
              {authState?.verification_url && (
                <a
                  href={authState.verification_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block w-full text-center py-2 rounded-lg border border-gray-600 hover:border-gray-400 text-sm text-blue-400 hover:text-blue-300 transition-colors"
                >
                  Open {authState.verification_url} ↗
                </a>
              )}
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

          {fs === 'success' && (
            <div className="flex items-center gap-2 text-green-400 text-sm">
              <span className="text-lg">✓</span>
              Authorization successful. Token saved.
            </div>
          )}

          {fs === 'error' && (
            <div className="text-red-400 text-sm">
              <span className="text-lg mr-1">✗</span>
              {authState?.message ?? 'Authorization failed.'}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSync}
              disabled={syncing}
              className="px-3 py-1.5 text-xs rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white transition-colors"
            >
              {syncing ? 'Syncing...' : isGmail ? 'Start / Renew Watch' : 'Sync Inbox Now'}
            </button>
            {isOutlook && (
              <button
                onClick={handleResetAuth}
                className="px-3 py-1.5 text-xs rounded-lg bg-red-800 hover:bg-red-700 text-white transition-colors"
              >
                Reset Auth
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

interface Props {
  direction: ConnectionDirection
  showToast: (msg: string) => void
  onConnectionsChanged?: () => void
}

export default function ConnectionsPage({ direction, showToast, onConnectionsChanged }: Props) {
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

  const handleDelete = async (conn: ConnectionDetail) => {
    const warning = conn.direction === 'outbound'
      ? `Delete connection "${conn.id}"? Rules referencing it will stop working.`
      : `Delete inbound connection "${conn.id}"?`
    if (!confirm(warning)) return
    try {
      await deleteConnection(conn.id)
      setConnections(cs => cs.filter(c => c.id !== conn.id))
      showToast(`Connection "${conn.id}" deleted.`)
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

  const handleToggleInbound = async (conn: ConnectionDetail) => {
    if (conn.direction !== 'inbound') return
    const nextEnabled = !isInboundEnabled(conn)
    const payload: ConnectionDetail = {
      ...conn,
      fields: {
        ...conn.fields,
        enabled: nextEnabled,
      },
    }

    try {
      const updated = await updateConnection(conn.id, payload)
      setConnections(cs => cs.map(c => (c.id === conn.id ? updated : c)))
      showToast(`Inbound connection "${conn.id}" ${nextEnabled ? 'enabled' : 'disabled'}.`)
      onConnectionsChanged?.()
    } catch (e: unknown) {
      showToast(`Failed to update "${conn.id}": ${e}`)
    }
  }

  if (loading) return (
    <div className="flex items-center justify-center py-24 text-gray-400">Loading…</div>
  )

  const visibleConnections = connections
    .filter(conn => conn.direction === direction)
    .sort((a, b) => a.id.localeCompare(b.id))

  const directionTitle = direction === 'inbound' ? 'Inbound Connections' : 'Outbound Connections'
  const directionDescription = direction === 'inbound'
    ? 'Sources that deliver messages into the system, including Gmail and Outlook.'
    : 'Destinations and delivery services referenced by rules, including Mailgun, S3, OneDrive, and Jira.'
  const addLabel = direction === 'inbound' ? '+ New Inbound Connection' : '+ New Outbound Connection'
  const addButtonClass = direction === 'inbound'
    ? 'px-4 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-sm font-medium transition-colors'
    : 'px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm font-medium transition-colors'
  const hasListItems = visibleConnections.length > 0

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-white">{directionTitle}</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {directionDescription}
          </p>
        </div>
        <button
          onClick={() => setEditing('new')}
          className={addButtonClass}
        >{addLabel}</button>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
      )}

      {!hasListItems ? (
        <div className="text-center py-24 text-gray-500">
          <p className="text-lg">No {direction} connections configured.</p>
          <p className="text-sm mt-2">Click <strong>{addLabel}</strong> to add one.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {visibleConnections.map(conn => (
            <div key={conn.id} className="bg-gray-900 border border-gray-700 rounded-xl px-5 py-4">
              {conn.direction === 'inbound' && (
                <div className="mb-3">
                  <span className={`text-xs px-2 py-1 rounded-full border ${isInboundEnabled(conn)
                    ? 'bg-green-900/40 text-green-300 border-green-700'
                    : 'bg-gray-800 text-gray-300 border-gray-600'}`}>
                    {isInboundEnabled(conn) ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
              )}
              <div className="flex items-center gap-4">
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
                  {conn.direction === 'inbound' && (
                    <button
                      onClick={() => handleToggleInbound(conn)}
                      className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${isInboundEnabled(conn)
                        ? 'bg-yellow-900/60 hover:bg-yellow-800/80 text-yellow-300'
                        : 'bg-emerald-800 hover:bg-emerald-700 text-emerald-100'}`}
                    >
                      {isInboundEnabled(conn) ? 'Disable' : 'Enable'}
                    </button>
                  )}
                  <button
                    onClick={() => setEditing(conn)}
                    className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
                  >Edit</button>
                  <button
                    onClick={() => handleDelete(conn)}
                    className="px-3 py-1.5 text-xs rounded-lg bg-red-900/50 hover:bg-red-800/70 text-red-300 transition-colors"
                  >Delete</button>
                </div>
              </div>

              {direction === 'inbound' && isInboundEnabled(conn) && (conn.type === 'gmail' || conn.type === 'outlook' || conn.type === 'outlook365') && (
                <InboundAuthSection conn={conn} showToast={showToast} />
              )}
              {direction === 'inbound' && !isInboundEnabled(conn) && (
                <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/40 px-3 py-2 text-xs text-gray-400">
                  This inbound connection is disabled. Enable it to allow authorization, watch/sync, and email ingestion.
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <ConnectionModal
          direction={direction}
          conn={editing === 'new' ? null : editing}
          onSave={handleSaved}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

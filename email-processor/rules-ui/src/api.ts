// Central API client — all calls go through here.
// In production, paths are relative (same origin). In dev, Vite proxies /api → :8001.

export interface Condition {
  type: string
  value?: string | number | null
  case_sensitive?: boolean
}

export interface Action {
  type: string
  connection: string
  config: Record<string, unknown>
}

export interface Rule {
  id: number
  name: string
  enabled: boolean
  match: 'all' | 'any'
  folder: string
  conditions: Condition[]
  actions: Action[]
  created_at: string
  updated_at: string
}

export interface RulePayload {
  name: string
  enabled: boolean
  match: 'all' | 'any'
  folder: string
  conditions: Condition[]
  actions: Action[]
}

export interface ConditionType {
  value: string
  label: string
}

export interface ActionType {
  value: string
  label: string
}

export type ConnectionDirection = 'inbound' | 'outbound'

export interface Connection {
  id: string
  direction: ConnectionDirection
  type: string
  label: string
}

// Full connection detail — used in the Connections editor
export interface ConnectionDetail {
  id: string
  direction: ConnectionDirection
  type: string
  fields: Record<string, string | boolean | number | null>
}

export interface ConnectionTypeOption {
  value: string
  label: string
}

export interface ConnectionTypeGroups {
  inbound: ConnectionTypeOption[]
  outbound: ConnectionTypeOption[]
}

// ── Auth / Users ──────────────────────────────────────────────────────────────

export interface UserInfo {
  id: number
  username: string
  role: 'admin' | 'viewer'
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface LoginPayload {
  username: string
  password: string
}

export interface UserCreatePayload {
  username: string
  password: string
  role: 'admin' | 'viewer'
  is_active: boolean
}

export interface UserUpdatePayload {
  username?: string
  password?: string
  role?: 'admin' | 'viewer'
  is_active?: boolean
}

// ── Token storage ────────────────────────────────────────────────────────────

const TOKEN_KEY = 'ep_access_token'
const USER_KEY  = 'ep_current_user'

export const getStoredToken = (): string | null => localStorage.getItem(TOKEN_KEY)
export const getStoredUser  = (): UserInfo | null => {
  const raw = localStorage.getItem(USER_KEY)
  return raw ? JSON.parse(raw) : null
}
export const storeAuth = (token: string, user: UserInfo) => {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}
export const clearAuth = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

// ── HTTP helper ───────────────────────────────────────────────────────────────

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken()
  const authHeader: Record<string, string> = token
    ? { Authorization: `Bearer ${token}` }
    : {}

  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...authHeader, ...init?.headers },
    ...init,
  })

  if (res.status === 401) {
    // Token expired or invalid — clear and reload to show login
    clearAuth()
    window.location.reload()
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const login = (payload: LoginPayload) =>
  request<{ access_token: string; token_type: string; user: UserInfo }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const getMe = () => request<UserInfo>('/api/auth/me')

// ── Users ─────────────────────────────────────────────────────────────────────

export const getUsers = () => request<UserInfo[]>('/api/users')
export const createUser = (payload: UserCreatePayload) =>
  request<UserInfo>('/api/users', { method: 'POST', body: JSON.stringify(payload) })
export const updateUser = (id: number, payload: UserUpdatePayload) =>
  request<UserInfo>(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
export const deleteUser = (id: number) =>
  request<void>(`/api/users/${id}`, { method: 'DELETE' })

// ── Rules ────────────────────────────────────────────────────────────────────

export const getRules = () => request<Rule[]>('/api/rules')
export const getRule = (id: number) => request<Rule>(`/api/rules/${id}`)
export const createRule = (payload: RulePayload) =>
  request<Rule>('/api/rules', { method: 'POST', body: JSON.stringify(payload) })
export const updateRule = (id: number, payload: Partial<RulePayload>) =>
  request<Rule>(`/api/rules/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
export const deleteRule = (id: number) =>
  request<void>(`/api/rules/${id}`, { method: 'DELETE' })
export const toggleRule = (id: number) =>
  request<Rule>(`/api/rules/${id}/toggle`, { method: 'POST' })
export const reloadEngine = () =>
  request<{ message: string }>('/api/rules/reload', { method: 'POST' })

// ── Metadata ─────────────────────────────────────────────────────────────────

export const getConditionTypes = () => request<ConditionType[]>('/api/meta/condition-types')
export const getActionTypes = () => request<ActionType[]>('/api/meta/action-types')
export const getConnections = () => request<Connection[]>('/api/meta/connections')
export const getConnectionTypes = () => request<ConnectionTypeGroups>('/api/meta/connection-types')
export const getServerConfig = () => request<{ azure_client_id: string }>('/api/meta/server-config')

// ── Connections CRUD ──────────────────────────────────────────────────────────

export const getConnectionDetails = () => request<ConnectionDetail[]>('/api/connections')
export const createConnection = (payload: ConnectionDetail) =>
  request<ConnectionDetail>('/api/connections', { method: 'POST', body: JSON.stringify(payload) })
export const updateConnection = (id: string, payload: ConnectionDetail) =>
  request<ConnectionDetail>(`/api/connections/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) })
export const deleteConnection = (id: string) =>
  request<void>(`/api/connections/${encodeURIComponent(id)}`, { method: 'DELETE' })

// ── Action Logs ───────────────────────────────────────────────────────────────

export interface ActionLog {
  id: number
  email_id: string
  email_subject: string
  email_from: string
  email_date: string | null
  rule_name: string
  action_type: string
  connection_id: string | null
  status: 'ok' | 'error' | 'skipped' | string
  detail: Record<string, unknown> | null
  triggered_at: string
}

export const getActionLogs = (params?: {
  skip?: number
  limit?: number
  rule_name?: string
  status?: string
}) => {
  const qs = new URLSearchParams()
  if (params?.skip != null)       qs.set('skip', String(params.skip))
  if (params?.limit != null)      qs.set('limit', String(params.limit))
  if (params?.rule_name)          qs.set('rule_name', params.rule_name)
  if (params?.status)             qs.set('status', params.status)
  const q = qs.toString()
  return request<ActionLog[]>(`/api/logs${q ? `?${q}` : ''}`)
}

export const countActionLogs = (params?: { rule_name?: string; status?: string }) => {
  const qs = new URLSearchParams()
  if (params?.rule_name) qs.set('rule_name', params.rule_name)
  if (params?.status)    qs.set('status', params.status)
  const q = qs.toString()
  return request<{ count: number }>(`/api/logs/count${q ? `?${q}` : ''}`)
}
// ── Google / Gmail OAuth ──────────────────────────────────────────────────────

export interface GoogleAuthState {
  flow_status: 'idle' | 'pending' | 'success' | 'error'
  flow_message?: string
  token_status: 'valid' | 'expired' | 'missing' | 'invalid' | 'error'
  token_expiry?: string | null
  scopes?: string[]
}

// These hit the notif_receiver service via the /gmail/ proxy
export const getGoogleAuthStatus = () =>
  request<GoogleAuthState>('/gmail/auth/status')

export const startGoogleAuth = () =>
  request<{ status: string; auth_url: string }>('/gmail/auth/start', { method: 'POST' })

export const resetGoogleAuthStatus = () =>
  request<void>('/gmail/auth/status', { method: 'DELETE' })
// ── OneDrive OAuth (───────────────────────────────────────────────

export interface OneDriveAuthState {
  status: 'idle' | 'pending' | 'success' | 'error'
  user_code?: string
  verification_url?: string
  message?: string
  expires_at?: string
}

export const startOneDriveAuth = (connId: string, clientId?: string, tokenCache?: string) =>
  request<OneDriveAuthState>(`/api/onedrive-auth/${encodeURIComponent(connId)}/start`, {
    method: 'POST',
    body: JSON.stringify({ client_id: clientId ?? '', token_cache: tokenCache ?? 'onedrive_token_cache.json' }),
  })
export const getOneDriveAuthStatus = (connId: string) =>
  request<OneDriveAuthState>(`/api/onedrive-auth/${encodeURIComponent(connId)}/status`)
export const clearOneDriveAuthStatus = (connId: string) =>
  request<void>(`/api/onedrive-auth/${encodeURIComponent(connId)}/status`, { method: 'DELETE' })

// ── Inbound Auth (Gmail / Outlook) ─────────────────────────────────────────

export interface InboundAuthState {
  status: 'idle' | 'pending' | 'success' | 'error'
  message?: string
  auth_url?: string
  user_code?: string
  verification_url?: string
  expires_at?: string
  token_status?: 'valid' | 'expired' | 'missing' | 'invalid' | 'error'
  token_expiry?: string | null
  scopes?: string[]
  provider?: 'gmail' | 'outlook' | string
}

export const startInboundAuth = (connId: string, clientId?: string) =>
  request<InboundAuthState>(`/api/inbound-auth/${encodeURIComponent(connId)}/start`, {
    method: 'POST',
    body: JSON.stringify({ client_id: clientId ?? '' }),
  })

export const getInboundAuthStatus = (connId: string) =>
  request<InboundAuthState>(`/api/inbound-auth/${encodeURIComponent(connId)}/status`)

export const clearInboundAuthStatus = (connId: string) =>
  request<void>(`/api/inbound-auth/${encodeURIComponent(connId)}/status`, { method: 'DELETE' })

export const resetInboundAuth = (connId: string) =>
  request<{ reset: boolean }>(`/api/inbound-auth/${encodeURIComponent(connId)}/reset-auth`, { method: 'POST' })

export const syncInboundConnection = (connId: string) =>
  request<{ status: string; provider: string; processed?: number }>(`/api/inbound-auth/${encodeURIComponent(connId)}/sync`, {
    method: 'POST',
  })

import { useEffect, useState, useCallback } from 'react'
import type { ActionLog } from '../api'
import { getActionLogs, countActionLogs } from '../api'

const PAGE_SIZE = 50

const STATUS_STYLES: Record<string, string> = {
  ok:      'bg-green-900/50 text-green-300 border-green-800',
  error:   'bg-red-900/50 text-red-300 border-red-800',
  skipped: 'bg-gray-700/50 text-gray-400 border-gray-700',
}

const ACTION_COLORS: Record<string, string> = {
  upload_to_s3:      'text-orange-300',
  upload_to_onedrive:'text-blue-300',
  create_jira_task:  'text-indigo-300',
  forward_email:     'text-green-300',
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function DetailPanel({ detail }: { detail: Record<string, unknown> | null }) {
  if (!detail || Object.keys(detail).length === 0) return null
  return (
    <pre className="mt-2 text-xs text-gray-400 bg-gray-950 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
      {JSON.stringify(detail, null, 2)}
    </pre>
  )
}

interface Props {
  showToast: (msg: string) => void
}

export default function ActivityPage({ showToast }: Props) {
  const [logs, setLogs] = useState<ActionLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterRule, setFilterRule] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = {
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        ...(filterRule   ? { rule_name: filterRule }   : {}),
        ...(filterStatus ? { status: filterStatus }    : {}),
      }
      const [rows, cnt] = await Promise.all([
        getActionLogs(params),
        countActionLogs({ rule_name: filterRule || undefined, status: filterStatus || undefined }),
      ])
      setLogs(rows)
      setTotal(cnt.count)
      setError(null)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [page, filterRule, filterStatus])

  useEffect(() => { load() }, [load])

  // Reset to page 0 when filters change
  const applyFilter = (rule: string, status: string) => {
    setFilterRule(rule)
    setFilterStatus(status)
    setPage(0)
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-white">Activity</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            Action log — every rule execution recorded here.
          </p>
        </div>
        <button
          onClick={() => load()}
          className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm font-medium transition-colors"
        >↻ Refresh</button>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <input
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500"
          placeholder="Filter by rule name…"
          value={filterRule}
          onChange={e => applyFilter(e.target.value, filterStatus)}
        />
        <select
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          value={filterStatus}
          onChange={e => applyFilter(filterRule, e.target.value)}
        >
          <option value="">All statuses</option>
          <option value="ok">OK</option>
          <option value="error">Error</option>
          <option value="skipped">Skipped</option>
        </select>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-gray-400">Loading…</div>
      ) : logs.length === 0 ? (
        <div className="text-center py-24 text-gray-500">
          <p className="text-lg">No activity yet.</p>
          <p className="text-sm mt-2">Actions will appear here as emails are processed.</p>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {logs.map(log => (
              <div
                key={log.id}
                className="bg-gray-900 border border-gray-700 rounded-xl overflow-hidden"
              >
                {/* Row */}
                <button
                  className="w-full text-left px-5 py-3 flex items-start gap-4 hover:bg-gray-800/50 transition-colors"
                  onClick={() => setExpanded(expanded === log.id ? null : log.id)}
                >
                  {/* Status badge */}
                  <span className={`mt-0.5 shrink-0 text-xs px-2 py-0.5 rounded-full border font-medium ${STATUS_STYLES[log.status] ?? STATUS_STYLES['skipped']}`}>
                    {log.status}
                  </span>

                  {/* Main info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="text-sm font-medium text-white truncate">{log.email_subject || '(no subject)'}</span>
                      <span className="text-xs text-gray-500 shrink-0">{log.email_from}</span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                      <span className="text-xs text-gray-400">{log.rule_name}</span>
                      <span className={`text-xs font-medium ${ACTION_COLORS[log.action_type] ?? 'text-gray-400'}`}>
                        {log.action_type}
                      </span>
                      {log.connection_id && (
                        <span className="text-xs text-gray-500">via {log.connection_id}</span>
                      )}
                    </div>
                  </div>

                  {/* Timestamp */}
                  <span className="text-xs text-gray-500 shrink-0 mt-0.5">{formatDate(log.triggered_at)}</span>

                  {/* Chevron */}
                  <svg
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    className={`w-4 h-4 text-gray-500 shrink-0 mt-0.5 transition-transform ${expanded === log.id ? 'rotate-180' : ''}`}
                  >
                    <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                </button>

                {/* Expanded detail */}
                {expanded === log.id && (
                  <div className="px-5 pb-4 border-t border-gray-800">
                    <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-400">
                      <span><span className="text-gray-500">Email ID:</span> {log.email_id}</span>
                      {log.email_date && <span><span className="text-gray-500">Date:</span> {log.email_date}</span>}
                    </div>
                    <DetailPanel detail={log.detail} />
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4 text-sm text-gray-400">
            <span>{total} total entries</span>
            <div className="flex items-center gap-2">
              <button
                disabled={page === 0}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-xs transition-colors"
              >← Prev</button>
              <span className="text-xs">{page + 1} / {totalPages}</span>
              <button
                disabled={page + 1 >= totalPages}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-xs transition-colors"
              >Next →</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

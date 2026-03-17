import { useState, useEffect } from 'react'
import type { Rule, RulePayload, Condition, Action, ConditionType, ActionType, Connection } from '../api'
import { createRule, updateRule } from '../api'

interface Props {
  rule: Rule | null
  conditionTypes: ConditionType[]
  actionTypes: ActionType[]
  connections: Connection[]
  onSave: () => void
  onClose: () => void
}

const ACTION_CONNECTION_TYPE: Record<string, string> = {
  upload_to_s3: 's3',
  upload_to_onedrive: 'onedrive',
  create_jira_task: 'jira',
  forward_email: 'mailgun',
}

const NO_VALUE_TYPES = ['has_attachments']
const NUMBER_VALUE_TYPES = ['attachment_count_gte']

function blankCondition(): Condition {
  return { type: 'subject_contains', value: '' }
}

function blankAction(): Action {
  return { type: 'upload_to_s3', connection: '', config: {} }
}

function configFields(type: string, config: Record<string, unknown>, onChange: (k: string, v: unknown) => void) {
  const inp = (key: string, label: string, placeholder = '') => (
    <div key={key} className="flex flex-col gap-1">
      <label className="text-xs text-gray-400">{label}</label>
      <input
        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        value={(config[key] as string) ?? ''}
        placeholder={placeholder}
        onChange={e => onChange(key, e.target.value)}
      />
    </div>
  )
  const textarea = (key: string, label: string, placeholder = '') => (
    <div key={key} className="flex flex-col gap-1">
      <label className="text-xs text-gray-400">{label}</label>
      <textarea
        rows={3}
        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500 resize-none"
        value={(config[key] as string) ?? ''}
        placeholder={placeholder}
        onChange={e => onChange(key, e.target.value)}
      />
    </div>
  )
  const chk = (key: string, label: string) => (
    <label key={key} className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        className="w-4 h-4 rounded accent-blue-600"
        checked={!!config[key]}
        onChange={e => onChange(key, e.target.checked)}
      />
      <span className="text-sm text-gray-300">{label}</span>
    </label>
  )

  switch (type) {
    case 'upload_to_s3':
      return [inp('prefix', 'S3 Key Prefix', 'attachments/')]
    case 'upload_to_onedrive':
      return [inp('folder', 'Folder Path', 'Email Attachments')]
    case 'create_jira_task':
      return [
        inp('project', 'Project Key', 'ENG'),
        inp('issue_type', 'Issue Type', 'Task'),
        inp('summary_template', 'Summary Template', 'Email: {subject}'),
        textarea('description_template', 'Description Template', 'From: {sender}\n\n{body}'),
        inp('labels', 'Labels (comma-separated)', 'email, auto'),
        <div key="priority" className="flex flex-col gap-1">
          <label className="text-xs text-gray-400">Priority</label>
          <select
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            value={(config.priority as string) ?? 'Medium'}
            onChange={e => onChange('priority', e.target.value)}
          >
            {['Highest', 'High', 'Medium', 'Low', 'Lowest'].map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>,
        chk('attach_files', 'Attach email files to JIRA ticket'),
      ]
    case 'forward_email':
      return [
        inp('to', 'To (comma-separated)', 'recipient@example.com'),
        inp('subject_prefix', 'Subject Prefix', '[Fwd] '),
      ]
    default:
      return []
  }
}

export default function RuleEditor({ rule, conditionTypes, actionTypes, connections, onSave, onClose }: Props) {
  const [name, setName] = useState(rule?.name ?? '')
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [match, setMatch] = useState<'all' | 'any'>(rule?.match ?? 'all')
  const [conditions, setConditions] = useState<Condition[]>(
    rule?.conditions?.length ? rule.conditions : [blankCondition()]
  )
  const [actions, setActions] = useState<Action[]>(
    rule?.actions?.length ? rule.actions : [blankAction()]
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  /** Conditions helpers */
  const updateCondition = (i: number, patch: Partial<Condition>) =>
    setConditions(cs => cs.map((c, idx) => idx === i ? { ...c, ...patch } : c))
  const removeCondition = (i: number) =>
    setConditions(cs => cs.filter((_, idx) => idx !== i))
  const addCondition = () => setConditions(cs => [...cs, blankCondition()])

  /** Actions helpers */
  const updateAction = (i: number, patch: Partial<Action>) =>
    setActions(as => as.map((a, idx) => idx === i ? { ...a, ...patch } : a))
  const removeAction = (i: number) =>
    setActions(as => as.filter((_, idx) => idx !== i))
  const addAction = () => setActions(as => [...as, blankAction()])

  const updateActionConfig = (i: number, key: string, value: unknown) =>
    setActions(as => as.map((a, idx) => idx === i ? { ...a, config: { ...a.config, [key]: value } } : a))

  const handleSubmit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    const payload: RulePayload = {
      name: name.trim(), enabled, match,
      conditions: conditions.map(c => ({
        type: c.type,
        value: c.value,
        ...(c.case_sensitive !== undefined ? { case_sensitive: c.case_sensitive } : {}),
      })),
      actions: actions.map(a => ({ type: a.type, connection: a.connection, config: a.config })),
    }
    setSaving(true)
    setError(null)
    try {
      if (rule) {
        await updateRule(rule.id, payload)
      } else {
        await createRule(payload)
      }
      onSave()
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm p-4 sm:p-8">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-2xl shadow-2xl my-auto">
        {/* Title bar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold text-white">
            {rule ? 'Edit Rule' : 'New Rule'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl leading-none">✕</button>
        </div>

        <div className="px-6 py-5 space-y-6">
          {error && (
            <div className="p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>
          )}

          {/* Name + meta */}
          <div className="space-y-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">Rule Name</label>
              <input
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                value={name}
                placeholder="e.g. Forward invoices to S3"
                onChange={e => setName(e.target.value)}
              />
            </div>

            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded accent-blue-600"
                  checked={enabled} onChange={e => setEnabled(e.target.checked)} />
                <span className="text-sm text-gray-300">Enabled</span>
              </label>

              <div className="flex items-center gap-3">
                <span className="text-sm text-gray-400">Match:</span>
                {(['all', 'any'] as const).map(m => (
                  <label key={m} className="flex items-center gap-1.5 cursor-pointer">
                    <input type="radio" name="match" value={m} className="accent-blue-600"
                      checked={match === m} onChange={() => setMatch(m)} />
                    <span className="text-sm text-gray-300">{m}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>

          {/* Conditions */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-300">Conditions</h3>
              <button
                onClick={addCondition}
                className="text-xs px-3 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              >+ Add Condition</button>
            </div>
            <div className="space-y-2">
              {conditions.map((cond, i) => {
                const noVal = NO_VALUE_TYPES.includes(cond.type)
                const numVal = NUMBER_VALUE_TYPES.includes(cond.type)
                return (
                  <div key={i} className="flex items-center gap-2">
                    <select
                      className="flex-shrink-0 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                      value={cond.type}
                      onChange={e => updateCondition(i, { type: e.target.value, value: '' })}
                    >
                      {conditionTypes.map(ct => (
                        <option key={ct.value} value={ct.value}>{ct.label}</option>
                      ))}
                    </select>
                    {!noVal && (
                      <input
                        className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                        type={numVal ? 'number' : 'text'}
                        value={cond.value ?? ''}
                        placeholder={numVal ? '1' : 'value…'}
                        onChange={e => updateCondition(i, { value: numVal ? parseInt(e.target.value) || 0 : e.target.value })}
                      />
                    )}
                    {noVal && <span className="flex-1 text-sm text-gray-600 italic">no value needed</span>}
                    <button
                      onClick={() => removeCondition(i)}
                      className="text-gray-500 hover:text-red-400 text-lg leading-none px-1"
                      disabled={conditions.length === 1}
                    >✕</button>
                  </div>
                )
              })}
            </div>
          </section>

          {/* Actions */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-300">Actions</h3>
              <button
                onClick={addAction}
                className="text-xs px-3 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              >+ Add Action</button>
            </div>
            <div className="space-y-4">
              {actions.map((action, i) => {
                const connType = ACTION_CONNECTION_TYPE[action.type]
                const filtered = connections.filter(c => c.type === connType)
                return (
                  <div key={i} className="bg-gray-800/60 border border-gray-700/60 rounded-xl p-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <select
                        className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                        value={action.type}
                        onChange={e => updateAction(i, { type: e.target.value, connection: '', config: {} })}
                      >
                        {actionTypes.map(at => (
                          <option key={at.value} value={at.value}>{at.label}</option>
                        ))}
                      </select>

                      <select
                        className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                        value={action.connection}
                        onChange={e => updateAction(i, { connection: e.target.value })}
                      >
                        <option value="">— select connection —</option>
                        {filtered.map(c => (
                          <option key={c.id} value={c.id}>{c.label}</option>
                        ))}
                      </select>

                      <button
                        onClick={() => removeAction(i)}
                        className="text-gray-500 hover:text-red-400 text-lg leading-none px-1"
                        disabled={actions.length === 1}
                      >✕</button>
                    </div>

                    {/* Per-type config fields */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {configFields(action.type, action.config ?? {}, (k, v) => updateActionConfig(i, k, v))}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
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
          >{saving ? 'Saving…' : 'Save Rule'}</button>
        </div>
      </div>
    </div>
  )
}

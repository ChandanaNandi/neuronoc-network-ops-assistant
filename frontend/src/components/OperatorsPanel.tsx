import { useState, type FormEvent } from 'react'
import {
  ApiError,
  api,
  formatDate,
  relativeAge,
  type Operator,
  type OperatorRole,
} from '../api'

interface OperatorsPanelProps {
  // Lifted state owned by App so the approval-form dropdown sees newly-created
  // operators immediately without a page reload.
  operators: Operator[] | null
  onRefresh: () => Promise<void> | void
}

export function OperatorsPanel({
  operators,
  onRefresh,
}: OperatorsPanelProps) {
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<OperatorRole>('operator')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setSubmitting(true)
    setError(null)
    try {
      await api.createOperator({ display_name: trimmed, role })
      setName('')
      setRole('operator')
      setShowForm(false)
      await onRefresh()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : 'create operator failed'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  function toggleForm() {
    setShowForm((s) => !s)
    setError(null)
  }

  return (
    <section className="operators-panel">
      <div className="operators-panel__header">
        <span className="operators-panel__title">
          Operators ({operators?.length ?? 0})
        </span>
        <span className="operators-panel__chips">
          {operators === null && (
            <span className="muted">loading...</span>
          )}
          {operators && operators.length === 0 && (
            <span className="muted">
              none yet - create one to attribute approvals
            </span>
          )}
          {operators &&
            operators.map((o) => (
              <span key={o.id} className="operator-chip">
                {o.display_name}{' '}
                <span className="muted">[{o.role}]</span>{' '}
                <span
                  className="operator-chip__when muted"
                  title={formatDate(o.created_at)}
                >
                  {relativeAge(o.created_at)}
                </span>
              </span>
            ))}
        </span>
        <button
          type="button"
          className="btn btn--action"
          onClick={toggleForm}
        >
          {showForm ? 'Cancel' : '+ Add operator'}
        </button>
      </div>
      {showForm && (
        <form className="operators-form" onSubmit={submit}>
          <input
            type="text"
            className="approval-form__input"
            aria-label="new operator name"
            placeholder="display_name (unique)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            autoFocus
          />
          <select
            className="approval-form__input"
            aria-label="new operator role"
            value={role}
            onChange={(e) => setRole(e.target.value as OperatorRole)}
            disabled={submitting}
          >
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
          <button
            type="submit"
            className="btn btn--primary btn--action"
            disabled={!name.trim() || submitting}
          >
            {submitting ? 'Creating...' : 'Create operator'}
          </button>
          {error && (
            <span className="operators-form__error" role="alert">
              {error}
            </span>
          )}
        </form>
      )}
    </section>
  )
}

import { useState, type FormEvent } from 'react'
import { ApiError, api, setAuthToken, type Operator } from '../api'

interface LoginPanelProps {
  // App owns the authenticated operator. Login success / logout are
  // notified through these callbacks so other components (approval
  // form, operators panel) see the change.
  currentOperator: Operator | null
  onLoggedIn: (op: Operator) => void
  onLoggedOut: () => void
}

// Phase 23 minimal login surface. When unauthenticated, renders a tiny
// display_name + password form. When authenticated, renders the
// operator name + role + a logout button. No "register" / "forgot
// password" / SSO surfaces — local dev auth only, per Phase 23 scope.
export function LoginPanel({
  currentOperator,
  onLoggedIn,
  onLoggedOut,
}: LoginPanelProps) {
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!displayName.trim() || !password) return
    setSubmitting(true)
    setError(null)
    try {
      const resp = await api.login({
        display_name: displayName.trim(),
        password,
      })
      setAuthToken(resp.token)
      onLoggedIn(resp.operator)
      setDisplayName('')
      setPassword('')
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : 'login failed'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  async function logout() {
    setSubmitting(true)
    try {
      // Best-effort: backend invalidates the session row. Even if the
      // call fails (e.g. network blip), we drop the token locally so
      // the operator is logged out from this client's perspective.
      try {
        await api.logout()
      } catch {
        // ignored - we still clear local state below.
      }
      setAuthToken(null)
      onLoggedOut()
    } finally {
      setSubmitting(false)
    }
  }

  if (currentOperator) {
    return (
      <section className="login-panel" aria-label="authenticated operator">
        <span className="login-panel__title">Logged in as</span>{' '}
        <strong className="login-panel__name">{currentOperator.display_name}</strong>{' '}
        <span className="muted">[{currentOperator.role}]</span>
        <button
          type="button"
          className="btn btn--action btn--small"
          onClick={() => void logout()}
          disabled={submitting}
        >
          Logout
        </button>
      </section>
    )
  }

  return (
    <section className="login-panel" aria-label="operator login">
      <form className="login-panel__form" onSubmit={submit}>
        <span className="login-panel__title">Operator login</span>
        <input
          type="text"
          className="approval-form__input"
          aria-label="login display name"
          placeholder="display_name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          disabled={submitting}
          autoComplete="username"
        />
        <input
          type="password"
          className="approval-form__input"
          aria-label="login password"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          autoComplete="current-password"
        />
        <button
          type="submit"
          className="btn btn--primary btn--action"
          disabled={!displayName.trim() || !password || submitting}
        >
          {submitting ? 'Logging in...' : 'Login'}
        </button>
        {error && (
          <span className="login-panel__error" role="alert">
            {error}
          </span>
        )}
      </form>
    </section>
  )
}

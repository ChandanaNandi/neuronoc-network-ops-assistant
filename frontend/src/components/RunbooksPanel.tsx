import { useState, type KeyboardEvent } from 'react'
import { ApiError, api, type RunbookHit } from '../api'

interface RunbooksPanelProps {
  // When null, the "Use selected incident" button is disabled. When set,
  // clicking that button forwards the id to the backend; the backend
  // derives the query from the incident row's title + type + summary.
  selectedIncidentId: string | null
}

// Phase 17B operator-facing wrapper around the Phase 17A deterministic
// keyword search endpoint. Read-only by construction:
// - never POSTs or mutates anything
// - renders ONLY the bounded ~280-char excerpt the backend returns; the
//   full Markdown file is never fetched
// - no caching - the dominant interaction is "type a new query" or "click
//   a new incident", not toggle, so a cache would add state without paying
//   for itself (cf. Phase 16B validation preview where toggle dominated).
export function RunbooksPanel({ selectedIncidentId }: RunbooksPanelProps) {
  const [q, setQ] = useState('')
  // null = no search yet (empty state hidden); [] = searched, no matches
  // (empty state shown). Distinguishes "ready" from "ran-it-and-got-zero".
  const [results, setResults] = useState<RunbookHit[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // What the last search was, so the empty-state message can describe it.
  const [lastLabel, setLastLabel] = useState<string | null>(null)

  async function doSearch(
    params: { q?: string; incident_id?: string },
    label: string,
  ) {
    setLoading(true)
    setError(null)
    setLastLabel(label)
    try {
      const hits = await api.searchRunbooks({ ...params, limit: 5 })
      setResults(hits)
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : 'runbook search failed'
      setError(msg)
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  function searchByQuery() {
    const trimmed = q.trim()
    if (!trimmed) return
    void doSearch({ q: trimmed }, `"${trimmed}"`)
  }

  function searchByIncident() {
    if (!selectedIncidentId) return
    void doSearch(
      { incident_id: selectedIncidentId },
      'the selected incident',
    )
  }

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && q.trim() && !loading) {
      e.preventDefault()
      searchByQuery()
    }
  }

  return (
    <section className="runbooks-panel" aria-label="runbook search">
      <div className="runbooks-panel__header">
        <span className="runbooks-panel__title">Runbooks</span>
        <input
          type="search"
          className="approval-form__input runbooks-panel__input"
          aria-label="runbook search query"
          placeholder="search runbooks - e.g. bgp neighbor"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onInputKeyDown}
          disabled={loading}
        />
        <button
          type="button"
          className="btn btn--action"
          disabled={!q.trim() || loading}
          onClick={searchByQuery}
        >
          {loading ? 'Searching...' : 'Search'}
        </button>
        <button
          type="button"
          className="btn btn--action"
          disabled={!selectedIncidentId || loading}
          onClick={searchByIncident}
          title={
            selectedIncidentId
              ? 'Search using the currently selected incident'
              : 'Select an incident to enable this'
          }
        >
          Use selected incident
        </button>
        <span className="muted runbooks-panel__caveat">
          Deterministic keyword search · read-only · excerpt only
        </span>
      </div>
      {error && (
        <div className="error-banner runbooks-panel__error" role="alert">
          Runbook search: {error}
        </div>
      )}
      {results !== null && !loading && (
        <div className="runbooks-panel__results">
          {results.length === 0 ? (
            <div className="muted">
              No runbook matched{lastLabel ? ` ${lastLabel}` : ''}.
            </div>
          ) : (
            <ul className="runbook-hits" aria-label="runbook search results">
              {results.map((h) => (
                <li key={h.slug} className="runbook-hit">
                  <div className="runbook-hit__head">
                    <strong className="runbook-hit__title">{h.title}</strong>
                    <code className="runbook-hit__path muted">{h.path}</code>
                    <span className="muted runbook-hit__score">
                      score {h.score}
                    </span>
                  </div>
                  <div className="runbook-hit__excerpt">{h.excerpt}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}

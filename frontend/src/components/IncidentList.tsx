import { useEffect, useState } from 'react'
import {
  ApiError,
  api,
  relativeAge,
  shortId,
  type Incident,
} from '../api'

interface IncidentListProps {
  selectedId: string | null
  onSelect: (id: string) => void
  refreshTrigger: number
  onError: (msg: string | null) => void
}

export function IncidentList({
  selectedId,
  onSelect,
  refreshTrigger,
  onError,
}: IncidentListProps) {
  const [incidents, setIncidents] = useState<Incident[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .listIncidents(50)
      .then((list) => {
        if (!alive) return
        setIncidents(list)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (!alive) return
        const msg =
          err instanceof ApiError ? err.detail : 'failed to load incidents'
        setError(msg)
        setLoading(false)
        onError(`Incident list: ${msg}`)
      })
    return () => {
      alive = false
    }
  }, [refreshTrigger, onError])

  return (
    <aside className="incident-list">
      <div className="panel__header">
        <span>Incidents</span>
        <span className="incident-list__count">
          {incidents ? `${incidents.length}` : ''}
        </span>
      </div>
      <div className="incident-list__body">
        {loading && !incidents && (
          <div className="empty-state">
            <div className="empty-state__title">Loading...</div>
          </div>
        )}
        {error && !incidents && (
          <div className="empty-state">
            <div className="empty-state__title">Failed to load</div>
            <div className="empty-state__hint">{error}</div>
          </div>
        )}
        {incidents && incidents.length === 0 && (
          <div className="empty-state">
            <div className="empty-state__title">No incidents</div>
            <div className="empty-state__hint">
              Seed some via <code>python -m app.simulator.seed --scenario all</code>{' '}
              or run the lab collector.
            </div>
          </div>
        )}
        {incidents &&
          incidents.map((inc) => (
            <button
              key={inc.id}
              type="button"
              className={`incident-row${
                inc.id === selectedId ? ' incident-row--selected' : ''
              }`}
              onClick={() => onSelect(inc.id)}
            >
              <div className="incident-row__top">
                <span
                  className={`badge badge--severity badge--severity-${inc.severity}`}
                >
                  {inc.severity}
                </span>
                <span
                  className={`badge badge--status badge--status-${inc.status}`}
                >
                  {inc.status}
                </span>
                <span className="incident-row__age">
                  {relativeAge(inc.created_at)}
                </span>
              </div>
              <div className="incident-row__title">{inc.title}</div>
              <div className="incident-row__meta">
                <code>{inc.incident_type}</code>
                <span className="incident-row__id">{shortId(inc.id)}</span>
              </div>
            </button>
          ))}
      </div>
    </aside>
  )
}

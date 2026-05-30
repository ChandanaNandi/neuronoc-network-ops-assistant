import { useCallback, useEffect, useState } from 'react'
import './App.css'
import {
  ApiError,
  api,
  type LabBgpCollectionSummary,
  type Operator,
} from './api'
import { IncidentDetail } from './components/IncidentDetail'
import { IncidentList } from './components/IncidentList'
import { OperatorsPanel } from './components/OperatorsPanel'
import { StatusGrid } from './components/StatusGrid'

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // bumping this triggers list + status refetch
  const [listRefresh, setListRefresh] = useState(0)
  // bumping this triggers detail refetch
  const [detailRefresh, setDetailRefresh] = useState(0)

  const [collecting, setCollecting] = useState(false)
  const [lastLabCollection, setLastLabCollection] =
    useState<LabBgpCollectionSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Phase 13B: operators state is lifted here so OperatorsPanel and
  // IncidentDetail share a single source of truth. Creating an operator in
  // the panel immediately updates the approval-form dropdown without
  // needing a per-form refetch.
  const [operators, setOperators] = useState<Operator[] | null>(null)
  const refreshOperators = useCallback(async () => {
    try {
      const list = await api.listOperators()
      setOperators(list)
    } catch {
      // A failed operator list must not break the rest of the console.
      // Falls back to empty - approval form handles that path.
      setOperators([])
    }
  }, [])
  useEffect(() => {
    void refreshOperators()
  }, [refreshOperators])

  const refreshAll = useCallback(() => {
    setListRefresh((x) => x + 1)
    setDetailRefresh((x) => x + 1)
  }, [])

  // After an action that PERSISTS rows (agent run, remediation plan), bump
  // both the list and detail refresh counters so the status grid + detail
  // panel reflect the new data. RCA is recomputed per-request and never
  // persisted, so its action skips this callback entirely.
  const onPersistedMutation = useCallback(() => {
    setListRefresh((x) => x + 1)
    setDetailRefresh((x) => x + 1)
  }, [])

  async function collectLab() {
    setCollecting(true)
    setError(null)
    try {
      const summary = await api.collectLabBgp()
      setLastLabCollection(summary)
      // Jump to the new incident the collection just created.
      setSelectedId(summary.incident_id)
      refreshAll()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : 'lab collect failed'
      setError(`Lab collect: ${msg}`)
    } finally {
      setCollecting(false)
    }
  }

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">NeuroNOC</div>
        <div className="app__tagline">Multi-agent AI NetOps</div>
        <div className="app__actions">
          <button
            type="button"
            className="btn"
            onClick={refreshAll}
            title="Re-fetch the incident list and the selected incident"
          >
            Refresh
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={collectLab}
            disabled={collecting}
          >
            {collecting ? 'Collecting lab...' : 'Collect lab BGP snapshot'}
          </button>
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
          <button
            type="button"
            className="error-banner__close"
            onClick={() => setError(null)}
            aria-label="dismiss"
          >
            ×
          </button>
        </div>
      )}

      <StatusGrid
        refreshTrigger={listRefresh}
        lastLabCollection={lastLabCollection}
      />

      <OperatorsPanel
        operators={operators}
        onRefresh={refreshOperators}
      />

      <main className="app__main">
        <IncidentList
          selectedId={selectedId}
          onSelect={setSelectedId}
          refreshTrigger={listRefresh}
          onError={setError}
        />
        <section className="incident-detail-pane">
          {selectedId ? (
            <IncidentDetail
              incidentId={selectedId}
              refreshTrigger={detailRefresh}
              onPersistedMutation={onPersistedMutation}
              onError={setError}
              operators={operators}
            />
          ) : (
            <div className="empty-state">
              <div className="empty-state__title">Select an incident</div>
              <div className="empty-state__hint">
                Pick an incident on the left to see its anomaly findings, agent
                runs, and remediation plans. Or run a lab collection above to
                create a fresh one.
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

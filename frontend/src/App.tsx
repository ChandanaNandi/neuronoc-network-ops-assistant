import './App.css'

type ServiceStatus = 'online' | 'offline' | 'unknown'

interface StatusCardProps {
  label: string
  status: ServiceStatus
  detail?: string
}

function StatusCard({ label, status, detail }: StatusCardProps) {
  return (
    <div className="status-card">
      <div className="status-card__label">{label}</div>
      <div className="status-card__value">
        <span className={`status-dot status-dot--${status}`} />
        <span className="status-card__status">{status}</span>
      </div>
      {detail && <div className="status-card__detail">{detail}</div>}
    </div>
  )
}

export default function App() {
  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">NeuroNOC</div>
        <div className="app__tagline">Multi-agent AI NetOps</div>
      </header>

      <section className="app__status-grid">
        <StatusCard label="Backend" status="unknown" detail="GET /health" />
        <StatusCard label="Postgres" status="unknown" detail="localhost:5433" />
        <StatusCard label="Ollama" status="unknown" detail="localhost:11434" />
        <StatusCard label="Agents" status="unknown" detail="not implemented (Phase 5)" />
      </section>

      <main className="app__main">
        <div className="panel">
          <div className="panel__header">Incident Console</div>
          <div className="panel__body panel__body--empty">
            <div className="empty-state">
              <div className="empty-state__icon">·</div>
              <div className="empty-state__title">No active incidents</div>
              <div className="empty-state__hint">
                Anomaly detection has not been wired up yet — see Phase 4 of the roadmap.
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

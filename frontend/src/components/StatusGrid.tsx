import { useEffect, useState } from 'react'
import {
  ApiError,
  api,
  shortId,
  type AgentRun,
  type LabBgpCollectionSummary,
} from '../api'

type Probe = 'checking' | 'online' | 'offline'

interface StatusCardProps {
  label: string
  probe: Probe
  value: string
  detail?: string
}

function StatusCard({ label, probe, value, detail }: StatusCardProps) {
  return (
    <div className="status-card">
      <div className="status-card__label">{label}</div>
      <div className="status-card__value">
        <span className={`status-dot status-dot--${probe}`} />
        <span className="status-card__status">{value}</span>
      </div>
      {detail && <div className="status-card__detail">{detail}</div>}
    </div>
  )
}

interface StatusGridProps {
  // Light-touch knob the parent can flip to force a poll - e.g. after a
  // collection action created an incident, we want the counts to update.
  refreshTrigger: number
  lastLabCollection: LabBgpCollectionSummary | null
}

export function StatusGrid({
  refreshTrigger,
  lastLabCollection,
}: StatusGridProps) {
  const [backend, setBackend] = useState<Probe>('checking')
  const [incidents, setIncidents] = useState<{
    probe: Probe
    total: number
    open: number
  }>({ probe: 'checking', total: 0, open: 0 })
  const [findings, setFindings] = useState<{
    probe: Probe
    total: number
  }>({ probe: 'checking', total: 0 })
  const [latestRun, setLatestRun] = useState<{
    probe: Probe
    run: AgentRun | null
  }>({ probe: 'checking', run: null })

  useEffect(() => {
    let alive = true
    async function probe() {
      // Backend health probe.
      try {
        await api.health()
        if (alive) setBackend('online')
      } catch {
        if (alive) setBackend('offline')
      }

      // Incidents list - implicitly probes Postgres reachability.
      try {
        const list = await api.listIncidents(100)
        if (!alive) return
        setIncidents({
          probe: 'online',
          total: list.length,
          open: list.filter((i) => i.status === 'open').length,
        })
      } catch (err) {
        if (alive) {
          setIncidents({
            probe: err instanceof ApiError && err.status > 0 ? 'online' : 'offline',
            total: 0,
            open: 0,
          })
        }
      }

      // Findings across open incidents - implicitly probes the anomaly engine.
      try {
        const list = await api.listOpenFindings(100)
        if (alive) setFindings({ probe: 'online', total: list.length })
      } catch {
        if (alive) setFindings({ probe: 'offline', total: 0 })
      }

      // Latest agent run across the newest 10 incidents. There's no global
      // agent-runs endpoint today, so we scan a small bounded window. If any
      // call fails we mark the card offline (rather than silently empty).
      try {
        const incidents = await api.listIncidents(10)
        const runLists = await Promise.all(
          incidents.map((inc) =>
            api.agentRunsForIncident(inc.id, 5).catch(() => null),
          ),
        )
        if (runLists.some((r) => r === null)) {
          throw new Error('one or more agent-runs queries failed')
        }
        const flat = (runLists as AgentRun[][]).flat()
        if (flat.length === 0) {
          if (alive) setLatestRun({ probe: 'online', run: null })
        } else {
          const newest = flat.reduce((a, b) =>
            new Date(a.created_at).getTime() >
            new Date(b.created_at).getTime()
              ? a
              : b,
          )
          if (alive) setLatestRun({ probe: 'online', run: newest })
        }
      } catch {
        if (alive) setLatestRun({ probe: 'offline', run: null })
      }
    }

    probe()
    const id = setInterval(probe, 15000) // light polling - 15s
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [refreshTrigger])

  const labProbe: Probe = lastLabCollection
    ? lastLabCollection.errors.length === 0
      ? 'online'
      : 'offline'
    : 'checking'
  const labValue = lastLabCollection
    ? `${lastLabCollection.routers_seen}/4 routers, ${lastLabCollection.established_count} Established`
    : 'Ready'
  const labDetail = lastLabCollection
    ? lastLabCollection.errors.length
      ? `${lastLabCollection.errors.length} collection error(s)`
      : `${lastLabCollection.events_created} events written`
    : 'Click "Collect lab BGP snapshot"'

  const runValue = latestRun.run
    ? `${latestRun.run.status} · ${shortId(latestRun.run.id)}`
    : latestRun.probe === 'offline'
      ? 'offline'
      : 'No runs yet'
  const runDetail = latestRun.run
    ? `${latestRun.run.workflow_name}, ${latestRun.run.steps.length} steps`
    : latestRun.probe === 'offline'
      ? 'failed to query agent runs'
      : 'newest 10 incidents have no agent runs'

  return (
    <section className="app__status-grid">
      <StatusCard
        label="Backend"
        probe={backend}
        value={backend === 'online' ? 'online' : backend}
        detail="GET /health"
      />
      <StatusCard
        label="Incidents"
        probe={incidents.probe}
        value={`${incidents.total}`}
        detail={`${incidents.open} open / ${
          incidents.total - incidents.open
        } closed`}
      />
      <StatusCard
        label="Anomaly findings"
        probe={findings.probe}
        value={`${findings.total}`}
        detail="rule hits across open incidents"
      />
      <StatusCard
        label="Latest agent run"
        probe={latestRun.probe}
        value={runValue}
        detail={runDetail}
      />
      <StatusCard
        label="FRR lab collector"
        probe={labProbe}
        value={labValue}
        detail={labDetail}
      />
    </section>
  )
}

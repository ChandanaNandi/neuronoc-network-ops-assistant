import { useEffect, useState } from 'react'
import {
  ApiError,
  api,
  formatDate,
  shortId,
  type AgentRun,
  type AnomalyFinding,
  type Incident,
  type RCAExplanation,
  type Recommendation,
} from '../api'

interface IncidentDetailProps {
  incidentId: string
  // Bumped to trigger a full refetch (parent does this after a mutation).
  refreshTrigger: number
  // Called ONLY after an action that persists rows (agent run, plan).
  // RCA generation does NOT persist, so the RCA action skips this callback
  // and the displayed RCAExplanation survives across re-renders.
  onPersistedMutation: () => void
  onError: (msg: string | null) => void
}

type ActionKey = 'agent' | 'rca' | 'plan'

const ACTION_LABELS: Record<ActionKey, string> = {
  agent: 'Run agent analysis',
  rca: 'Generate RCA',
  plan: 'Generate remediation plan',
}

export function IncidentDetail({
  incidentId,
  refreshTrigger,
  onPersistedMutation,
  onError,
}: IncidentDetailProps) {
  const [incident, setIncident] = useState<Incident | null>(null)
  const [findings, setFindings] = useState<AnomalyFinding[] | null>(null)
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [plans, setPlans] = useState<Recommendation[] | null>(null)
  const [rca, setRca] = useState<RCAExplanation | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState<ActionKey | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    setActionMsg(null)
    setRca(null) // RCA isn't persisted; recompute on demand.

    Promise.all([
      api.getIncident(incidentId),
      api.findingsForIncident(incidentId),
      api.agentRunsForIncident(incidentId, 20),
      api.remediationPlansForIncident(incidentId, 20),
    ])
      .then(([inc, fs, rs, ps]) => {
        if (!alive) return
        setIncident(inc)
        setFindings(fs)
        setRuns(rs)
        setPlans(ps)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (!alive) return
        const msg =
          err instanceof ApiError ? err.detail : 'failed to load incident'
        setError(msg)
        setLoading(false)
        onError(`Incident detail: ${msg}`)
      })

    return () => {
      alive = false
    }
  }, [incidentId, refreshTrigger, onError])

  async function runAction(key: ActionKey) {
    setRunning(key)
    setActionMsg(null)
    try {
      if (key === 'agent') {
        const run = await api.runAgentAnalysis(incidentId)
        setActionMsg(
          `Agent run ${shortId(run.id)} ${run.status}; ${run.steps.length} steps recorded.`,
        )
        onPersistedMutation()
      } else if (key === 'rca') {
        const explanation = await api.generateRca(incidentId)
        setRca(explanation)
        setActionMsg(
          explanation.llm_available
            ? `RCA via ${explanation.model} (LLM available).`
            : 'RCA fallback used (Ollama unavailable). Deterministic explanation shown below.',
        )
        // NOTE: deliberately NOT calling onPersistedMutation() here -
        // RCA is recomputed on demand and never persisted, so triggering
        // a detail refetch would wipe the just-displayed explanation.
      } else if (key === 'plan') {
        const plan = await api.generateRemediationPlan(incidentId)
        setActionMsg(
          `Remediation plan persisted: ${plan.title} (risk ${plan.risk}).`,
        )
        onPersistedMutation()
      }
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : 'action failed'
      setActionMsg(`Error: ${msg}`)
      onError(`${ACTION_LABELS[key]}: ${msg}`)
    } finally {
      setRunning(null)
    }
  }

  if (loading && !incident) {
    return (
      <div className="incident-detail">
        <div className="empty-state">
          <div className="empty-state__title">Loading incident...</div>
        </div>
      </div>
    )
  }
  if (error && !incident) {
    return (
      <div className="incident-detail">
        <div className="empty-state">
          <div className="empty-state__title">Failed to load</div>
          <div className="empty-state__hint">{error}</div>
        </div>
      </div>
    )
  }
  if (!incident) return null

  return (
    <div className="incident-detail">
      <header className="incident-detail__header">
        <div>
          <div className="incident-detail__title">{incident.title}</div>
          <div className="incident-detail__meta">
            <span
              className={`badge badge--severity badge--severity-${incident.severity}`}
            >
              {incident.severity}
            </span>
            <span
              className={`badge badge--status badge--status-${incident.status}`}
            >
              {incident.status}
            </span>
            <code className="incident-detail__type">{incident.incident_type}</code>
            <span className="incident-detail__id">id {shortId(incident.id, 12)}</span>
            <span className="incident-detail__when">
              created {formatDate(incident.created_at)}
            </span>
          </div>
        </div>
      </header>

      <div className="incident-detail__actionbar">
        {(['agent', 'rca', 'plan'] as ActionKey[]).map((k) => (
          <button
            key={k}
            type="button"
            className="btn btn--action"
            disabled={running !== null}
            onClick={() => runAction(k)}
          >
            {running === k ? `${ACTION_LABELS[k]}...` : ACTION_LABELS[k]}
          </button>
        ))}
        {actionMsg && <span className="action-msg">{actionMsg}</span>}
      </div>

      {incident.summary && (
        <section className="detail-section">
          <h3 className="detail-section__title">Summary</h3>
          <pre className="detail-section__text">{incident.summary}</pre>
        </section>
      )}

      <section className="detail-section">
        <h3 className="detail-section__title">
          Anomaly findings <span className="muted">({findings?.length ?? 0})</span>
        </h3>
        {findings && findings.length === 0 && (
          <div className="muted">No rule hit this incident.</div>
        )}
        {findings &&
          findings.map((f) => (
            <div key={`${f.rule_id}:${f.incident_id}`} className="finding-card">
              <div className="finding-card__head">
                <span
                  className={`badge badge--severity badge--severity-${
                    f.severity as 'low' | 'medium' | 'high' | 'critical'
                  }`}
                >
                  {f.severity}
                </span>
                <code className="finding-card__rule">
                  {f.rule_id} {f.rule_name}
                </code>
                <span className="muted">
                  conf {Math.round(f.confidence * 100)}%
                </span>
              </div>
              <div className="finding-card__summary">{f.summary}</div>
              <div className="finding-card__next">
                <span className="label">Next step:</span> {f.recommended_next_step}
              </div>
              {f.evidence_refs.length > 0 && (
                <div className="finding-card__refs muted">
                  evidence refs:{' '}
                  {f.evidence_refs.map((id) => shortId(id)).join(', ')}
                </div>
              )}
            </div>
          ))}
      </section>

      <section className="detail-section">
        <h3 className="detail-section__title">
          Events &amp; evidence{' '}
          <span className="muted">(not yet exposed)</span>
        </h3>
        <div className="muted">
          The current backend exposes events/evidence only via POST and only
          embedded in agent runs. A dedicated GET endpoint will land in a
          follow-up phase (see Phase 9A report).
        </div>
      </section>

      {rca && (
        <section className="detail-section">
          <h3 className="detail-section__title">
            RCA explanation{' '}
            <span className="muted">
              {rca.llm_available ? `(${rca.model})` : '(deterministic fallback)'}
            </span>
          </h3>
          <div className="rca">
            <div>
              <strong>Summary:</strong> {rca.summary}
            </div>
            <div>
              <strong>Likely root cause:</strong> {rca.likely_root_cause}
            </div>
            {rca.recommended_next_steps.length > 0 && (
              <div>
                <strong>Recommended next steps:</strong>
                <ul>
                  {rca.recommended_next_steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {rca.unsafe_actions.length > 0 && (
              <div>
                <strong>Unsafe actions:</strong>
                <ul>
                  {rca.unsafe_actions.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="detail-section">
        <h3 className="detail-section__title">
          Agent runs <span className="muted">({runs?.length ?? 0})</span>
        </h3>
        {runs && runs.length === 0 && (
          <div className="muted">No agent runs yet. Click "Run agent analysis".</div>
        )}
        {runs &&
          runs.map((run) => (
            <details key={run.id} className="run-card">
              <summary>
                <span
                  className={`badge badge--run-${run.status}`}
                >
                  {run.status}
                </span>{' '}
                <code>{shortId(run.id)}</code>{' '}
                <span className="muted">{formatDate(run.created_at)}</span>
                <span className="muted"> · {run.steps.length} steps</span>
              </summary>
              <ol className="run-card__steps">
                {run.steps.map((s) => (
                  <li key={s.id}>
                    <code>{s.step_name}</code>{' '}
                    <span className="muted">{s.status}</span>
                  </li>
                ))}
              </ol>
              {run.output_payload && (
                <pre className="run-card__report">
                  {JSON.stringify(run.output_payload, null, 2)}
                </pre>
              )}
              {run.error && <div className="error-banner">{run.error}</div>}
            </details>
          ))}
      </section>

      <section className="detail-section">
        <h3 className="detail-section__title">
          Remediation plans{' '}
          <span className="muted">({plans?.length ?? 0})</span>
        </h3>
        {plans && plans.length === 0 && (
          <div className="muted">
            No remediation plans yet. Click "Generate remediation plan" - drafts only,
            nothing executes.
          </div>
        )}
        {plans &&
          plans.map((p) => (
            <details key={p.id} className="plan-card">
              <summary>
                <span
                  className={`badge badge--severity badge--severity-${p.risk}`}
                >
                  risk {p.risk}
                </span>{' '}
                {p.title}{' '}
                <span className="muted">{formatDate(p.created_at)}</span>
              </summary>
              <pre className="plan-card__details">{p.details}</pre>
            </details>
          ))}
      </section>
    </div>
  )
}

import { useEffect, useState } from 'react'
import {
  ApiError,
  api,
  formatDate,
  shortId,
  type AgentRun,
  type AnomalyFinding,
  type Incident,
  type IncidentEvent,
  type IncidentEvidence,
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

type RefHit = { kind: 'event' | 'evidence'; label: string }
type RefLookup = Map<string, RefHit>

function buildRefLookup(
  events: IncidentEvent[] | null,
  evidence: IncidentEvidence[] | null,
): RefLookup {
  const m: RefLookup = new Map()
  for (const e of events ?? []) {
    m.set(e.id, { kind: 'event', label: `evt:${e.event_type}@${e.source}` })
  }
  for (const v of evidence ?? []) {
    m.set(v.id, { kind: 'evidence', label: `ev:${v.evidence_type}@${v.source}` })
  }
  return m
}

function renderRef(id: string, lookup: RefLookup): string {
  const hit = lookup.get(id)
  return hit ? hit.label : `unresolved ${shortId(id)}`
}

export function IncidentDetail({
  incidentId,
  refreshTrigger,
  onPersistedMutation,
  onError,
}: IncidentDetailProps) {
  const [incident, setIncident] = useState<Incident | null>(null)
  const [findings, setFindings] = useState<AnomalyFinding[] | null>(null)
  const [events, setEvents] = useState<IncidentEvent[] | null>(null)
  const [evidence, setEvidence] = useState<IncidentEvidence[] | null>(null)
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [plans, setPlans] = useState<Recommendation[] | null>(null)
  const [rca, setRca] = useState<RCAExplanation | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState<ActionKey | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // At most one inline approval form is open at a time. The draft captures
  // which plan it belongs to, the kind of decision (approve/reject), and
  // the in-progress operator name + note. Phase 10B replaces the Phase 10A
  // window.prompt flow.
  const [approvalDraft, setApprovalDraft] = useState<{
    recId: string
    kind: 'approve' | 'reject'
    operator: string
    note: string
  } | null>(null)
  const [submittingApproval, setSubmittingApproval] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    setActionMsg(null)
    setRca(null) // RCA isn't persisted; recompute on demand.

    Promise.all([
      api.getIncident(incidentId),
      api.findingsForIncident(incidentId),
      api.eventsForIncident(incidentId, 100),
      api.evidenceForIncident(incidentId, 100),
      api.agentRunsForIncident(incidentId, 20),
      api.remediationPlansForIncident(incidentId, 20),
    ])
      .then(([inc, fs, evs, evd, rs, ps]) => {
        if (!alive) return
        setIncident(inc)
        setFindings(fs)
        setEvents(evs)
        setEvidence(evd)
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

  function openApprovalForm(
    recommendationId: string,
    kind: 'approve' | 'reject',
  ) {
    setApprovalDraft({
      recId: recommendationId,
      kind,
      operator: '',
      note: '',
    })
  }

  function closeApprovalForm() {
    setApprovalDraft(null)
  }

  async function submitApprovalForm() {
    if (!approvalDraft) return
    const operatorName = approvalDraft.operator.trim()
    if (!operatorName) return // submit is disabled in this state anyway

    setSubmittingApproval(true)
    try {
      const body = {
        operator_name: operatorName,
        note: approvalDraft.note.trim() || null,
      }
      if (approvalDraft.kind === 'approve') {
        await api.approveRecommendation(approvalDraft.recId, body)
      } else {
        await api.rejectRecommendation(approvalDraft.recId, body)
      }
      // On success the refreshed plan card itself shows the new status /
      // operator / note via its plan-card__approval block, so we just
      // collapse the form. The detail refetch wipes any inline actionMsg
      // anyway (see Phase 9A handling).
      setApprovalDraft(null)
      onPersistedMutation()
    } catch (err) {
      // Keep the form open on failure so the operator doesn't have to
      // retype, and surface the error via both the inline action-msg and
      // the top-level error banner.
      const msg =
        err instanceof ApiError ? err.detail : `${approvalDraft.kind} failed`
      setActionMsg(`Error: ${msg}`)
      onError(`${approvalDraft.kind} plan: ${msg}`)
    } finally {
      setSubmittingApproval(false)
    }
  }

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

  const refLookup = buildRefLookup(events, evidence)

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
                  refs:{' '}
                  {f.evidence_refs.map((id) => renderRef(id, refLookup)).join(', ')}
                </div>
              )}
            </div>
          ))}
      </section>

      <section className="detail-section">
        <h3 className="detail-section__title">
          Events <span className="muted">({events?.length ?? 0})</span>
        </h3>
        {events && events.length === 0 && (
          <div className="muted">No events recorded for this incident.</div>
        )}
        {events &&
          events.map((e) => (
            <div key={e.id} className="event-card">
              <div className="event-card__head">
                <code className="event-card__type">{e.event_type}</code>
                <span className="muted">@{e.source}</span>
                <span className="muted event-card__when">
                  {formatDate(e.created_at)}
                </span>
                <code className="event-card__id muted">{shortId(e.id)}</code>
              </div>
              <div className="event-card__message">{e.message}</div>
              {e.payload && Object.keys(e.payload).length > 0 && (
                <details className="event-card__payload">
                  <summary className="muted">payload</summary>
                  <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                </details>
              )}
            </div>
          ))}
      </section>

      <section className="detail-section">
        <h3 className="detail-section__title">
          Evidence <span className="muted">({evidence?.length ?? 0})</span>
        </h3>
        {evidence && evidence.length === 0 && (
          <div className="muted">No evidence attached to this incident.</div>
        )}
        {evidence &&
          evidence.map((v) => (
            <div key={v.id} className="event-card">
              <div className="event-card__head">
                <code className="event-card__type">{v.evidence_type}</code>
                <span className="muted">@{v.source}</span>
                <span className="muted event-card__when">
                  {formatDate(v.created_at)}
                </span>
                <code className="event-card__id muted">{shortId(v.id)}</code>
              </div>
              <pre className="event-card__content">{v.content}</pre>
              {v.payload && Object.keys(v.payload).length > 0 && (
                <details className="event-card__payload">
                  <summary className="muted">payload</summary>
                  <pre>{JSON.stringify(v.payload, null, 2)}</pre>
                </details>
              )}
            </div>
          ))}
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
        <div className="muted plan-section__caveat">
          Plan-only. Approving or rejecting records intent on the row -
          nothing is executed and no device is contacted.
        </div>
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
                <span
                  className={`badge badge--approval-${p.approval_status}`}
                >
                  {p.approval_status}
                </span>{' '}
                {p.title}{' '}
                <span className="muted">{formatDate(p.created_at)}</span>
              </summary>
              {p.approval_status !== 'pending' && (
                <div className="plan-card__approval">
                  <strong>{p.approval_status}</strong>{' '}
                  by <code>{p.approved_by ?? '?'}</code>{' '}
                  {p.approved_at && (
                    <span className="muted">at {formatDate(p.approved_at)}</span>
                  )}
                  {p.approval_note && (
                    <div className="plan-card__note">
                      <span className="label">note:</span> {p.approval_note}
                    </div>
                  )}
                </div>
              )}
              <div className="plan-card__actions">
                <button
                  type="button"
                  className="btn btn--action"
                  disabled={approvalDraft?.recId === p.id || submittingApproval}
                  onClick={() => openApprovalForm(p.id, 'approve')}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="btn btn--action"
                  disabled={approvalDraft?.recId === p.id || submittingApproval}
                  onClick={() => openApprovalForm(p.id, 'reject')}
                >
                  Reject
                </button>
                <span className="muted plan-card__safety">
                  records intent only; no execution
                </span>
              </div>
              {approvalDraft?.recId === p.id && (
                <form
                  className="approval-form"
                  onSubmit={(e) => {
                    e.preventDefault()
                    void submitApprovalForm()
                  }}
                >
                  <div className="approval-form__title">
                    Confirm {approvalDraft.kind} - records intent only;
                    no execution.
                  </div>
                  <label className="approval-form__field">
                    <span className="approval-form__label">Operator name</span>
                    <input
                      type="text"
                      className="approval-form__input"
                      aria-label="operator name"
                      autoFocus
                      required
                      value={approvalDraft.operator}
                      onChange={(e) =>
                        setApprovalDraft({
                          ...approvalDraft,
                          operator: e.target.value,
                        })
                      }
                      disabled={submittingApproval}
                    />
                  </label>
                  <label className="approval-form__field">
                    <span className="approval-form__label">
                      Note (optional)
                    </span>
                    <textarea
                      className="approval-form__textarea"
                      aria-label="approval note"
                      rows={2}
                      value={approvalDraft.note}
                      onChange={(e) =>
                        setApprovalDraft({
                          ...approvalDraft,
                          note: e.target.value,
                        })
                      }
                      disabled={submittingApproval}
                    />
                  </label>
                  <div className="approval-form__actions">
                    <button
                      type="submit"
                      className="btn btn--primary btn--action"
                      disabled={
                        !approvalDraft.operator.trim() || submittingApproval
                      }
                    >
                      {submittingApproval
                        ? 'Working...'
                        : `Confirm ${approvalDraft.kind}`}
                    </button>
                    <button
                      type="button"
                      className="btn btn--action"
                      onClick={closeApprovalForm}
                      disabled={submittingApproval}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
              <pre className="plan-card__details">{p.details}</pre>
            </details>
          ))}
      </section>
    </div>
  )
}

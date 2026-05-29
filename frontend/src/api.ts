// Typed fetch wrappers for the NeuroNOC backend.
// Mirrors Pydantic schemas in backend/app/schemas/*.
//
// Conventions:
// - All functions throw ApiError on non-2xx so the UI can render a clean message.
// - Datetimes come back as ISO strings; we keep them as strings in TS and let the
//   UI format on the fly with Date().toLocaleString() to avoid an extra dep.

// ----- shared types -----

export type Severity = 'low' | 'medium' | 'high' | 'critical'
export type IncidentStatus = 'open' | 'investigating' | 'resolved'
export type RecommendationRisk = 'low' | 'medium' | 'high'

export interface Incident {
  id: string
  title: string
  status: IncidentStatus
  severity: Severity
  incident_type: string
  summary: string | null
  root_cause: string | null
  confidence: number | null
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export interface IncidentEvent {
  id: string
  incident_id: string
  event_type: string
  source: string
  message: string
  payload: Record<string, unknown> | null
  created_at: string
}

export interface IncidentEvidence {
  id: string
  incident_id: string
  evidence_type: string
  source: string
  content: string
  payload: Record<string, unknown> | null
  created_at: string
}

export interface AnomalyFinding {
  rule_id: string
  rule_name: string
  severity: string
  confidence: number
  incident_id: string
  incident_type: string
  summary: string
  evidence_refs: string[]
  recommended_next_step: string
}

export interface AgentStep {
  id: string
  run_id: string
  step_name: string
  status: string
  input_payload: Record<string, unknown> | null
  output_payload: Record<string, unknown> | null
  error: string | null
  created_at: string
}

export interface AgentRun {
  id: string
  incident_id: string
  workflow_name: string
  status: 'running' | 'completed' | 'failed'
  input_payload: Record<string, unknown> | null
  output_payload: Record<string, unknown> | null
  error: string | null
  created_at: string
  completed_at: string | null
  steps: AgentStep[]
}

export interface RCAExplanation {
  incident_id: string
  summary: string
  likely_root_cause: string
  supporting_evidence: string[]
  runbook_references: string[]
  recommended_next_steps: string[]
  unsafe_actions: string[]
  confidence: number
  model: string | null
  llm_available: boolean
}

export interface Recommendation {
  id: string
  incident_id: string
  recommendation_type: string
  title: string
  details: string
  risk: RecommendationRisk
  requires_approval: boolean
  created_at: string
}

export interface RemediationPlan {
  incident_id: string
  plan_type: string
  title: string
  risk: string
  requires_approval: boolean
  summary: string
  pre_checks: string[]
  proposed_commands: string[]
  proposed_ansible_playbook: string
  post_checks: string[]
  rollback_steps: string[]
  validation_criteria: string[]
  safety_notes: string[]
  source: string
  confidence: number
}

export interface LabBgpCollectionSummary {
  incident_id: string
  routers_seen: number
  peers_seen: number
  established_count: number
  non_established_count: number
  events_created: number
  errors: string[]
}

export interface HealthResponse {
  status: string
  service: string
}

// ----- fetch helper -----

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : 'network error')
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (body && typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // ignore - body wasn't JSON
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

const post = <T>(path: string): Promise<T> =>
  request<T>(path, { method: 'POST' })

// ----- endpoints -----

export const api = {
  health: (): Promise<HealthResponse> => request<HealthResponse>('/health'),

  listIncidents: (limit = 50): Promise<Incident[]> =>
    request<Incident[]>(`/api/incidents?limit=${limit}`),

  getIncident: (id: string): Promise<Incident> =>
    request<Incident>(`/api/incidents/${id}`),

  eventsForIncident: (id: string, limit = 50): Promise<IncidentEvent[]> =>
    request<IncidentEvent[]>(`/api/incidents/${id}/events?limit=${limit}`),

  evidenceForIncident: (id: string, limit = 50): Promise<IncidentEvidence[]> =>
    request<IncidentEvidence[]>(
      `/api/incidents/${id}/evidence?limit=${limit}`,
    ),

  listOpenFindings: (limit = 100): Promise<AnomalyFinding[]> =>
    request<AnomalyFinding[]>(`/api/anomalies/open?limit=${limit}`),

  findingsForIncident: (id: string): Promise<AnomalyFinding[]> =>
    request<AnomalyFinding[]>(`/api/anomalies/incidents/${id}`),

  agentRunsForIncident: (id: string, limit = 20): Promise<AgentRun[]> =>
    request<AgentRun[]>(`/api/agents/incidents/${id}/runs?limit=${limit}`),

  remediationPlansForIncident: (
    id: string,
    limit = 20,
  ): Promise<Recommendation[]> =>
    request<Recommendation[]>(
      `/api/remediation/incidents/${id}/plans?limit=${limit}`,
    ),

  // actions
  runAgentAnalysis: (id: string): Promise<AgentRun> =>
    post<AgentRun>(`/api/agents/incidents/${id}/analyze`),

  generateRca: (id: string): Promise<RCAExplanation> =>
    post<RCAExplanation>(`/api/rca/incidents/${id}/explain`),

  generateRemediationPlan: (id: string): Promise<RemediationPlan> =>
    post<RemediationPlan>(`/api/remediation/incidents/${id}/plan`),

  collectLabBgp: (): Promise<LabBgpCollectionSummary> =>
    post<LabBgpCollectionSummary>('/api/lab/collect/bgp'),
}

// ----- small utils for the UI -----

export function shortId(id: string, n = 8): string {
  return id.slice(0, n)
}

export function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

export function relativeAge(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const now = Date.now()
  const secs = Math.max(0, Math.round((now - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 48) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

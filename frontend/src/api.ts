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
export type ApprovalStatus = 'pending' | 'approved' | 'rejected'
export type OperatorRole = 'operator' | 'admin'

export interface Operator {
  id: string
  display_name: string
  role: OperatorRole
  created_at: string
}

export interface OperatorCreate {
  display_name: string
  role?: OperatorRole
}

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
  // Backend currently records only created_at for steps; this is kept
  // optional so the inspector lights up the "completed" suffix
  // automatically if the backend later adds it.
  completed_at?: string | null
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
  approval_status: ApprovalStatus
  approved_by: string | null
  approved_by_operator_id: string | null
  approved_at: string | null
  approval_note: string | null
}

export interface ApprovalRequest {
  // Phase 23: body carries `note` only. The approving operator's
  // identity comes from the authenticated bearer-token session, not
  // from a submitted field. `extra="forbid"` on the backend means
  // legacy `operator_id` / `operator_name` would 422.
  note?: string | null
}

// Phase 23 auth shapes — mirror app/schemas/auth.py + operators.py.
export interface LoginRequest {
  display_name: string
  password: string
}

export interface LoginResponse {
  token: string
  operator: Operator
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

// Phase 18A normalized telemetry event. Mirrors `TelemetryEvent` in
// backend/app/telemetry/events.py. `collector_type` and `severity` are
// string-literal unions matching the backend enum values.
export type TelemetryCollectorType = 'snmp' | 'syslog' | 'manual'
export type TelemetrySeverity =
  | 'info'
  | 'notice'
  | 'warning'
  | 'error'
  | 'critical'

export interface TelemetryEvent {
  source: string
  collector_type: TelemetryCollectorType
  hostname?: string | null
  mgmt_ip?: string | null
  device_hint?: string | null
  observed_at: string
  event_type: string
  severity: TelemetrySeverity
  message: string
  labels: Record<string, string>
  raw: Record<string, unknown>
}

// Phase 18B read-only correlation preview. Mirrors
// `TelemetryCorrelationPreview` in backend/app/telemetry/correlator.py.
// `persisted` is `false` as a literal type so the compiler refuses any
// reassignment - this can't quietly become a write path on the UI side.
export interface TelemetryCorrelationPreview {
  telemetry_event: TelemetryEvent
  suggested_incident_type: string
  suggested_title: string
  suggested_severity: string
  suggested_event_type: string
  suggested_event_source: string
  suggested_event_payload: Record<string, unknown>
  correlation_key: string
  confidence: number
  rationale: string[]
  would_create_incident: boolean
  would_create_event: boolean
  persisted: false
}

// Phase 17A scored runbook hit from `GET /api/runbooks/search`. Mirrors
// `RunbookHit` in backend/app/schemas/runbooks.py. The backend returns
// only the bounded ~280-char excerpt, never the full Markdown file - the
// UI must not try to render anything bigger.
export interface RunbookHit {
  slug: string
  title: string
  score: number
  excerpt: string
  path: string
}

// Phase 16A read-only validation surface for a persisted remediation plan.
// Mirrors `ValidationPreviewRead` in backend/app/schemas/validation.py.
// `executable` is hard-pinned to false on the backend Pydantic side
// (Literal[False]); we mirror that with a literal type here so a future
// caller can't reassign it without a compiler complaint. Intentionally
// does NOT include `proposed_commands` / `proposed_ansible_playbook` -
// the preview is read-only by construction.
export interface ValidationPreview {
  recommendation_id: string
  incident_id: string
  plan_title: string
  plan_risk: string
  validation_source: 'remediation_plan'
  pre_checks: string[]
  post_checks: string[]
  validation_criteria: string[]
  rollback_steps: string[]
  safety_notes: string[]
  executable: false
  generated_at: string
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

// Phase 21A umbrella lab snapshot: BGP + interfaces + running-config in
// one Incident. Mirrors `LabSnapshotSummary` in app/lab/collector.py.
export interface LabSnapshotSummary {
  incident_id: string
  routers_seen: number
  peers_seen: number
  established_count: number
  non_established_count: number
  interfaces_seen: number
  interfaces_with_errors: number
  interfaces_down: number
  configs_collected: number
  events_created: number
  evidence_created: number
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

// Phase 23: a single bearer token, kept in-memory and mirrored into
// localStorage so a page reload keeps the operator logged in. This is
// dev-appropriate local auth; production deployments should swap in a
// real identity provider (SSO / SAML / OAuth) and likely move the token
// to a HttpOnly cookie. Documented in backend/README.md.
const AUTH_TOKEN_KEY = 'neuronoc_auth_token'
let _authToken: string | null = null

function _readPersistedToken(): string | null {
  try {
    return window.localStorage.getItem(AUTH_TOKEN_KEY)
  } catch {
    return null
  }
}

// Hydrate on module load so a refresh keeps the user logged in.
_authToken = _readPersistedToken()

export function setAuthToken(token: string | null): void {
  _authToken = token
  try {
    if (token === null) {
      window.localStorage.removeItem(AUTH_TOKEN_KEY)
    } else {
      window.localStorage.setItem(AUTH_TOKEN_KEY, token)
    }
  } catch {
    // localStorage may be unavailable (e.g. Safari private mode). In-memory
    // state still works for the rest of the session.
  }
}

export function getAuthToken(): string | null {
  return _authToken
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  // Phase 23: inject the bearer token on every request when one is
  // present. The backend ignores it on endpoints that don't require
  // auth, so this is safe for all calls.
  let finalInit: RequestInit | undefined = init
  if (_authToken !== null) {
    const headers = new Headers(init?.headers ?? {})
    headers.set('Authorization', `Bearer ${_authToken}`)
    finalInit = { ...(init ?? {}), headers }
  }
  let response: Response
  try {
    response = await fetch(path, finalInit)
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

const postJson = <T>(path: string, body: unknown): Promise<T> =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

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

  // Phase 21A umbrella lab snapshot - BGP + interfaces + running-config.
  collectLabSnapshot: (): Promise<LabSnapshotSummary> =>
    post<LabSnapshotSummary>('/api/lab/collect/snapshot'),

  approveRecommendation: (
    id: string,
    body: ApprovalRequest,
  ): Promise<Recommendation> =>
    postJson<Recommendation>(
      `/api/remediation/recommendations/${id}/approve`,
      body,
    ),

  rejectRecommendation: (
    id: string,
    body: ApprovalRequest,
  ): Promise<Recommendation> =>
    postJson<Recommendation>(
      `/api/remediation/recommendations/${id}/reject`,
      body,
    ),

  // Phase 13A operators
  listOperators: (): Promise<Operator[]> =>
    request<Operator[]>('/api/operators'),

  createOperator: (body: OperatorCreate): Promise<Operator> =>
    postJson<Operator>('/api/operators', body),

  // Phase 23 auth — login mints a bearer token, logout invalidates the
  // current session, me returns the operator bound to the current
  // bearer token (used to rehydrate state on page load).
  login: (body: LoginRequest): Promise<LoginResponse> =>
    postJson<LoginResponse>('/api/auth/login', body),

  logout: (): Promise<void> => post<void>('/api/auth/logout'),

  me: (): Promise<Operator> => request<Operator>('/api/auth/me'),

  // Phase 16A read-only validation preview for a persisted remediation plan.
  getValidationPreview: (recommendationId: string): Promise<ValidationPreview> =>
    request<ValidationPreview>(
      `/api/validation/recommendations/${recommendationId}/preview`,
    ),

  // Phase 17A deterministic keyword search over bundled runbooks.
  // At least one of q / incident_id must be supplied (the backend returns
  // 400 otherwise); 404 if incident_id is unknown.
  searchRunbooks: (params: {
    q?: string
    incident_id?: string
    limit?: number
  }): Promise<RunbookHit[]> => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    if (params.incident_id) qs.set('incident_id', params.incident_id)
    if (params.limit !== undefined) qs.set('limit', String(params.limit))
    return request<RunbookHit[]>(`/api/runbooks/search?${qs.toString()}`)
  },

  // Phase 18A validation-only round-trip for a TelemetryEvent payload.
  // Backend returns 422 on schema failure; no persistence either way.
  validateTelemetry: (payload: unknown): Promise<TelemetryEvent> =>
    postJson<TelemetryEvent>('/api/telemetry/validate', payload),

  // Phase 18B read-only correlation preview. Maps a TelemetryEvent to
  // how it would land as an Incident + IncidentEvent; persisted=false.
  previewTelemetryCorrelation: (
    payload: unknown,
  ): Promise<TelemetryCorrelationPreview> =>
    postJson<TelemetryCorrelationPreview>(
      '/api/telemetry/correlate/preview',
      payload,
    ),
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

// Format the elapsed time between two ISO timestamps. Returns null if the
// end timestamp is absent (the inspector falls back to showing only the start
// time in that case) or if either value fails to parse. Sub-second durations
// render as `<n> ms`, anything past 1 s as `<n.n> s`. Kept tiny on purpose -
// there's no need for a full units library here.
export function formatDuration(
  startIso: string,
  endIso?: string | null,
): string | null {
  if (!endIso) return null
  const start = new Date(startIso).getTime()
  const end = new Date(endIso).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  const ms = Math.max(0, end - start)
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
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

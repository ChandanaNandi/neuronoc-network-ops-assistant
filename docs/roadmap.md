# NeuroNOC roadmap

Phases are sequential. Each phase is reviewed and approved before the next begins.

## Phase 1 — Scaffold ✓

- Monorepo layout (`backend/`, `frontend/`, `infra/`, `docs/`).
- FastAPI app with `/health` and `/`.
- Vite + React + TS dashboard shell.
- Docker Compose with Postgres 16 on host port 5433.
- `.env.example`, root docs.
- Hardening: `.gitignore`, Python pinned to 3.12, cwd-independent config loader.

## Phase 2 — Data model and incidents ✓

- SQLAlchemy 2.x + Alembic migrations, psycopg 3 driver.
- Tables shipped: `devices`, `incidents`, `incident_events`, `incident_evidence`, `recommendations` (UUID PKs, timestamped, JSONB payloads).
- DB-level defaults (`gen_random_uuid()`, `incidents.status='open'`, `recommendations.requires_approval=true`).
- Sync `Session` per request; savepoint-rolled-back test fixture against real Postgres.
- REST endpoints: incident CRUD + event / evidence / recommendation sub-resources, paginated list (newest first).
- Deferred to later phases: `interfaces`, `metrics_raw`, `agent_runs`, `agent_messages`.

## Phase 3 — Collector simulator ✓

- Deterministic synthetic incident generator in `app/simulator/` — **not a real collector**, no SNMP / syslog / streaming.
- 4 simulator devices, 5 scenarios, CLI + optional API, surgical reset.

## Phase 4 — Anomaly engine ✓

- Deterministic rule-based engine in `app/anomaly/` — no ML, no LLM.
- 7 rules (`R001`–`R007`).
- Read-only: findings recomputed per request, not persisted (persistence arrives in Phase 5 via `agent_runs`).

## Phase 5 — LangGraph multi-agent orchestration ✓

- LangGraph 1.x `StateGraph` workflow in `app/agents/`. Deterministic Python nodes - no LLM calls.
- 6 nodes; per-run / per-step audit in `agent_runs` / `agent_steps`.
- HTTP: `POST /api/agents/incidents/{id}/analyze`, `GET /api/agents/runs/{id}`, `GET /api/agents/incidents/{id}/runs`.
- CLI: `python -m app.agents.runner --incident-id <uuid>`.

## Phase 6 — Ollama RCA explanation + keyword runbook retrieval ✓

- Optional local-LLM explanation layer in `app/rca/`. Local Ollama only.
- Bundled Markdown runbooks + keyword retrieval (no vector store yet).
- Graceful fallback when Ollama is unreachable; `require_llm=True` opts into 503 / non-zero exit.
- HTTP `POST /api/rca/incidents/{id}/explain`, CLI `python -m app.rca.explainer --incident-id <uuid>`.

## Phase 7 — Remediation planning *(current)*

- Plan-only. **Nothing is executed.** A test scans `app/remediation/` and fails the build if any execution token (`subprocess`, `ansible_runner`, `netmiko`, `napalm`, `paramiko`, `pexpect`, `fabric`, `scrapli`) ever appears.
- Reuses the existing `recommendations` table (no migration). Persisted plans use `recommendation_type="remediation_plan"`, `requires_approval=True` (re-asserted in the persistence layer), and store the full structured plan in `details` as a readable summary plus a fenced JSON block.
- `RemediationPlan` Pydantic model: `incident_id`, `plan_type`, `title`, `risk`, `requires_approval`, `summary`, `pre_checks`, `proposed_commands`, `proposed_ansible_playbook`, `post_checks`, `rollback_steps`, `validation_criteria`, `safety_notes`, `source`, `confidence`.
- 5 specific templates + 1 default: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`, `generic_investigation`. Selection: incident_type first, then Phase 5 correlation theme, then default.
- Ansible drafts gate every risky task on `when: false` plus a "REQUIRES APPROVED CHANGE WINDOW" comment so the file cannot run as-is.
- `interface_errors_spike` deliberately leads with observation, never a config change.
- HTTP: `POST /api/remediation/incidents/{id}/plan` (creates + persists), `GET /api/remediation/incidents/{id}/plans?limit=20` (lists persisted plans).
- CLI: `python -m app.remediation.planner --incident-id <uuid> [--persist | --no-persist]`.
- Deferred: any actual execution path, diff preview in the UI, change-management integration, plan signing / approval workflow.

## Phase 8 — Network lab integration

### Phase 8A — readiness audit ✓

- Read-only host audit picked Compose over Containerlab/Lima for the M4 / disk / arm64 budget.

### Phase 8B — Compose FRR mini-lab ✓

- 4 FRR v8.4.1 routers (edge-1, edge-2, core-1, branch-1) on dedicated `neuronoc_lab_*` bridges.
- eBGP fully Established; loopbacks advertised; `lab.sh` helper for `up`/`down`/`ps`/`logs`/`cli`/`bgp`.
- `pull_policy: never` so the lab refuses to silently pull a different FRR version.

### Phase 8C — one-shot BGP collector *(current)*

- `app/lab/collector.py` scrapes the four lab routers via `docker exec` + `vtysh -c "show ip bgp summary json"` (read-only only).
- Persists into the **existing** `Incident` / `IncidentEvent` schema - no migration.
- One `Incident` per invocation, tagged `[lab-collector]` in `summary`; events: `lab_bgp_peer_established`, `lab_bgp_peer_not_established`, `lab_bgp_prefix_snapshot`, `lab_bgp_collection_error`.
- Severity derived: low (all good) / medium (some peers down) / high (any collection error).
- HTTP: `POST /api/lab/collect/bgp` (synchronous, 201). CLI: `python -m app.lab.collector --collect`.
- One-shot only - no background daemon, no scheduler, no continuous ingest. That arrives if and when the project needs streaming telemetry.

### Later sub-phases (deferred)

- Containerlab topology (when veth pairs, L2 trunks, or multi-vendor are actually needed).
- End-to-end loop: lab fault → detector → RCA → validation → plan → human-approved apply.
- Lima fallback for x86_64-only network images.

## Phase 10A — Remediation approval workflow stub *(current)*

- Migration `f78faa47f6bd` adds 4 columns to `recommendations`: `approval_status` (default `'pending'`, indexed), `approved_by`, `approved_at`, `approval_note`.
- `ApprovalStatus` enum (pending / approved / rejected). New `ApprovalRequest` schema (`operator_name`, optional `note`).
- `set_recommendation_approval(db, rec_id, status, operator_name, note)` helper in `app/remediation/planner.py`; new exceptions `RecommendationNotFoundError` (404) and `WrongRecommendationTypeError` (400 - only `remediation_plan` recommendations are approvable).
- HTTP: `POST /api/remediation/recommendations/{id}/approve` and `.../reject`. Idempotent same-state calls update the metadata.
- Safety contract preserved: the existing "no remote-execution imports" AST scan now covers both `app/remediation/` AND `app/api/remediation.py`.
- UI: per-plan approval badge + Approve/Reject buttons that prompt for operator + note and refresh the panel. Caveat line states recorded intent only, no execution.
- **No auth yet, no execution path, no background job.** Approval is persisted intent only.

## Beyond

- AuthN/AuthZ, multi-tenant.
- Production deployment (Kubernetes + Helm).
- Observability (Prometheus, Grafana, OpenTelemetry).
- Batfish-based validation, Terraform/OpenTofu for IaC.

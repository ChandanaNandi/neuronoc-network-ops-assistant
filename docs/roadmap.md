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

## Phase 5 — LangGraph multi-agent orchestration *(current)*

- LangGraph 1.x `StateGraph` workflow in `app/agents/`. **Deterministic Python nodes** - no LLM calls, no learned behavior.
- 6 nodes wired START → `load_incident` → `anomaly_detection` → `evidence_summary` → `correlation` → `validation` → `report` → END.
- Correlation produces themes ∈ {`routing_failure`, `interface_physical_issue`, `latency_or_loss`, `policy_block`, `unknown`}; validation produces impacts ∈ {`reachability_loss`, `route_missing`, `packet_loss`, `high_latency`, `acl_deny`}.
- Final `IncidentAnalysisReport`: anomaly count, key findings, correlated signals, suspected root cause (templated), validation summary, recommended next steps, `requires_human_review`, average confidence.
- New tables: `agent_runs` (incident_id, workflow_name, status, input/output payload, error, timestamps) and `agent_steps` (run_id, step_name, status, output_payload, error). FK cascade-delete from runs.
- HTTP: `POST /api/agents/incidents/{id}/analyze`, `GET /api/agents/runs/{id}`, `GET /api/agents/incidents/{id}/runs?limit=20`.
- CLI: `python -m app.agents.runner --incident-id <uuid>`, prints final report JSON.
- Synchronous execution only — background jobs and the frontend Agent Inspector are deferred.

## Phase 6 — Ollama / RAG explanation

- Local LLM routing (qwen2.5:7b fast / qwen2.5:14b reasoning).
- pgvector + embeddings over device configs, past incidents, runbooks.
- RCA agent uses RAG to ground hypotheses in real artifacts.

## Phase 7 — Remediation planning

- Ansible playbook drafting (`--check` mode only by default).
- Diff preview in UI; explicit human approval required to apply.
- Rollback plan generation alongside every change.

## Phase 8 — Network lab integration

- Containerlab topology with FRR (and SR Linux where arm64 permits).
- End-to-end: simulated fault → detector → RCA → validation → remediation plan → applied to lab.
- Lima fallback for x86_64-only network images.

## Beyond

- AuthN/AuthZ, multi-tenant.
- Production deployment (Kubernetes + Helm).
- Observability (Prometheus, Grafana, OpenTelemetry).
- Batfish-based validation, Terraform/OpenTofu for IaC.

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

## Phase 4 — Anomaly engine *(current)*

- Deterministic rule-based engine in `app/anomaly/` — **no ML, no LLM, no learned weights**.
- 7 rules (`R001`–`R007`) cover BGP down, route withdrawal, interface error spike, packet loss, latency spike, ACL deny spike, route missing.
- `AnomalyFinding` is a Pydantic model with `rule_id`, `rule_name`, `severity`, `confidence`, `incident_id`, `incident_type`, `summary`, `evidence_refs`, `recommended_next_step`.
- Engine entry points: `analyze_incident(db, incident_id)` and `analyze_open_incidents(db, limit)`. Read-only — findings are recomputed per request and **not persisted** (that lands in Phase 5).
- HTTP: `GET /api/anomalies/incidents/{id}` (404 on miss), `GET /api/anomalies/open?limit=50` (max 100).
- CLI: `python -m app.anomaly.engine --incident-id <uuid>|--open [--limit N]`, prints JSON.
- Deferred to later phases: statistical detectors (z-score, EWMA), windowing / time-series detection, ML / learned models, persistence of findings, backpressure-safe ingest loop (those arrive with the real collector).

## Phase 5 — LangGraph multi-agent orchestration

- Supervisor + Detector / RCA / Validator / Remediator agents.
- Typed agent I/O, tool whitelist, audit log per run.
- Frontend Agent Inspector showing the reasoning trail.

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

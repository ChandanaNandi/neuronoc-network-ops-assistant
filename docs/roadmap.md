# NeuroNOC roadmap

Phases are sequential. Each phase is reviewed and approved before the next begins.

## Phase 1 — Scaffold ✓

- Monorepo layout (`backend/`, `frontend/`, `infra/`, `docs/`).
- FastAPI app with `/health` and `/`.
- Vite + React + TS dashboard shell.
- Docker Compose with Postgres 16 on host port 5433.
- `.env.example`, root docs.
- Hardening: `.gitignore`, Python pinned to 3.12, cwd-independent config loader.

## Phase 2 — Data model and incidents *(current)*

- SQLAlchemy 2.x + Alembic migrations, psycopg 3 driver.
- Tables shipped this phase: `devices`, `incidents`, `incident_events`, `incident_evidence`, `recommendations` (all UUID PKs, timestamped, JSONB payloads where useful).
- Sync `Session` per request; transactional test fixture (savepoint-rolled-back) so tests share the dev DB without polluting it.
- REST endpoints: incident CRUD + event / evidence / recommendation sub-resources, paginated list (newest first).
- Deferred to later phases (call these out explicitly): `interfaces`, `metrics_raw`, `agent_runs`, `agent_messages`. Frontend still shows the static dashboard — wiring it to live incident data is the next sub-task before Phase 3.

## Phase 3 — Collector simulator

- Synthetic SNMP/syslog generator (Python).
- Ingest endpoint that writes to `metrics_raw` / `events`.
- Time-series sanity views.

## Phase 4 — Anomaly engine

- Rule + statistical detectors (z-score, EWMA, threshold breach).
- Detector output writes `events` → groups into `incidents`.
- Backpressure-safe ingest loop.

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

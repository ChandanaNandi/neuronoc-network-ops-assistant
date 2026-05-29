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

## Phase 3 — Collector simulator *(current)*

- Deterministic synthetic incident generator in `app/simulator/` — **not a real collector**, no SNMP / syslog / streaming.
- 4 simulator devices seeded idempotently: `edge-1`, `edge-2`, `core-1`, `branch-1` (all `frrouting`).
- 5 scenarios: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`. Each writes 1 incident + 2–3 events + 2–3 evidence rows + 1 recommendation with realistic JSONB payloads (device/interface/neighbor/prefix/before/after/metric_name/metric_value/unit/observed_at).
- CLI `python -m app.simulator.seed --scenario all|<name>|--reset` and matching optional API at `/api/simulator/{seed,reset}`.
- Every simulator row is tagged (`[simulator]` prefix in `Incident.summary`, `_origin=simulator` in payloads); `--reset` deletes only those rows and never touches operator-created incidents or seeded devices.
- Deferred: actual telemetry ingest, `metrics_raw` time-series, time-series sanity views — those land alongside the real collector in a later sub-phase.

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

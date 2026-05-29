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

## Phase 6 — Ollama RCA explanation + keyword runbook retrieval *(current)*

- Optional local-LLM explanation layer in `app/rca/`. **Local Ollama only** - no cloud-LLM packages.
- `OLLAMA_BASE_URL` (default `http://localhost:11434`) and `OLLAMA_MODEL` (default `qwen2.5:7b-instruct`) added to settings + `.env.example`.
- 5 Markdown runbooks bundled at `app/knowledge/runbooks/`; keyword scoring in `app/knowledge/retriever.py` (no vector store yet).
- Thin Ollama client (`app/llm/ollama.py`) - all failure modes collapse to `OllamaUnavailableError`.
- `RCAExplanation` Pydantic model with `summary`, `likely_root_cause`, `supporting_evidence`, `runbook_references`, `recommended_next_steps`, `unsafe_actions`, `confidence`, `model`, `llm_available`.
- Prompt constrains the model: *use only provided evidence and runbook snippets; never invent commands / hostnames / prefixes / AS numbers*.
- Graceful degradation: if Ollama is unreachable / mis-behaving, the explainer returns a deterministic fallback (`llm_available=False`) built from the Phase 5 report; `require_llm=True` opts into a 503 / nonzero-exit instead.
- HTTP: `POST /api/rca/incidents/{id}/explain[?model=...&require_llm=true]`.
- CLI: `python -m app.rca.explainer --incident-id <uuid> [--model ...] [--require-llm]`.
- The explainer **never** executes remediation.
- Deferred: vector embeddings + pgvector, multi-shot reasoning, agent-tool-calling, persistent RCAExplanation rows.

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

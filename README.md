# NeuroNOC

NeuroNOC is an open-source multi-agent AI NetOps platform for network anomaly detection, root-cause analysis, validation, and remediation planning.

## Phase 10A scope *(current)*

**Remediation approval workflow stub.** Persists a `pending` / `approved` / `rejected` state on every remediation plan plus operator name + timestamp + free-text note. **Approving still does not execute anything** — there is no execution path in the code. This is recorded intent only, intended to make the human-in-the-loop a real database row instead of just a `requires_approval=True` flag.

- 1 small forward-only migration (`f78faa47f6bd`) adds 4 columns to `recommendations`: `approval_status` (NOT NULL, server-defaults to `'pending'`, indexed), `approved_by`, `approved_at`, `approval_note`. Existing rows fill with `pending` at ALTER TABLE time.
- New API: `POST /api/remediation/recommendations/{id}/approve` and `/reject` with `{operator_name, note}` body. 404 if missing, 400 if recommendation_type ≠ `remediation_plan`, idempotent same-state calls update the metadata.
- No auth (the spec is "no auth yet" — operator_name is supplied by caller, persisted verbatim).
- The Phase 7 "no remote-execution imports" safety scan now covers both `app/remediation/` AND `app/api/remediation.py`, so approval code can't silently acquire an execution dependency.
- UI: every remediation plan card shows the approval badge plus `Approve` / `Reject` buttons; clicking either prompts for operator name + optional note, persists the decision, and refreshes the panel. A caveat line ("Plan-only … nothing is executed") sits above the card list.

## Phase 8C scope

**One-shot collector** that scrapes the Phase 8B FRR Compose lab over `docker exec` + `vtysh -c "show ... json"` and writes the BGP state into the existing `Incident` / `IncidentEvent` tables. **No schema change.** No background daemon, no scheduler, no loop — each invocation produces one fresh tagged `Incident` plus one `IncidentEvent` per peer (plus an aggregate snapshot event per router, plus a `lab_bgp_collection_error` event for any router we couldn't reach).

- Read-only towards the lab: only `show ip bgp summary json`. No `clear`, no `conf t`, no config edits.
- Per-collection `Incident.summary` is prefixed `[lab-collector]` so it can be filtered separately from operator-created incidents and Phase 3 simulator data.
- Event types: `lab_bgp_peer_established`, `lab_bgp_peer_not_established`, `lab_bgp_prefix_snapshot` (one per router), `lab_bgp_collection_error` (one per unreachable router).
- Incident severity derived: `low` if every peer is Established, `medium` if any peer is not, `high` if any router could not be scraped.
- API: `POST /api/lab/collect/bgp` (sync; returns a `LabBgpCollectionSummary`).
- CLI: `uv run python -m app.lab.collector --collect` (prints the same summary as JSON).

Phases 0–8B remain intact. See `docs/roadmap.md` for what lands when.

## Phase 7 scope

**Remediation plan generation — DRAFTS ONLY, never executed.** Turns a Phase 5 incident analysis (and optional Phase 6 RCA) into a structured `RemediationPlan` with pre-checks, proposed commands, an Ansible playbook draft, post-checks, rollback steps, validation criteria, and safety notes. **Every plan is hard-pinned `requires_approval=True`. Nothing is run.**

- Plan-only: NO `subprocess`, NO `ansible_runner`, NO `netmiko`, NO `napalm`, NO device connections. A test scans `app/remediation/` and fails the build if any of those tokens are imported.
- Reuses the existing `recommendations` table — **no schema change, no migration**. Plans are persisted as `Recommendation` rows with `recommendation_type="remediation_plan"`; the full structured plan is stored in `details` as a readable summary + fenced JSON block.
- 5 specific templates + 1 default: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`, plus a generic fallback that explicitly demands manual investigation.
- Risky Ansible tasks are gated on `when: false` so the draft cannot run as-is even if someone forgets to read the comments.
- API: `POST /api/remediation/incidents/{id}/plan` (creates + persists), `GET /api/remediation/incidents/{id}/plans` (lists persisted plans newest-first, limit 1–100).
- CLI: `uv run python -m app.remediation.planner --incident-id <uuid> [--persist | --no-persist]`.

Phases 0–6 remain intact. See `docs/roadmap.md` for what lands when.

## Repo layout

```
backend/    FastAPI app (Python 3.12, managed with uv)
frontend/   Vite + React + TS (managed with pnpm)
infra/      Docker support files (Postgres init.sql today)
docs/       architecture.md, roadmap.md
docker-compose.yml
.env.example
SETUP_STATUS.md   # local-env audit + Phase 0 readiness record
```

## Prerequisites

- macOS / Linux
- Docker Desktop (or Docker Engine + Compose v2)
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 20+ and pnpm 10+
- Ollama running on host port 11434 (only needed from Phase 6 onward; the dashboard does not call it yet)

## Start Postgres (Docker)

```bash
docker compose up -d postgres
docker compose ps
```

Postgres listens on **`localhost:5433`** (mapped to the container's 5432). Data persists in the named volume `neuronoc_postgres_data`.

Stop it with:

```bash
docker compose stop postgres   # keep data
# or
docker compose down            # remove container, keep volume
```

Connect with `psql` (the bare `postgresql://` URL — psql does not understand the `+psycopg` driver suffix used by SQLAlchemy):

```bash
psql "postgresql://neuronoc:neuronoc_dev_password@localhost:5433/neuronoc"
```

## Database migrations

The backend uses Alembic. From `backend/`:

```bash
uv run alembic upgrade head            # apply all migrations
uv run alembic revision --autogenerate -m "describe change"   # after editing models
uv run alembic downgrade -1            # roll back last migration
uv run alembic history                 # show migration history
```

`alembic/env.py` reads `DATABASE_URL` from `app.core.config.settings`, so there is one source of truth — set it in the root `.env`.

## Run the backend

```bash
cd backend
uv sync
uv run alembic upgrade head            # ensure schema exists (idempotent)
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Smoke test:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","service":"neuronoc-backend"}

curl -s http://127.0.0.1:8000/api/incidents | jq .
```

Run tests (requires Postgres running on `localhost:5433` with the schema applied):

```bash
cd backend
uv run pytest -q
```

Tests run against the real Postgres but wrap each test in a transaction that is rolled back at the end, so they never persist data into your dev database.

## Seed simulated incidents (Phase 3)

```bash
cd backend

uv run python -m app.simulator.seed --scenario all              # seed all 5 scenarios
uv run python -m app.simulator.seed --scenario bgp_neighbor_down # seed one
uv run python -m app.simulator.seed --reset                      # remove simulator data
```

Or via the API (after starting the backend):

```bash
curl -X POST 'http://127.0.0.1:8000/api/simulator/seed?scenario=all'
curl -X POST  http://127.0.0.1:8000/api/simulator/reset
```

`--reset` only deletes incidents that the simulator created (marked `[simulator]` in `summary`). It leaves the four simulator devices in place and does not touch any operator-created incidents. There is no `--reset-devices` flag yet; if you ever need to start over, delete the devices manually via `psql`.

**Important:** this is fabricated data for development. Real telemetry collection (SNMP, syslog, streaming) lands in a later phase.

## Run the anomaly engine (Phase 4)

CLI:

```bash
cd backend

# one incident (get the id from the simulator output, or `GET /api/incidents`)
uv run python -m app.anomaly.engine --incident-id <uuid>

# every currently-open incident
uv run python -m app.anomaly.engine --open --limit 50
```

Output is a JSON array of `AnomalyFinding` objects (rule_id, rule_name, severity, confidence, summary, evidence_refs, recommended_next_step).

HTTP:

```bash
curl -s http://127.0.0.1:8000/api/anomalies/open?limit=50 | jq .
curl -s http://127.0.0.1:8000/api/anomalies/incidents/<uuid> | jq .
```

The engine is **deterministic and rule-based** — no ML. Findings are *not* persisted anywhere; they are recomputed on every request from the underlying incident / event / evidence rows.

## Run the agent workflow (Phase 5)

CLI:

```bash
cd backend

# get an incident id (e.g. from the simulator)
uv run python -m app.simulator.seed --scenario bgp_neighbor_down

# run the full LangGraph workflow against that incident
uv run python -m app.agents.runner --incident-id <uuid>
```

The CLI prints the `IncidentAnalysisReport` as JSON. The `AgentRun` row and its 6 `AgentStep` rows are persisted to Postgres for audit.

HTTP:

```bash
# run the workflow (synchronous, returns the completed run + all steps)
curl -X POST http://127.0.0.1:8000/api/agents/incidents/<uuid>/analyze | jq .

# fetch one run with its steps
curl -s http://127.0.0.1:8000/api/agents/runs/<run_uuid> | jq .

# list runs for an incident, newest first
curl -s 'http://127.0.0.1:8000/api/agents/incidents/<uuid>/runs?limit=20' | jq .
```

The agents are **deterministic Python nodes coordinated by LangGraph** — no LLM calls, no model inference. Phase 6 will introduce Ollama-backed reasoning under the same orchestration shape.

## Generate RCA explanations (Phase 6, optional Ollama)

CLI:

```bash
cd backend

# Uses Ollama if it is reachable; otherwise prints a deterministic fallback.
uv run python -m app.rca.explainer --incident-id <uuid>

# Override the model (default OLLAMA_MODEL):
uv run python -m app.rca.explainer --incident-id <uuid> --model qwen2.5:14b-instruct

# Fail loudly when Ollama is unreachable instead of falling back:
uv run python -m app.rca.explainer --incident-id <uuid> --require-llm
```

HTTP:

```bash
# Synchronous; returns 200 with a deterministic fallback if Ollama is down.
curl -s -X POST 'http://127.0.0.1:8000/api/rca/incidents/<uuid>/explain' | jq .

# require_llm=true returns 503 if Ollama is down (good for "experimental" UI badges).
curl -s -X POST 'http://127.0.0.1:8000/api/rca/incidents/<uuid>/explain?require_llm=true' | jq .
```

Configuration:

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct      # 7b for fast triage; bump to 14b for richer reasoning
```

Notes:

- The prompt explicitly tells the model to *use only provided evidence and runbook snippets*, never to invent commands / hostnames / prefixes / AS numbers.
- Runbooks live in `backend/app/knowledge/runbooks/*.md`. Retrieval is **keyword-based** (deterministic, no embeddings, no vector store) — real RAG arrives later if and when retrieval quality becomes the bottleneck.
- The explainer **never** executes a remediation action. Every actionable step is gated on explicit human approval.
- Ollama is required to run on the **host** (Apple Metal GPU); containerized Ollama on macOS is CPU-only and ~10–20× slower.

## Collect from the FRR lab (Phase 8C, one-shot)

Bring the Phase 8B lab up first (`./infra/lab/scripts/lab.sh up`), then:

```bash
cd backend

# One synchronous collection. Prints a JSON summary; writes one Incident +
# per-peer events to Postgres.
uv run python -m app.lab.collector --collect
```

HTTP:

```bash
# Equivalent to the CLI; returns the same summary shape.
curl -s -X POST http://127.0.0.1:8000/api/lab/collect/bgp | jq .
```

Each invocation creates a fresh `Incident` tagged `[lab-collector]` plus one `IncidentEvent` per peer. Re-running gives you another fresh row — there is no background ingester yet, that is intentionally out of scope.

The collector calls `docker exec neuronoc-lab-<router> vtysh -c "show ip bgp summary json"`; it never touches a config-changing command. If a router is down or its output isn't JSON, you get a `lab_bgp_collection_error` event instead of a crash.

## Generate a remediation plan (Phase 7, plan-only)

CLI:

```bash
cd backend

# Generate + persist a draft plan (default).
uv run python -m app.remediation.planner --incident-id <uuid>

# Print without persisting.
uv run python -m app.remediation.planner --incident-id <uuid> --no-persist
```

HTTP:

```bash
# Generate + persist; returns the structured RemediationPlan.
curl -s -X POST 'http://127.0.0.1:8000/api/remediation/incidents/<uuid>/plan' | jq .

# List previously-persisted plans for an incident, newest first.
curl -s 'http://127.0.0.1:8000/api/remediation/incidents/<uuid>/plans?limit=20' | jq .
```

Hard safety contract:

- **Nothing is ever executed.** The planner does not shell out, run Ansible, or open a device connection. A CI test parses every `.py` file under `app/remediation/` with the Python AST and fails the build if any actual `import` statement pulls in a remote-execution library (the list lives only in the test).
- Every plan is `requires_approval=True` (re-asserted in the persistence layer as defence-in-depth).
- Every plan includes pre-checks, post-checks, **rollback steps**, validation criteria, and safety notes.
- The Ansible draft is non-executable: risky tasks carry `when: false` plus an explicit `REQUIRES APPROVED CHANGE WINDOW` comment.
- The `interface_errors_spike` template explicitly does not propose a config change as the first action — observation comes first.

## Run the frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open `http://localhost:5173`. The dashboard renders a static shell; all status indicators show "unknown" until later phases wire up the live checks.

Production-style build:

```bash
cd frontend
pnpm build
```

## Configuration

A **single `.env` file at the project root** is shared by Docker Compose (variable interpolation) and the backend (loaded by `pydantic-settings`). Set it up once:

```bash
cp .env.example .env
```

Environment variables exported in your shell always override values from `.env`.

## Intentionally NOT implemented yet

- **Real** telemetry collection (SNMP / syslog / streaming) — Phase 3 ships synthetic data only
- ML / learned anomaly detection — Phase 4 ships deterministic rules only
- Cloud LLMs (OpenAI / Anthropic / etc.) — Phase 6 ships local-Ollama only, with a deterministic fallback
- Vector RAG — Phase 6 ships keyword retrieval over bundled Markdown runbooks
- Remediation **execution** — Phase 7 ships plan-only drafts. No Ansible run, no device contact, no automatic remediation. Approval and human application are required.
- Containerlab network simulation (Phase 8)
- Authentication / authorization
- Async DB / queueing
- Production deployment (k8s, helm, observability stack)

## License & status

Pre-alpha, open-source. Phases 1–8C implemented (scaffold, schema, simulator, anomaly engine, deterministic LangGraph orchestration, local-Ollama RCA explanation with deterministic fallback, plan-only remediation drafts with approval + rollback, Compose FRR network lab + one-shot BGP collector). Not yet usable for real network operations — no continuous telemetry pipeline, no remediation execution, no production lab.

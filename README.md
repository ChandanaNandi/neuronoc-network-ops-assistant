# NeuroNOC

NeuroNOC is an open-source multi-agent AI NetOps platform for network anomaly detection, root-cause analysis, validation, and remediation planning.

## Phase 6 scope *(current)*

**Optional local-LLM RCA explanation, with deterministic fallback.** A small bundled runbook knowledge base + keyword retrieval + a thin Ollama client produce a structured `RCAExplanation` for an incident. **If Ollama is unavailable, the explainer falls back to a deterministic explanation built from the Phase 5 report — nothing crashes, nothing blocks.**

- Local Ollama only (`http://localhost:11434` by default). **No OpenAI, Anthropic, or cloud LLM packages added.**
- Retrieval: deterministic keyword scoring over 5 bundled Markdown runbooks (`bgp`, `interface_errors`, `latency_loss`, `route_missing`, `policy_acl`). **Not a vector store yet.**
- Prompt explicitly constrains the model: *use only provided evidence and runbook snippets; do not invent commands, hostnames, interfaces, prefixes, vendors, or AS numbers*.
- Output validated against a `RCAExplanation` Pydantic schema; if the model's JSON doesn't match, we fall back too.
- API: `POST /api/rca/incidents/{id}/explain` with optional `?model=…&require_llm=true`.
- CLI: `uv run python -m app.rca.explainer --incident-id <uuid> [--model …] [--require-llm]`.
- New env vars: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (default `qwen2.5:7b-instruct`).

Phases 0–5 remain intact. See `docs/roadmap.md` for what lands when.

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
- Ansible / remediation execution (Phase 7) — recommendation rows can be stored, but nothing is applied to real devices
- Containerlab network simulation (Phase 8)
- Authentication / authorization
- Async DB / queueing
- Production deployment (k8s, helm, observability stack)

## License & status

Pre-alpha, open-source. Phases 1–6 implemented (scaffold, schema, simulator, anomaly engine, deterministic LangGraph orchestration, local-Ollama RCA explanation with deterministic fallback). Not yet usable for real network operations — no real telemetry collection, no remediation execution.

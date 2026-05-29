# NeuroNOC

NeuroNOC is an open-source multi-agent AI NetOps platform for network anomaly detection, root-cause analysis, validation, and remediation planning.

## Phase 2 scope *(current)*

Database foundation and incident CRUD APIs — still no agents, LLM calls, anomaly detection, or network automation.

- SQLAlchemy 2.x models: `devices`, `incidents`, `incident_events`, `incident_evidence`, `recommendations` (all UUID PKs, JSONB payload columns).
- Alembic migrations driven from `app.db.models.Base.metadata` (autogenerate-friendly).
- psycopg 3 driver (`postgresql+psycopg://...`).
- Synchronous SQLAlchemy `Session` (async deferred).
- REST endpoints under `/api/incidents` for create, list, fetch by id, plus sub-resources for events / evidence / recommendations.
- Tests run against the **real Docker Postgres** with savepoint-rolled-back transactions, so dev data is never polluted.

Phase 1 (scaffold) and Phase 0 (env audit) remain intact. See `docs/roadmap.md` for what lands when.

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

- Telemetry / collector simulator (Phase 3)
- Anomaly detection (Phase 4)
- LangGraph multi-agent orchestration (Phase 5)
- Ollama / RAG integration (Phase 6)
- Ansible / remediation execution (Phase 7) — recommendation rows can be stored, but nothing is applied to real devices
- Containerlab network simulation (Phase 8)
- Authentication / authorization
- Async DB / queueing
- Production deployment (k8s, helm, observability stack)

## License & status

Pre-alpha, open-source. Phase 1 scaffold — not usable for real network operations yet.

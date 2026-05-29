# NeuroNOC

NeuroNOC is an open-source multi-agent AI NetOps platform for network anomaly detection, root-cause analysis, validation, and remediation planning.

## Phase 3 scope *(current)*

Deterministic **collector simulator** that seeds realistic, fake-but-structured network incident data into the Postgres database. This is **simulated data, not a real collector** — no SNMP, no syslog ingest, no device polling. Later phases will replace this with the real telemetry pipeline.

- 4 simulator devices (edge-1, edge-2, core-1, branch-1) seeded idempotently by hostname.
- 5 scenarios: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`. Each writes 1 incident + 2–3 events + 2–3 evidence rows + 1 recommendation with realistic JSONB payloads (device, interface, neighbor, prefix, before/after, metric_name, etc.).
- CLI: `uv run python -m app.simulator.seed --scenario all|<name>|--reset`.
- Optional API: `POST /api/simulator/seed?scenario=…` and `POST /api/simulator/reset`.
- Every simulator-created row is **tagged** (`[simulator]` in `Incident.summary`, `_origin: simulator` in JSONB payloads, `simulator:<device>` in `source`), so `--reset` removes only simulator data and leaves operator-created incidents alone.

Phases 0–2 (env audit, scaffold, schema) remain intact. See `docs/roadmap.md` for what lands when.

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

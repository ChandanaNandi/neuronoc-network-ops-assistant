# NeuroNOC backend

FastAPI + SQLAlchemy 2 + Alembic + psycopg 3. Phase 2 scope: incident data model and CRUD endpoints. No agents, no LLM, no anomaly detection.

## Setup

```bash
uv sync
```

Make sure Postgres is up (see the root README):

```bash
docker compose up -d postgres
```

## Migrations

```bash
uv run alembic upgrade head                                # apply all
uv run alembic revision --autogenerate -m "describe it"    # after editing models
uv run alembic downgrade -1                                # roll back last
```

`alembic/env.py` pulls `DATABASE_URL` from `app.core.config.settings`, so there is one source of truth (the project-root `.env`, with the defaults in `app/core/config.py` as fallback).

## Run

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Endpoints:

- `GET /` — service banner
- `GET /health` — health probe
- `POST /api/incidents` — create
- `GET /api/incidents?limit=50` — list, newest first (limit 1–100)
- `GET /api/incidents/{id}` — fetch one (404 if missing)
- `POST /api/incidents/{id}/events` — append event
- `POST /api/incidents/{id}/evidence` — attach evidence
- `POST /api/incidents/{id}/recommendations` — attach recommendation
- `POST /api/simulator/seed?scenario=all|<name>` — seed simulated incidents
- `POST /api/simulator/reset` — remove all simulator-created incidents

Interactive docs at `/docs` once running.

## Simulator

The Phase 3 simulator lives in `app/simulator/`. CLI:

```bash
uv run python -m app.simulator.seed --scenario all
uv run python -m app.simulator.seed --scenario bgp_neighbor_down
uv run python -m app.simulator.seed --reset
```

- `ensure_devices` is idempotent on the unique `hostname` column.
- `apply_scenario` writes one Incident + N events + N evidence + 1 recommendation.
- `reset_simulator_data` deletes incidents whose `summary` starts with `[simulator]`; the FK `ON DELETE CASCADE` removes children. Devices are preserved.

Scenarios are defined in `app/simulator/scenarios.py` as pure data — adding a new one means appending a dict to `SCENARIOS`, nothing more.

## Test

```bash
uv run pytest -q
```

Tests use the real Docker Postgres but each test runs inside a transaction that is rolled back at the end (via `Session(..., join_transaction_mode="create_savepoint")`), so the dev DB stays clean.

## Configuration

Reads from environment variables or the **project-root** `.env` file (the same file Docker Compose uses). See `app/core/config.py` for the full list. Defaults match the dev Docker Compose layout (Postgres on host port 5433, psycopg driver).

Set up the shared `.env` once from the repo root:

```bash
cp .env.example .env
```

Note on the DATABASE_URL scheme:

- SQLAlchemy / app: `postgresql+psycopg://...` (driver suffix selects psycopg 3)
- `psql` CLI: `postgresql://...` (no driver suffix — psql rejects it)

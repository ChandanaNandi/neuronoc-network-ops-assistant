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
- `GET /api/anomalies/incidents/{id}` — run rule engine against one incident (404 if missing)
- `GET /api/anomalies/open?limit=50` — run engine against all open incidents (limit 1–100)
- `POST /api/agents/incidents/{id}/analyze` — run the Phase 5 LangGraph workflow, returns the completed `AgentRun` with all 6 steps (404 if incident missing)
- `GET /api/agents/runs/{id}` — fetch one run + its steps (404 if missing)
- `GET /api/agents/incidents/{id}/runs?limit=20` — list runs for an incident, newest first (limit 1–100)
- `POST /api/rca/incidents/{id}/explain[?model=…&require_llm=true]` — Phase 6 RCA explanation (Ollama-backed when reachable, deterministic fallback otherwise; 503 if `require_llm=true` and Ollama is down)
- `POST /api/remediation/incidents/{id}/plan` — generate **and persist** a Phase 7 draft `RemediationPlan` (404 if incident missing)
- `GET /api/remediation/incidents/{id}/plans?limit=20` — list previously persisted plans for an incident, newest first (404 if incident missing)
- `POST /api/remediation/recommendations/{id}/approve` — Phase 10A: record approval intent on a remediation plan. Body `{operator_name, note?}`. 404 if missing, 400 if recommendation_type ≠ `remediation_plan`. **Records intent only — no execution.**
- `POST /api/remediation/recommendations/{id}/reject` — same shape, marks rejected.
- `POST /api/lab/collect/bgp` — Phase 8C: one-shot BGP collection from the Compose FRR lab (writes one tagged `Incident` + per-peer events; requires the Phase 8B lab to be running)

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

## Anomaly engine (Phase 4)

Deterministic rule set in `app/anomaly/rules.py`. Engine entry points live in `app/anomaly/engine.py`:

- `analyze_incident(db, incident_id)` — load one incident's events + evidence, run every rule, return `list[AnomalyFinding]`. Raises `IncidentNotFoundError` if the id is unknown.
- `analyze_open_incidents(db, limit=50)` — flat list of findings across the most-recent N open incidents.

CLI:

```bash
uv run python -m app.anomaly.engine --incident-id <uuid>
uv run python -m app.anomaly.engine --open --limit 50
```

Both modes print a JSON array of findings to stdout. The engine is **read-only**; no findings are persisted. Phase 5 will introduce `agent_runs` for that.

Rules currently shipped (7):

| Rule ID | Rule name | Trigger |
|---|---|---|
| R001 | `bgp_neighbor_down_detected` | `event_type=bgp_state_change` AND `payload.after == "Idle"` |
| R002 | `route_withdrawal_detected` | `event_type=route_withdrawal` OR `payload.metric_name == "withdrawn_prefixes"` with value > 0 |
| R003 | `interface_error_spike_detected` | `payload.metric_name == "input_errors_per_min"` with value > 50 |
| R004 | `packet_loss_detected` | `payload.metric_name` ∈ {`packet_loss_percent`, `loss_pct`, `loss_percent`} with value > 1 |
| R005 | `latency_spike_detected` | `payload.metric_name` ∈ {`latency_ms`, `rtt_ms`} with value > 100 |
| R006 | `acl_deny_spike_detected` | `event_type=traffic_denied` OR `payload.metric_name == "acl_deny_hits"` with value > 0 |
| R007 | `route_missing_detected` | `event_type=route_missing` OR evidence `payload.result == "not_in_table"` |

## LangGraph workflow (Phase 5)

`app/agents/` holds the workflow. Six deterministic nodes (no LLM calls) connected START → load_incident → anomaly_detection → evidence_summary → correlation → validation → report → END.

| Node | Reads | Produces |
|---|---|---|
| `load_incident` | `Incident`, related `IncidentEvent` & `IncidentEvidence` | state slices |
| `anomaly_detection` | state | `AnomalyFinding[]` (re-uses Phase 4 engine) |
| `evidence_summary` | events + evidence | counts, distinct event/evidence types, distinct devices |
| `correlation` | findings | themes ∈ {`routing_failure`, `interface_physical_issue`, `latency_or_loss`, `policy_block`, `unknown`} |
| `validation` | events + evidence | impacts ∈ {`reachability_loss`, `route_missing`, `packet_loss`, `high_latency`, `acl_deny`} |
| `report` | everything above | `IncidentAnalysisReport` dict |

Each node persists an `AgentStep` (with `output_payload` JSONB) for audit. The orchestrating `AgentRun` row tracks `status` (`running` → `completed` / `failed`), `input_payload`, `output_payload` (= final report), and timestamps.

CLI:

```bash
uv run python -m app.agents.runner --incident-id <uuid>
```

Runner module: `app/agents/runner.py` (entry point `run_incident_analysis(db, incident_id) -> AgentRun`).

## RCA explainer (Phase 6 — optional Ollama)

Adds a thin local-LLM explanation layer on top of the deterministic Phase 5 workflow. **Local Ollama only — no cloud LLM packages.**

Modules:
- `app/knowledge/runbooks/` — 5 Markdown runbooks (bgp, interface_errors, latency_loss, route_missing, policy_acl).
- `app/knowledge/retriever.py` — `retrieve_runbooks(query, limit=3)`, keyword scoring (title weight 2x, body weight 1x), deterministic tie-break by filename.
- `app/llm/ollama.py` — `generate_ollama_json(prompt, model=None, timeout_seconds=60)` plus `OllamaUnavailableError`. POSTs `/api/generate` with `format=json, stream=false`. All failure modes (network, non-2xx, empty body, malformed JSON) collapse to `OllamaUnavailableError`.
- `app/rca/explainer.py` — `RCAExplanation` Pydantic model, `build_rca_prompt(report, runbooks) -> str`, and `generate_rca_explanation(db, incident_id, model=None, require_llm=False) -> RCAExplanation`.

Flow inside `generate_rca_explanation`:
1. Verify the incident exists (else `IncidentNotFoundError`).
2. Reuse the most recent completed `AgentRun.output_payload`; otherwise drive the Phase 5 workflow to create one.
3. Retrieve up to 3 runbooks using `incident_type + key_findings + correlated_signals` as the query.
4. Build the prompt (constraint: *use only the provided evidence and runbook snippets*).
5. Call Ollama. If it succeeds, validate the parsed JSON against `RCAExplanation` and return.
6. On any Ollama failure: if `require_llm=True`, re-raise `OllamaUnavailableError`; else return a deterministic fallback with `llm_available=False`.

CLI:

```bash
uv run python -m app.rca.explainer --incident-id <uuid> [--model <name>] [--require-llm]
```

Configuration (project-root `.env`):

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
```

The explainer **never** executes a remediation action. The fallback always includes the unsafe-actions guardrail line.

## Remediation planner (Phase 7 — plan only, never executes)

`app/remediation/` turns an incident into a structured `RemediationPlan`. Plans are drafts for human review.

Modules:
- `app/schemas/remediation.py` — `RemediationPlan` Pydantic model (incident_id, plan_type, title, risk, requires_approval, summary, pre_checks, proposed_commands, proposed_ansible_playbook, post_checks, rollback_steps, validation_criteria, safety_notes, source, confidence).
- `app/remediation/templates.py` — six templates: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`, and a default `generic_investigation` fallback. Template selection picks by `incident_type` first, then by Phase 5 correlation theme.
- `app/remediation/planner.py` — `build_remediation_plan(db, incident_id) -> RemediationPlan` and `persist_remediation_recommendation(db, plan) -> Recommendation`.

Persistence: reuses the existing `recommendations` table — **no migration**. Persisted rows have `recommendation_type="remediation_plan"`, `requires_approval=True` (hard-pinned in the persistence helper as defence-in-depth), and `details` formatted as a human-readable summary plus a fenced JSON block carrying the full structured plan.

Hard safety contract enforced by `tests/test_remediation.py::test_remediation_package_blocks_execution_library_imports`: the test parses every `.py` file under `app/remediation/` with the Python AST and fails the build if any actual `import` statement pulls in a remote-execution library. The list of blocked libraries lives only in the test file - no production module under `app/remediation/` carries those names, even in comments.

Ansible drafts gate every risky task on `when: false` so the file cannot run as-is even by accident.

CLI:

```bash
uv run python -m app.remediation.planner --incident-id <uuid> [--persist | --no-persist]
```

Default is `--persist` (matches the API's behaviour).

## FRR lab collector (Phase 8C — one-shot)

`app/lab/collector.py` scrapes the Phase 8B Compose lab and persists one `Incident` per invocation into the existing schema (no migration). One-shot only — no daemon, no scheduler.

Entry point:

- `collect_lab_bgp_snapshot(db, *, runner=None, routers=None) -> LabBgpCollectionSummary`. The `runner` parameter is a `Callable[[list[str]], tuple[stdout, stderr, returncode]]` and defaults to a `subprocess.run`-backed implementation; tests inject a fake to avoid touching live Docker.

Each invocation:

- creates a new `Incident` whose `summary` starts with `[lab-collector]`
- writes one `IncidentEvent` per (router, peer): `lab_bgp_peer_established` or `lab_bgp_peer_not_established`
- writes one `lab_bgp_prefix_snapshot` per successfully-scraped router (carries router_id, local_as, peer_count)
- writes one `lab_bgp_collection_error` per unreachable / malformed router
- sets incident severity: `low` (all good) / `medium` (some peers not Established) / `high` (any collection error)

CLI:

```bash
uv run python -m app.lab.collector --collect
```

HTTP:

- `POST /api/lab/collect/bgp` → `LabBgpCollectionSummary` (synchronous, 201).

Read-only safety: the collector only runs `vtysh -c "show ip bgp summary json"`. No config-changing commands, no `clear`, no `conf t`.

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

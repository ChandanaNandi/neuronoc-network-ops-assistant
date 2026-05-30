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
- `POST /api/remediation/recommendations/{id}/approve` — record approval intent on a remediation plan. Body: **exactly one** of `{operator_id, note?}` (Phase 13A — resolves to an Operator row + audit FK) or `{operator_name, note?}` (legacy free-form string, Phase 10A). 404 if recommendation missing, 400 if recommendation_type ≠ `remediation_plan`, 404 if `operator_id` is unknown, 422 if neither / both identity fields supplied. **Records intent only — no execution.**
- `POST /api/remediation/recommendations/{id}/reject` — same body shape, marks rejected.
- `POST /api/lab/collect/bgp` — Phase 8C: one-shot BGP collection from the Compose FRR lab (writes one tagged `Incident` + per-peer events; requires the Phase 8B lab to be running)
- `POST /api/lab/collect/snapshot` — Phase 21A/21E: umbrella one-shot lab snapshot — BGP + interface counters/status + route table + running-config — produces **one** Incident (`incident_type=lab_full_snapshot`, tagged `[lab-collector]`) with mixed events + per-router `running_config_snapshot` / `route_table_snapshot` evidence. Same lab-only contract as `/collect/bgp`: read-only, `docker exec` + `vtysh -c "show ..."` only, runtime-asserted against the `LAB_ROUTERS` allow-list and a `show *` prefix + forbidden-token guard. **No external device targeting. No remediation execution. No new dependency.** Severity escalates low → medium (any not-Established peer OR interface with errors OR expected lab loopback missing from BGP route table) → high (any scrape failure OR interface down).
- `GET /api/operators` — Phase 13A: list operators (minimal dev/local identity rows)
- `POST /api/operators` — create an operator (409 on duplicate `display_name`)
- `GET /api/validation/recommendations/{id}/preview` — Phase 16A: read-only validation surface derived from a persisted remediation plan's fenced JSON. Returns `pre_checks` / `post_checks` / `validation_criteria` / `rollback_steps` / `safety_notes` plus `executable=False` and `validation_source="remediation_plan"`. 404 if the recommendation is missing, 400 if `recommendation_type` ≠ `remediation_plan`, 400 if the plan JSON is missing / malformed / fails schema validation. **Plan-only and read-only — no execution, no device contact; `proposed_commands` / `proposed_ansible_playbook` are intentionally NOT included so the response can't be mistaken for an actionable artifact.**
- `GET /api/runbooks/search?q=…&incident_id=…&limit=5` — Phase 17A: deterministic keyword search over the bundled Markdown runbooks (`app/knowledge/runbooks/*.md`). Reuses the Phase 6 in-process scorer (title 2× weight, body 1×, alpha tie-break). Returns `RunbookHit[]` with `{slug, title, score, excerpt, path}`. At least one of `q` / `incident_id` is required (400 otherwise); 404 if `incident_id` is unknown; 422 for `limit` outside `[1, 20]`. `incident_id` derives the query from the incident row's `title + incident_type + summary` with no agent-run side effect. **No embeddings, no pgvector, no LLM call, no network — purely in-process; never executes a command.**
- `POST /api/telemetry/validate` — Phase 18A: validation-only round-trip for a `TelemetryEvent` payload (source / collector_type / hostname / mgmt_ip / device_hint / observed_at / event_type / severity / message / labels / raw). Returns the normalized event on success; 422 with field-level breakdown on schema violation (unknown enum, missing required field, oversized `raw` / `labels`, `extra="forbid"`). **No persistence. No device contact. No socket. No subprocess.** Phase 18A ships only adapter Protocol interfaces (`SNMPAdapter.poll`, `SyslogAdapter.parse`) under `app/telemetry/` — real ingest implementations land in a later phase under separate review. A safety AST scan in `tests/test_telemetry.py` fails the build if any file in `app/telemetry/` or `app/api/telemetry.py` ever imports `subprocess`, `pysnmp`, `easysnmp`, `netsnmp`, `socket`, `asyncio`, `paramiko`, `netmiko`, `napalm`, `scrapli`, `pexpect`, `fabric`, or `ansible_runner`.
- `POST /api/telemetry/correlate/preview` — Phase 18B: pure deterministic mapping from a `TelemetryEvent` to a `TelemetryCorrelationPreview` describing how it WOULD land if real ingest were running — `suggested_incident_type` (`bgp_neighbor_down` / `interface_errors_spike` / `latency_spike` / `route_missing` / `acl_blocking_traffic` / `telemetry_observation`), `suggested_title`, `suggested_severity` (info+notice→low, warning→medium, error→high, critical→critical), `suggested_event_type`, `suggested_event_source`, `suggested_event_payload`, `correlation_key` (narrows by peer / interface / prefix per type), `confidence` (0.9 event_type match, 0.6 message match, 0.3 fallback), `rationale`, `would_create_incident` (False for the generic fallback so unknown traps don't auto-open incidents), `would_create_event` (always True), `persisted: Literal[False]` (pinned by the type system). **Preview only — no correlation against existing incidents, no row writes.** Row-count assertion in tests pins the no-persistence contract. 422 on schema failure.

Interactive docs at `/docs` once running.

## Demo path: real lab → incident → RCA → runbook → plan (lab-only)

End-to-end smoke walk-through that proves the Phase 21A collector feeds the existing incident-detail / RCA / runbook / remediation flow against **real** read-only observations from the FRR lab. Lab-only by design — nothing here contacts an external device, and **no remediation is ever executed**.

1. **Bring up the Phase 8B FRR Compose lab.** (Run from the repo root; the helper script lives at `infra/lab/scripts/lab.sh`.)
   ```bash
   ./infra/lab/scripts/lab.sh up           # docker compose up -d the four FRR routers
   ./infra/lab/scripts/lab.sh bgp all      # quick check: every BGP peer should be Established
   ```

2. **Inject a fault.** Phase 21D's `lab.sh inject` / `heal` helpers make this one command per scenario (lab-only, idempotent — see `infra/lab/README.md` for the full fault matrix and canonical-peer map):
   ```bash
   # Reliable BGP-down on edge-1's canonical peer (core-1 side).
   ./infra/lab/scripts/lab.sh inject bgp-down edge-1

   # Or take an interface down (also flaps any BGP session on that link;
   # richer single-command demo - feeds both link_down_detected AND
   # bgp_neighbor_down_detected findings).
   ./infra/lab/scripts/lab.sh inject iface-down edge-1 eth0

   # Heal (symmetric); BGP convergence takes ~30s after a heal.
   ./infra/lab/scripts/lab.sh heal bgp-down edge-1
   ./infra/lab/scripts/lab.sh heal iface-down edge-1 eth0
   ```
   You can still drop to raw `docker stop` / `vtysh -c "..."` if you want a different shape, but the helper is what the demo flow assumes.

3. **Click `Collect lab snapshot` in the operator console header.** The Phase 21A/21E `POST /api/lab/collect/snapshot` runs `docker exec` against each lab container (read-only `show *` commands only — `_assert_known_router` + `_assert_show_command` gate every call) and writes ONE Incident (`incident_type=lab_full_snapshot`, tagged `[lab-collector]`) carrying BGP events, per-interface status events, route-table events, and per-router `running_config_snapshot` / `route_table_snapshot` evidence rows.

4. **Inspect the new incident** in the detail pane. You should see the not-Established peer event, the interface-status event with the down/errors payload, and the running-config evidence rows.

5. **Click `Run agent analysis`** to drive the Phase 5 LangGraph workflow over the snapshot. The agent run inspector (Phase 15A/15B) shows the six deterministic LangGraph step payloads + the bundled final report.

6. **Click `Generate RCA`** for the Phase 6 explanation (deterministic fallback if Ollama is unavailable; live LLM if reachable).

7. **Use the Runbooks panel** (Phase 17B): click `Use selected incident` — the Phase 17A deterministic scorer surfaces `bgp.md` first for a BGP-shaped lab snapshot.

8. **Click `Generate remediation plan`** for the Phase 7 draft. **The plan is plan-only** — `requires_approval=True` is pinned across the chain.

9. **Optionally `Preview validation`** on the new plan card (Phase 16B) to see only the pre/post checks / validation criteria / rollback steps / safety notes — the actionable `proposed_commands` and `proposed_ansible_playbook` are intentionally absent from the preview.

10. **Approval still required.** Approve via the inline form (Phase 13B operator picker). **Nothing executes.** The approval is intent-only.

End-to-end pinned by `tests/test_lab_collector.py::test_phase21b_lab_snapshot_feeds_full_workflow_end_to_end`, which runs collector → Phase 5 → Phase 6 (forced deterministic fallback) → Phase 7 with a fake docker-exec runner.

**Phase 21C upgrade**: lab snapshot events now feed the existing Phase 4 anomaly rules. The collector's `lab_bgp_peer_not_established` events trigger `bgp_neighbor_down_detected` (R001); `lab_interface_status` events with `payload.has_errors=true` trigger `interface_error_spike_detected` (R003); `lab_interface_status` with `payload.down=true` triggers the new `link_down_detected` (R008). All three route through the existing theme map → Phase 7 selects a **specific** template (`template_bgp_neighbor_down` / `template_interface_errors_spike`) instead of the generic-investigation fallback. The Phase 21B end-to-end test pins this: `plan.plan_type != "generic_investigation"`.

**Phase 21E upgrade**: route-table snapshots now feed `route_missing_detected` (R007) from real lab data. The collector checks each router for the other three routers' expected loopback `/32`s learned via BGP, emits `lab_route_missing` when one is absent, and stores bounded `route_table_snapshot` evidence.

**Remaining boundary**: syslog / ACL deny / packet-loss / latency signals are still not produced by the FRR lab. `acl_deny_spike_detected`, `packet_loss_detected`, `latency_spike_detected`, and `route_withdrawal_detected` remain simulator-only until a lab component actually produces those signal types.

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

### Bounded dev-only watch loop (Phase 11A)

For "watch lab BGP appear in the console without clicking Collect every few seconds", the same module supports a **bounded** loop. This is **dev-only**, not a service:

```bash
# bring the lab up first (see infra/lab/README.md)
./infra/lab/scripts/lab.sh up

# then in another terminal:
cd backend
uv run python -m app.lab.collector --watch --iterations 6 --interval-seconds 10
```

Hard caps enforced in code: `1 ≤ iterations ≤ 100`, `1 ≤ interval-seconds ≤ 3600`. There is no infinite-loop mode, no `--forever`, no background-fork, no cron / launchd integration, no auto-start from the web app. The loop exits cleanly after N iterations and there is no daemon to stop.

Output is **newline-delimited JSON**, one `LabBgpCollectionSummary` per line — pipe to `jq` if you want:

```bash
uv run python -m app.lab.collector --watch --iterations 3 --interval-seconds 5 \
  | jq -c '{i: .incident_id[:8], est: .established_count, err: .errors|length}'
```

Failure handling: per-router scrape failures already surface as the summary's `errors` field and the loop keeps going. If a whole iteration raises an unexpected exception (e.g. DB blip), the loop catches it, prints a JSON `{"iteration": N, "error": "..."}` row, and continues — so a transient blip doesn't kill the run.

Cleanup when done:

```bash
./infra/lab/scripts/lab.sh down
# optional: drop the lab-tagged incidents from this watch run
psql "postgresql://neuronoc:neuronoc_dev_password@localhost:5433/neuronoc" \
  -c "DELETE FROM incidents WHERE summary LIKE '[lab-collector]%';"
```

In the operator console, new lab incidents appear after the next `Refresh` click or after the next 15 s status-grid poll — no UI change is needed for this feature.

## Operators (Phase 13A)

Minimal local identity rows so the approval workflow can attribute decisions to a known row instead of an arbitrary string. **NOT production auth** — no passwords, tokens, sessions, RBAC enforcement, or external IdP integration. The `role` column (`operator` / `admin`) is advisory and not checked anywhere yet.

Seed an operator (idempotent by `display_name`):

```bash
uv run python -m app.operators.seed --name local-operator --role admin
uv run python -m app.operators.seed --name alice --role operator
```

Or via the API:

```bash
curl -s -X POST http://127.0.0.1:8000/api/operators \
  -H 'Content-Type: application/json' \
  -d '{"display_name":"alice","role":"operator"}'

curl -s http://127.0.0.1:8000/api/operators | jq .
```

The approval endpoints (`POST /api/remediation/recommendations/{id}/{approve,reject}`) accept **either**:

- `{"operator_id": "<uuid>", "note": "..."}` — resolves the operator row; `approved_by` is set to `display_name` and `approved_by_operator_id` records the FK for audit trail integrity.
- `{"operator_name": "alice", "note": "..."}` — legacy Phase 10A shape; persisted verbatim; FK stays NULL.

Exactly one of `operator_id` / `operator_name` is required (422 if neither). Existing CLI/script callers that send only `operator_name` continue to work unchanged.

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

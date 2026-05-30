# NeuroNOC roadmap

Phases are sequential. Each phase is reviewed and approved before the next begins.

Conventions:
- ✓ means committed on `main`.
- *(current)* marks the most recent committed scope and the natural next jumping-off point. At any time exactly one phase carries this marker.
- "No execution" / "no auth" guardrails are preserved across every phase below; they are NOT deferred or watered down later in the list.

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

## Phase 3 — Collector simulator ✓

- Deterministic synthetic incident generator in `app/simulator/` — **not a real collector**, no SNMP / syslog / streaming.
- 4 simulator devices, 5 scenarios, CLI + optional API, surgical reset.

## Phase 4 — Anomaly engine ✓

- Deterministic rule-based engine in `app/anomaly/` — no ML, no LLM.
- 7 rules (`R001`–`R007`).
- Read-only: findings recomputed per request, not persisted (persistence arrives in Phase 5 via `agent_runs`).

## Phase 5 — LangGraph multi-agent orchestration ✓

- LangGraph 1.x `StateGraph` workflow in `app/agents/`. Deterministic Python nodes — no LLM calls.
- 6 nodes; per-run / per-step audit in `agent_runs` / `agent_steps`.
- HTTP: `POST /api/agents/incidents/{id}/analyze`, `GET /api/agents/runs/{id}`, `GET /api/agents/incidents/{id}/runs`.
- CLI: `python -m app.agents.runner --incident-id <uuid>`.

## Phase 6 — Ollama RCA explanation + keyword runbook retrieval ✓

- Optional local-LLM explanation layer in `app/rca/`. Local Ollama only.
- Bundled Markdown runbooks + keyword retrieval (no vector store yet).
- Graceful fallback when Ollama is unreachable; `require_llm=True` opts into 503 / non-zero exit.
- HTTP `POST /api/rca/incidents/{id}/explain`, CLI `python -m app.rca.explainer --incident-id <uuid>`.

## Phase 7 — Remediation planning ✓

- Plan-only. **Nothing is executed.** A test scans `app/remediation/` (and `app/api/remediation.py` since Phase 10A) for execution-library imports and fails the build if any appear.
- Reuses the existing `recommendations` table (no migration). Persisted plans use `recommendation_type="remediation_plan"`, `requires_approval=True`, and store the full structured plan in `details` as a readable summary plus a fenced JSON block.
- 5 specific templates + 1 default: `bgp_neighbor_down`, `interface_errors_spike`, `latency_spike`, `route_missing`, `acl_blocking_traffic`, `generic_investigation`.
- Ansible drafts gate every risky task on `when: false` plus a "REQUIRES APPROVED CHANGE WINDOW" comment so the file cannot run as-is.
- HTTP: `POST /api/remediation/incidents/{id}/plan`, `GET /api/remediation/incidents/{id}/plans`. CLI: `python -m app.remediation.planner --incident-id <uuid> [--persist | --no-persist]`.

## Phase 8 — Network lab integration

### Phase 8A — readiness audit ✓

- Read-only host audit picked Compose over Containerlab/Lima for the M4 / disk / arm64 budget.

### Phase 8B — Compose FRR mini-lab ✓

- 4 FRR v8.4.1 routers (edge-1, edge-2, core-1, branch-1) on dedicated `neuronoc_lab_*` bridges.
- eBGP fully Established; loopbacks advertised; `lab.sh` helper for `up`/`down`/`ps`/`logs`/`cli`/`bgp`.
- `pull_policy: never` so the lab refuses to silently pull a different FRR version.

### Phase 8C — one-shot BGP collector ✓

- `app/lab/collector.py` scrapes the four lab routers via `docker exec` + `vtysh -c "show ip bgp summary json"` (read-only).
- Persists into the **existing** `Incident` / `IncidentEvent` schema — no migration.
- One `Incident` per invocation, tagged `[lab-collector]`; events: `lab_bgp_peer_established`, `lab_bgp_peer_not_established`, `lab_bgp_prefix_snapshot`, `lab_bgp_collection_error`.
- HTTP: `POST /api/lab/collect/bgp`. CLI: `python -m app.lab.collector --collect`.

### Later sub-phases (deferred)

- Containerlab topology (when veth pairs, L2 trunks, or multi-vendor are actually needed).
- End-to-end loop: lab fault → detector → RCA → validation → plan → human-approved apply.
- Lima fallback for x86_64-only network images.

## Phase 9A — Operator console UI ✓

- Frontend wired to existing backend endpoints: live status cards, incident list, incident detail with anomaly findings + agent runs + RCA + remediation plans, action buttons (Run agent analysis, Generate RCA, Generate remediation plan, Collect lab BGP snapshot).
- Single-screen master/detail (no router), Vite dev proxy to backend, error banner, loading/empty/error states per section.
- Phase 9A's first commit deliberately surfaced one UX issue: RCA result was getting wiped by an unnecessary detail refetch — fixed mid-phase by splitting the success callback so RCA does not trigger a refetch.

## Phase 9B — Incident event/evidence read views ✓

- Two new read endpoints: `GET /api/incidents/{id}/events` and `GET /api/incidents/{id}/evidence`. Deterministic order: `(created_at ASC, id ASC)`, limit 1–100. 404 if incident missing.
- Schema reuse from Phase 2; no migration.
- UI replaces the Phase 9A "events & evidence not yet exposed" placeholder with real cards and dereferences anomaly `evidence_refs` into human-readable `evt:<type>@<source>` / `ev:<type>@<source>` labels.

## Phase 9C — Operator console smoke flow docs ✓

- Step-by-step demo-flow doc in `frontend/README.md` covering seed → run servers → click path → cleanup.
- Manual browser verification (no Playwright yet at this point).
- A backend-test flake from Phase 9B's ordering change was caught + fixed during this phase's pre-commit validation.

## Phase 10A — Remediation approval workflow stub ✓

- Migration `f78faa47f6bd` adds 4 columns to `recommendations`: `approval_status` (default `'pending'`, indexed), `approved_by`, `approved_at`, `approval_note`.
- `ApprovalStatus` enum (pending / approved / rejected). Initial `ApprovalRequest` schema (`operator_name`, optional `note`).
- HTTP: `POST /api/remediation/recommendations/{id}/approve` and `.../reject`. Idempotent same-state calls update metadata.
- Safety contract preserved: the "no remote-execution imports" AST scan was extended to cover `app/api/remediation.py` so the new approval API code can't acquire an execution dependency.
- UI: per-plan approval badge + Approve/Reject buttons backed by `window.prompt`.
- **No auth, no execution path, no background job.** Approval is persisted intent only.

## Phase 10B — Inline remediation approval form ✓

- Frontend-only UX polish: replaces `window.prompt` with an inline accessible form inside each plan card (operator name input, note textarea, Confirm / Cancel). Only one form open at a time across all cards.
- Submit disabled until operator name is non-empty. Failure keeps the form open; success collapses it and the refreshed plan card carries the new state.
- Backend approval API / schema unchanged.

## Phase 11A — Bounded dev-only watch loop ✓

- `app/lab/collector.py` grows a `--watch --iterations N [--interval-seconds S]` mode that calls `collect_lab_bgp_snapshot` N times with N-1 sleeps in between, then exits. Hard caps `1 ≤ iterations ≤ 100`, `1 ≤ interval-seconds ≤ 3600` enforced at parse time.
- Output is newline-delimited JSON, one summary per iteration. Per-router scrape failures already surface in the summary's `errors` field; an unexpected exception is caught, emitted as a `{"iteration": N, "error": "..."}` row, and the loop continues.
- Original `--collect` single-shot mode unchanged; two modes share a mutually-exclusive required argparse group.
- **No new DB tables, no migrations, no service, no daemon, no scheduler.** Dev/local only. No auto-trigger from the web app.

## Phase 12A — Playwright operator console smoke tests ✓

- `@playwright/test` added as a frontend dev dependency; Chromium browser installed once via `pnpm exec playwright install chromium`.
- Single-browser smoke suite under `frontend/e2e/`: webServer config auto-starts backend + Vite, `globalSetup` resets + seeds simulator data (and from Phase 13A onward, seeds `local-operator`), `globalTeardown` resets simulator data.
- Real backend / real Postgres / real Vite proxy — no mocks. No Ollama / no FRR lab required (RCA test accepts either live model or deterministic fallback).

## Phase 13A — Minimal local operator identity ✓

- Migration `33112e9b5b1c` adds an `operators` table (id, display_name unique + indexed, role default `operator`, created_at). No passwords, no tokens, no sessions.
- Same migration adds nullable `recommendations.approved_by_operator_id` FK (ON DELETE SET NULL) so historical approvals survive operator deletion.
- `Operator` model + `OperatorRole` enum; `OperatorRead` / `OperatorCreate` schemas.
- HTTP: `GET /api/operators`, `POST /api/operators` (409 on duplicate `display_name`).
- CLI: `python -m app.operators.seed --name X --role Y` is idempotent by display_name and updates the role if changed.
- Approval payload (`ApprovalRequest`) now accepts **exactly one** of `operator_id` (resolves to display_name + FK) or legacy `operator_name` (string verbatim, no FK). XOR enforced at both the schema layer and the planner helper. 422 if neither / both, 404 if `operator_id` is unknown.
- UI: approval form gains an operator dropdown (sourced from `/api/operators`); empty/failed list falls back cleanly to the existing free-form name input.
- **Not production auth.** No password storage, no JWT, no OAuth, no SSO, no RBAC enforcement. `role` is advisory only. Existing CLI/script callers that pass only `operator_name` continue to work unchanged.

## Phase 13B — Local operator management UI *(current)*

- `OperatorsPanel` component renders a compact horizontal strip between the status grid and the master/detail: count + one chip per operator (`display_name` · `[role]` · relative-age timestamp) + an inline create form (display_name input + role select).
- Operators state lifted to `App` so the management panel and the approval-form dropdown share a single source of truth — a newly-created operator appears in the dropdown immediately.
- 409 on duplicate `display_name` surfaces as an inline `role="alert"` error; the form stays open with the typed value intact.
- **Frontend-only**, no backend code change, no migration, no new dependency. Same Phase 13A guardrails apply: no passwords, no tokens, no sessions, no RBAC enforcement.
- UI-created operator rows (test names prefixed `e2e-ui-op-` from the e2e suite, plus anything you create manually) accumulate in the dev DB by design. No delete endpoint; cleanup is a one-line `psql` recipe documented in `frontend/README.md`.

## Beyond

- Real authentication (passwords / SSO / SAML) and RBAC enforcement.
- Multi-tenancy.
- Production deployment (Kubernetes + Helm).
- Observability (Prometheus, Grafana, OpenTelemetry).
- Batfish-based validation, Terraform / OpenTofu for IaC.
- Vector RAG over runbooks / device configs / past incidents (pgvector + embeddings).
- Continuous telemetry ingest (real collector, not the Phase 8C one-shot scraper).
- Frontend Agent Inspector + per-step trace UI.

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

## Phase 13B — Local operator management UI ✓

- `OperatorsPanel` component renders a compact horizontal strip between the status grid and the master/detail: count + one chip per operator (`display_name` · `[role]` · relative-age timestamp) + an inline create form (display_name input + role select).
- Operators state lifted to `App` so the management panel and the approval-form dropdown share a single source of truth — a newly-created operator appears in the dropdown immediately.
- 409 on duplicate `display_name` surfaces as an inline `role="alert"` error; the form stays open with the typed value intact.
- **Frontend-only**, no backend code change, no migration, no new dependency. Same Phase 13A guardrails apply: no passwords, no tokens, no sessions, no RBAC enforcement.
- UI-created operator rows (test names prefixed `e2e-ui-op-` from the e2e suite, plus anything you create manually) accumulate in the dev DB by design. No delete endpoint; cleanup is a one-line `psql` recipe documented in `frontend/README.md`.

## Phase 14A — GitHub Actions CI workflow ✓

- `.github/workflows/ci.yml` runs the same local-quality gates on every PR/push: a `backend` job (uv sync → alembic upgrade → pytest -q against a Postgres 16 service container), a `frontend` job (pnpm install → pnpm build = `tsc -b && vite build`), and an `e2e` job (Playwright Chromium smoke against real backend + real Vite proxy + real Postgres; Playwright's `webServer` auto-starts uvicorn and Vite).
- All three jobs run in parallel; e2e does its own setup so it doesn't wait on the others. Postgres dev creds match `app/core/config.py` defaults so no env overrides are needed.
- **Out of scope by design:** no FRR Compose lab (needs Docker-in-Docker), no Ollama (no GPU; RCA test already accepts the deterministic fallback), no remediation execution path. The Phase 7 / Phase 10A AST safety scans still run as part of pytest.

## Phase 14B — Playwright CI diagnostics ✓

- `frontend/playwright.config.ts`: `trace: process.env.CI ? 'retain-on-failure' : 'on-first-retry'`. CI captures a trace on the very first failing run; locally we keep the lighter default so green runs write nothing to disk.
- `retries: 0` kept — flakes stay visible, not silently retried.
- The e2e CI job already uploads both `frontend/playwright-report/` (browsable HTML report) and `frontend/test-results/` (per-test `trace.zip` + failure screenshots) as a single artifact named `playwright-report`, only on failure, with 7-day retention. README has the operator-facing recipe.
- Test/CI configuration + docs only; no app code, no schema, no dependencies, no new CI jobs.

## Phase 15A — Agent run inspector UI ✓

- Frontend-only: enriches the `run-card` block in `IncidentDetail` so operators can inspect a Phase 5 LangGraph audit trail without leaving the incident view.
- Run-card header now shows: status badge, short id, `workflow_name`, started/completed timestamps, step count.
- Each step is its own expandable `<details>` block carrying status badge, `step_name`, `created_at`, and on expand a compact `input` / `output` JSON payload pair (plus `error` text when present). The full final report stays accessible under a collapsed `final report` block.
- Backend API unchanged — `GET /api/agents/runs/{id}` and `GET /api/agents/incidents/{id}/runs` already include `steps[]` with `input_payload`/`output_payload`/`error`/`created_at`/`completed_at`. No schema change, no migration, no dependency added.
- Existing Playwright `.run-card` selector preserved; a new test opens the newest run after `Run agent analysis` and asserts all 6 deterministic LangGraph step names (`load_incident`, `anomaly_detection`, `evidence_summary`, `correlation`, `validation`, `report`) plus that the `report` step's payload renders.
- Read-only inspection: expanding a step does NOT re-execute anything; opening the inspector does NOT contact a device. Same guardrails as every prior phase — no execution path, no auth/RBAC, no LLM behavior change.

## Phase 15B — Agent inspector polish: status, duration, copy ✓

- New tiny helper `formatDuration(startIso, endIso?)` in `frontend/src/api.ts` (alongside `formatDate` / `relativeAge`): returns `<n> ms` for sub-second deltas, `<n.n> s` otherwise, or `null` when the end timestamp is absent. ~10 lines, no dependency.
- Run summary appends ` · <duration>` to the header when `run.completed_at` is present. Step heads gain a payload-shape chip (`· input N keys · output M keys`, `· error` suffix when a step recorded an error) so operators can scan run shape without expanding every step.
- Step badges already use the existing `badge--run-{running,completed,failed}` classes; the chip and duration only add textual context, no new color tokens.
- The collapsed `final report` block grows a `Copy final report JSON` button that calls `navigator.clipboard.writeText(...)` inside try/catch and renders an inline `Copied.` / `Copy failed.` (`role="status"`, `aria-live="polite"`) that auto-clears after 2 s. Per-run keyed state lets multiple run cards each carry their own copy message independently.
- Playwright assertions pinned: a duration token (regex `· \d+(\.\d+)? (ms|s)`), at least one step's `input N keys` / `output N keys` summary chip, and the copy button click surfacing EITHER `Copied.` OR `Copy failed.` (regex). Clipboard contents are intentionally NOT sniffed - clipboard permissions vary between headed/headless and a strict assertion would be brittle without serving no useful operator value.
- Same guardrails as every prior phase — no execution path, no auth/RBAC, no LLM behavior change, no backend API/schema/migration touched, no new dependency.

## Phase 16A — Validation preview model and API ✓

- New package `app/validation/` with a single read-only entry point `build_validation_preview(db, recommendation_id)` that re-projects a persisted remediation plan's validation surface (`pre_checks`, `post_checks`, `validation_criteria`, `rollback_steps`, `safety_notes`). Backend-only.
- Helper `extract_fenced_json(text)` parses the ```` ```json … ``` ```` block written by `app/remediation/planner._render_details`; returns `None` on missing / malformed / non-object content (caller turns that into a `PlanParseError`). Tolerates CRLF-normalised dumps. Unit-tested directly.
- New schema `ValidationPreviewRead` (`app/schemas/validation.py`) pins `executable: Literal[False]` and `validation_source: Literal["remediation_plan"]` so neither field can be flipped by a future caller; intentionally OMITS `proposed_commands` / `proposed_ansible_playbook` so the response cannot be mistaken for an actionable artifact.
- HTTP: `GET /api/validation/recommendations/{id}/preview` → `ValidationPreviewRead`. 404 if recommendation missing, 400 if `recommendation_type` ≠ `remediation_plan`, 400 if the fenced JSON is missing / malformed / fails `RemediationPlan` schema validation. GET-only; never mutates row state.
- No migration. Validation surface is derived 100% from existing `recommendations.details` content (the planner has been writing fenced JSON since Phase 7).
- Safety: parallel AST scan (mirror of `test_remediation.py`'s) added in `test_validation.py`. Fails the build if any module under `app/validation/` or `app/api/validation.py` imports `subprocess`, `ansible_runner`, `netmiko`, `napalm`, `paramiko`, `pexpect`, `fabric`, or `scrapli`.
- **CLI intentionally skipped.** The API returns the exact same Pydantic JSON via a single GET, and the existing `python -m app.remediation.planner --incident-id X --no-persist` already prints the full plan (including all validation fields). Adding a `python -m app.validation.preview` would duplicate the API for zero new capability and would carry ~20 lines of argparse boilerplate + 1–2 CLI tests for the privilege.
- Same guardrails as every prior phase — no execution path, no device connection, no auth/RBAC, no LLM behavior change, no schema migration, no new dependency. Frontend wiring deferred to a later sub-phase.

## Phase 16B — Validation preview UI ✓

- Frontend-only: surfaces the Phase 16A `GET /api/validation/recommendations/{id}/preview` response inside each remediation `plan-card` so operators can inspect the validation surface before approving.
- New TS type `ValidationPreview` (`frontend/src/api.ts`) mirrors `ValidationPreviewRead`; `executable: false` is a literal type so the compiler refuses any reassignment. New `api.getValidationPreview(recommendationId)` fetch wrapper.
- New `Preview validation` button in the `plan-card` action row (next to Approve/Reject). Toggles a collapsible `ValidationPreviewBlock` sub-component rendered below the actions. Loading / error states are per-plan keyed records on `IncidentDetail`, never spilling between plan cards.
- Preview cache is per recommendation id, scoped to the current incident — a second open of the same plan's preview is instant (no network roundtrip). Cache resets only when `incidentId` changes (validation surface is derived from `recommendations.details` JSON, which doesn't mutate on approve/reject).
- Render contract: ONLY `Pre-checks`, `Post-checks`, `Validation criteria`, `Rollback steps`, `Safety notes` plus a header line `source: remediation_plan · executable: false` and the caveat "Read-only. Nothing here executes a command or contacts a device. Proposed commands and Ansible playbook are intentionally omitted." The Playwright assertion pins `executable: false`, `source: remediation_plan`, both `Pre-checks` and `Validation criteria` sections, AND the absence of `proposed_commands` / `proposed_ansible_playbook` strings — so any future refactor that leaks an actionable field will fail the build.
- Existing Approve/Reject behavior is unaffected; the operator strip, approval form, and Phase 15B inspector are untouched.
- Same guardrails as every prior phase — no backend change, no schema, no migration, no dependency, no execution path, no device contact, no auth/RBAC, no LLM behavior change.

## Phase 17A — Lightweight runbook retrieval index ✓

- **Deterministic keyword retrieval, NOT vector RAG yet.** No embeddings, no pgvector, no Ollama dependency, no network calls — the scorer is the same in-process `retrieve_runbooks` from Phase 6 (`app/knowledge/retriever.py`).
- Tiny extension to the Phase 6 retriever: `RetrievedRunbook` gains a `path` field (relative filename inside `app/knowledge/runbooks/`, e.g. `bgp.md`). One-line dataclass addition + one construction-site update. RCA reads only `.title` / `.name` / `.score` / `.snippet`, so this is fully backward-compatible.
- New schema `RunbookHit` (`app/schemas/runbooks.py`, `extra="forbid"`): `{slug, title, score, excerpt, path}`. Excerpt is the bundled first ~280 chars — the endpoint never reads or returns the full runbook file.
- New endpoint `GET /api/runbooks/search?q=...&incident_id=...&limit=5` → `list[RunbookHit]`. Query sources:
  - `q` is free-text, tokenised against title (2× weight) and body, alpha tie-break.
  - `incident_id` (optional) derives a query from the incident row's `title + incident_type + summary`. No agent-run trigger, no LLM side effect, no DB write.
  - At least one of `q` / `incident_id` must be supplied (400 otherwise). 404 if `incident_id` is unknown. 422 for `limit` outside `[1, 20]`.
- **RCA integration left untouched on purpose.** The Phase 6 RCA explainer (`app/rca/explainer.py`) already calls `retrieve_runbooks(...)` cleanly with its own `_retrieval_query()` builder; rewriting that to go through the new HTTP layer would add a roundtrip with no quality gain and would couple RCA to its own router. The Phase 17A endpoint is purely an operator-facing surface.
- Tests: 17 new in `test_runbooks.py` covering loader (every bundled runbook present), the new `path` field, BGP-query → bgp.md ordering, limit, empty/no-match → `[]`, all four API entry points (`q` only, `incident_id` only, both combined, no-match returns `[]`), and the 400/404/422 error surface.
- Same guardrails as every prior phase — no schema migration, no dependency added, no LLM behavior change, no remediation execution path, no device connection, no auth/RBAC, no frontend code touched.

## Phase 17B — Runbook search UI panel ✓

- **Deterministic keyword search UI, NOT vector RAG.** Frontend-only consumer of the Phase 17A `GET /api/runbooks/search` endpoint; no embeddings, no LLM, no full-file fetch.
- New TS type `RunbookHit` (mirrors backend `RunbookHit` schema) and `api.searchRunbooks({q?, incident_id?, limit?})` wrapper.
- New `RunbooksPanel` component lives between `OperatorsPanel` and the master/detail in `App.tsx`. Search input, `Search` button (disabled when `q.trim()` is empty), `Use selected incident` button (disabled until an incident is selected). Enter in the input also triggers a search.
- App passes `selectedIncidentId` down so `Use selected incident` forwards the id and the backend derives the query from the incident row's `title + incident_type + summary` (no agent-run side effect).
- Render contract: title, file path (`<slug>.md`), score, and the bundled ~280-char `excerpt` only. **The full Markdown body is never fetched or rendered** — the panel reads ONLY what the API returns, and the API ships only the bounded excerpt. The Playwright assertion pins the excerpt's length (`<400` chars) AND the absence of a unique string from later in `bgp.md` (`Reload the router`); any future regression that leaked the full file body would fail CI.
- Distinct null vs `[]` results state surfaces a clean empty-state message ("No runbook matched ..."), and 400/404/422 errors render inline via `role="alert"`.
- **Caching skipped on purpose.** The panel's dominant interaction is "type new query" / "click new incident" — not toggle, like Phase 16B's validation preview. A cache would add state without paying for itself; if the operator wants the same result again, the round-trip is one cheap GET against the in-process Phase 17A scorer.
- Existing components (OperatorsPanel, IncidentList, IncidentDetail, agent run inspector, validation preview block) are untouched.
- Same guardrails — no backend change, no schema, no migration, no dependency, no execution path, no device contact, no auth/RBAC, no LLM behavior change.

## Phase 18A — Real telemetry ingest skeleton (adapter interfaces only) ✓

- **Adapter-interface-only — no real collection yet.** New `app/telemetry/` package ships the typed contract for future SNMP / syslog work without opening a socket, importing a real SNMP / syslog library, running a daemon, or persisting anything. Phase 18B+ will plug implementations behind these interfaces under a separate review.
- New `TelemetryEvent` Pydantic model (`app/telemetry/events.py`, `extra="forbid"`): `source` / `collector_type` (enum: `snmp` / `syslog` / `manual`) / `hostname` / `mgmt_ip` / `device_hint` / `observed_at` / `event_type` / `severity` (enum: `info` / `notice` / `warning` / `error` / `critical`) / `message` / `labels` / `raw`. Bounds: `raw` capped at 64 keys × 4096 chars per string value; `labels` capped at 32 entries × 64-char keys × 256-char values. Bounds chosen to fit realistic SNMP traps and syslog frames; raising them in a later phase requires an explicit decision, not silent drift.
- New `SNMPAdapter` and `SyslogAdapter` Protocol classes (`app/telemetry/adapters.py`, `runtime_checkable`). Pure typing contracts: `SNMPAdapter.poll(host, community?, oids?) -> list[TelemetryEvent]` and `SyslogAdapter.parse(line, *, default_hostname?) -> TelemetryEvent | None`. Zero implementation, zero network code.
- New `normalize_manual_event(payload)` helper (`app/telemetry/normalizer.py`): round-trips a dict (or an already-built `TelemetryEvent`) through the model. **No persistence side effect.** Persistence — mapping a `TelemetryEvent` onto an `Incident` / `IncidentEvent` row — would force correlation / dedup / incident-creation rules that are explicitly Phase 18B+ territory; the user signed off on deferring that.
- New endpoint `POST /api/telemetry/validate` → `TelemetryEvent`. Validation-only round-trip. **Does not persist anything** (pinned by a test that snapshots row counts across `incidents`, `incident_events`, `incident_evidence`, `recommendations`, `agent_runs`, `agent_steps` and asserts no divergence). 422 with field-level breakdown on schema failure.
- Safety: new AST scan in `test_telemetry.py` fails the build if any file under `app/telemetry/` or `app/api/telemetry.py` ever imports `subprocess`, `pysnmp`, `easysnmp`, `netsnmp`, `socket`, `asyncio`, `paramiko`, `netmiko`, `napalm`, `scrapli`, `pexpect`, `fabric`, or `ansible_runner`. The forbid list spans SNMP libs, raw network primitives, async server primitives, and the existing remote-execution set.
- Tests: 20 new in `test_telemetry.py` covering schema happy path + defaults, enum validation × 2, required-field validation, `extra="forbid"`, all three bounds (raw key count, raw value len, labels count), `normalize_manual_event` over dict + model + bad payload, `runtime_checkable` Protocol behavior (both adapters), API happy / 422 × 3 / no-persistence row-count assertion, and the AST safety scan.
- Same guardrails as every prior phase — no schema migration, no dependency added, no LLM behavior change, no frontend code touched, no execution path, no device connection, no auth/RBAC, no background daemon/scheduler.

## Phase 18B — Manual telemetry-to-incident correlation preview ✓

- **Preview-only — NO persistence, NO correlation writes yet.** New `app/telemetry/correlator.py` ships a pure function `build_correlation_preview(event)` that maps a `TelemetryEvent` to a `TelemetryCorrelationPreview` describing how it WOULD land if real ingest were running. No DB session, no I/O, no LLM, no device contact. Phase 18C+ will layer real correlation (against open incidents) and write paths under a separate review.
- New `TelemetryCorrelationPreview` model (`extra="forbid"`): `telemetry_event` (full echo), `suggested_incident_type`, `suggested_title`, `suggested_severity`, `suggested_event_type`, `suggested_event_source`, `suggested_event_payload` (nested under a single `telemetry` key so future hand-added IncidentEvent fields don't collide), `correlation_key`, `confidence`, `rationale`, `would_create_incident`, `would_create_event`, and `persisted: Literal[False]` — the type system refuses any reassignment, so this can't quietly turn into a write path.
- **Deterministic rule mapping** (rule order is the contract; pinned by tests): BGP shape → `bgp_neighbor_down`, interface down/error → `interface_errors_spike`, latency/loss/rtt → `latency_spike`, route missing/withdrawn/unreachable → `route_missing`, ACL deny/block → `acl_blocking_traffic`, anything else → `telemetry_observation`. event_type matches weigh higher than message matches (0.9 vs 0.6 confidence; fallback is 0.3). Incident-type names align with the existing simulator scenarios so downstream anomaly + remediation pickup needs no new wiring.
- **Severity collapse**: `TelemetrySeverity` (info / notice / warning / error / critical) → incident severity (low / medium / high / critical). info+notice → low, warning → medium, error → high, critical → critical.
- **Correlation key** narrows per type so collisions don't drop signal: `bgp_neighbor_down::host::peer=…`, `interface_errors_spike::host::if=…`, `route_missing::host::prefix=…`, plus the generic `<type>::<host>` for the rest. Future Phase 18C correlator code will use this exact string to dedupe against open incidents.
- **`would_create_incident`** is True only for matches that landed on a known specific incident_type. The `telemetry_observation` fallback returns `False` so vendor-proprietary or unmappable traps don't auto-open an incident — they'd be logged as an `IncidentEvent` only. **`would_create_event`** is always True — every telemetry event would land as an IncidentEvent row.
- New endpoint `POST /api/telemetry/correlate/preview` → `TelemetryCorrelationPreview`. One-line handler; row-count assertion across `incidents` / `incident_events` / `incident_evidence` / `recommendations` / `agent_runs` / `agent_steps` pins the no-persistence contract structurally.
- Tests: 15 new in `test_telemetry.py` (now 35 total) covering all 5 specific rule mappings + fallback, severity-mapping table covering every level, message-vs-event_type confidence gap, payload nesting, device-descriptor fallback through hostname/hint/ip, `Literal[False]` type pin, API happy path + 422 + no-persistence row-count.
- The existing AST safety scan in `test_telemetry.py` already scans `app/telemetry/` recursively via `rglob("*.py")`, so the new `correlator.py` is covered automatically — same forbid list (`subprocess`, `pysnmp`, `easysnmp`, `netsnmp`, `socket`, `asyncio`, `paramiko`, `netmiko`, `napalm`, `scrapli`, `pexpect`, `fabric`, `ansible_runner`).
- Same guardrails as every prior phase — no schema migration, no dependency added, no LLM behavior change, no frontend code touched, no execution path, no device connection, no auth/RBAC, no background daemon/scheduler.

## Phase 18C — Manual telemetry preview UI ✓

- **Preview-only UI for the Phase 18A/18B telemetry endpoints. NO real SNMP/syslog collection. NO persistence. NO device contact.** Frontend-only consumer of `POST /api/telemetry/validate` and `POST /api/telemetry/correlate/preview` — both endpoints are already pinned no-persistence on the backend by row-count regression tests.
- New TS types `TelemetryEvent`, `TelemetryCorrelationPreview` (with `persisted: false` as a literal type — the compiler refuses any reassignment on the UI side too), plus `TelemetryCollectorType` / `TelemetrySeverity` string-literal unions matching the backend enums.
- New API wrappers `api.validateTelemetry(payload)` and `api.previewTelemetryCorrelation(payload)`. Both accept `unknown` as the payload type so the caller can hand a freshly-parsed JSON object straight through; the backend does the schema enforcement and returns 422 on failure.
- New `TelemetryPanel` component lives below `RunbooksPanel` in `App.tsx`. Collapsible `<details>` element, default-closed, with the caveat "preview only · no persistence · no device contact" visible in the summary even before expansion.
- Body has a JSON textarea pre-filled with a hard-coded BGP-shaped sample (session-only — never read from or written to `localStorage` / `sessionStorage` / cookies), three buttons (`Validate`, `Preview correlation`, `Reset to sample`), two distinct inline error paths ("Invalid JSON (not sent to backend)" for local parse failures, "API rejected payload" for backend 422s), and two result blocks (`Validated event` raw JSON + `Correlation preview` definition-list with `suggested_incident_type`, `suggested_title`, `suggested_severity`, `correlation_key`, `confidence`, `would_create_incident`, `would_create_event`, `persisted`, and a rationale list).
- Both buttons parse the JSON locally first; if the parse fails, the API is NOT called — the inline parse error message explicitly says "not sent to backend" so the operator can distinguish which layer rejected the payload.
- Playwright pins: panel-header caveat, body-intro caveat, BGP-shaped sample → `bgp_neighbor_down`, `persisted: false`, `would_create_incident` + `would_create_event` rows present, invalid JSON → inline parse error rendered without API call.
- Existing components (OperatorsPanel, RunbooksPanel, IncidentList, IncidentDetail, agent inspector, validation preview, runbook search) are untouched.
- Same guardrails — no backend change, no schema, no migration, no dependency, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 18D — Telemetry preview API smoke coverage ✓

- **Client-contract coverage only — NOT a new telemetry capability.** Frontend has no Vitest / Jest harness (only Playwright); per spec, extended Playwright with `page.route()` interception rather than adding a new test framework / dependency.
- Strengthened the existing Phase 18C `Telemetry preview correlates the sample BGP event` test with route interception that counts requests to `/api/telemetry/correlate/preview`. The valid-sample click is now pinned to fire **exactly 1** request; the invalid-JSON click is pinned to fire **0** additional requests (count stays at 1 after a 250 ms settle). Existing inline `"not sent to backend"` parse-error assertion preserved.
- Two new e2e tests pin the client wrapper contracts:
  - `Telemetry preview API: Validate POSTs the JSON body to /api/telemetry/validate` — captures method (POST) + JSON body and asserts the BGP-shaped sample round-trips unchanged.
  - `Telemetry preview API: Preview correlation POSTs the body and the response carries persisted=false` — intercepts both directions via `route.fetch()`, asserts the outbound body matches the sample AND the inbound response carries `persisted: false`, `suggested_incident_type: bgp_neighbor_down`, `would_create_incident: true`, `would_create_event: true`.
- Panel placement and default-collapsed behavior unchanged. No new client code paths added — the panel itself, the API wrappers, and the backend endpoints are all untouched.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 19A — Telemetry preview fixtures for reusable scenarios ✓

- **Client-side examples only — NOT ingestion, replay, or persistence.** Adds a compact `Sample fixture` dropdown above the textarea in the existing `TelemetryPanel`. Selecting an entry replaces the textarea JSON; nothing else in the UI flow changes.
- Five hard-coded fixtures, defined as a `FIXTURES` constant array at module scope in `src/components/TelemetryPanel.tsx`:
  - `bgp` — BGP neighbor down (default)
  - `interface` — Interface down / errors
  - `latency` — Latency spike
  - `route-missing` — Route missing / withdrawn
  - `unknown` — Unknown vendor trap (fallback) — shaped with **zero rule keywords** in `event_type` / `message` so the Phase 18B correlator falls through to `telemetry_observation` deterministically. Pins `would_create_incident: false`, `would_create_event: true`, `persisted: false`.
- New `activeFixtureId` React state + `loadFixture(id)` helper. `Reset to sample` snaps back to the **currently-active** fixture (not always BGP), so picking unknown → editing → reset returns the unknown JSON. **Never reads from or writes to** `localStorage` / `sessionStorage` / cookies / URL params / backend.
- Panel placement (between Runbooks panel and master/detail) and default-collapsed `<details>` behavior preserved verbatim. Existing buttons (`Validate`, `Preview correlation`, `Reset to sample`) keep their semantics; local parse-error short-circuit before API call still in place; API errors still rendered distinct from parse errors.
- Two new Playwright tests added (16 total now):
  - `Telemetry fixture picker swaps the textarea contents to the selected event` — selects each fixture in turn and asserts the textarea reflects the expected `event_type` string. Catches any wiring regression (wrong id, lost entry, label/value drift).
  - `Telemetry preview: unknown vendor fixture falls back to telemetry_observation` — picks `unknown`, clicks `Preview correlation`, and asserts the rendered dl's `<dd>` for `would_create_incident` is `false`, `would_create_event` is `true`, and `persisted` is `false`.
- All 14 prior Phase 18C/18D tests preserved, including the route-interception assertions and the invalid-JSON zero-call assertion.
- Same guardrails — no backend change, no schema, no migration, no dependency added, no new test framework, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 19B — Telemetry fixture reset and stale-result polish ✓

- **UI polish only — NOT new telemetry ingestion or persistence.** Tightens the Phase 19A fixture picker so a displayed result never looks like it belongs to a newly-selected or just-edited payload when it was actually produced by an older one.
- New `clearResultsAndErrors()` helper in `TelemetryPanel` nulls all four ephemeral states (`validateResult`, `correlateResult`, `parseError`, `apiError`) in one call. Wired into:
  - `loadFixture(id)` — picking a different fixture clears prior results.
  - `resetToSample()` — clicking Reset clears prior results.
  - textarea `onChange` — manual edits clear prior results (and only triggers when `e.target.value !== json` so React state churn from re-renders doesn't flicker the result).
- Reset semantics unchanged from Phase 19A — `Reset to sample` still snaps to the **currently-active** fixture, not always BGP. Pinned by the new `Reset to sample after editing returns to the active fixture, not BGP` test which selects `unknown`, edits the textarea to `{ "edited": true }`, clicks Reset, and asserts the textarea is back to `vendor_proprietary_trap` (with an explicit `not.toHaveValue(/bgp_neighbor_down/)` so a regression to "always reset to default" would fail).
- Tiny muted caveat `Results reflect the last submitted payload.` renders above the result blocks only when at least one result is on screen — quiet reminder that re-clicking is what refreshes the output. ~3-line CSS addition; no layout shift on the empty state.
- Two new Playwright tests added (18 total now):
  - `Telemetry preview clears stale result block when fixture is switched` — preview BGP fixture → switch to unknown → asserts the BGP result block goes to `count(0)` BEFORE the next click → re-preview → asserts fresh result is `telemetry_observation` with zero BGP leakage.
  - `Reset to sample after editing returns to the active fixture, not BGP` — see above.
- All 16 prior tests preserved including the Phase 18D route-interception + invalid-JSON zero-call assertions and the Phase 19A fixture-switch + unknown-fallback tests.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added, no new test framework, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 19C — Telemetry fixture accessibility and keyboard coverage ✓

- **UI accessibility / test polish only — NOT a new telemetry capability.** Adds focused Playwright coverage of the Phase 19A/19B fixture picker + preview flow. No markup fix was needed — the existing `TelemetryPanel` already exposed every affordance through programmatic labels.
- Markup audit (no changes required):
  - `<section className="telemetry-panel" aria-label="telemetry preview">` — labelled `<section>` is a "region" landmark
  - `<select id="telemetry-fixture">` is associated with `<label htmlFor="telemetry-fixture">` AND carries `aria-label="telemetry sample fixture"` for test addressability
  - `<textarea id="telemetry-json">` is dual-labelled the same way (`aria-label="telemetry event json"`)
  - Buttons (`Validate`, `Preview correlation`, `Reset to sample`) get their accessible names from text content
  - Error banners use `role="alert"`
  - Native `<details>` / `<summary>` makes the collapse-toggle keyboard-operable (Enter / Space) without any custom JS
- Two new Playwright tests added (20 total now):
  - `Telemetry panel: accessible names and roles are present` — pin-test that the section is reachable as `getByRole('region', { name: 'telemetry preview' })`, the fixture picker via `getByLabel('telemetry sample fixture')`, the textarea via `getByLabel('telemetry event json')`, and all three buttons via `getByRole('button', { name: /^.../ })` with exact-match regex. Smoke check; no axe / no new dependency.
  - `Telemetry panel: keyboard-only flow opens, picks unknown fixture, submits, gets fallback` — focuses `<summary>`, presses Enter to expand, Tabs to the fixture `<select>` (verified by `*:focus` having `id="telemetry-fixture"`), changes to `unknown` via the standard select API (the same OS path screen-reader AT bridges drive), Tabs three more times to land on `Preview correlation` (verified by `*:focus` having text `"Preview correlation"`), presses Enter to submit, and pins the fallback contract (`telemetry_observation`, `would_create_incident: false`, `would_create_event: true`, `persisted: false`).
- All 18 prior tests preserved, including the Phase 19B stale-result + reset-to-active-fixture assertions.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added (axe-core / @axe-core/playwright deliberately NOT added; no new test framework), no markup change, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 20A — Telemetry preview fixture export ✓

- **Client-only export — NOT ingestion, replay, upload, or persistence.** Adds a small `Download JSON` button to `TelemetryPanel` that saves the current textarea contents as a local `.json` file. The button never calls the backend, never writes to `localStorage` / `sessionStorage` / cookies, and there is **no matching import / upload path** — operators can pull JSON out, not push it back in.
- New `downloadJson()` helper uses browser primitives only: `new Blob([json], { type: 'application/json' })` → `URL.createObjectURL` → temporary `<a download>` attached to `document.body` → `click()` → detach → `setTimeout(() => URL.revokeObjectURL(url), 0)` to defer revoke by one tick so the download has fully initiated. Standard pattern; safe across browsers.
- Filename is deterministic and tied to the active fixture id: `telemetry-bgp.json`, `telemetry-interface.json`, `telemetry-latency.json`, `telemetry-route-missing.json`, `telemetry-unknown.json`. Driven by the existing `activeFixtureId` state — no separate state.
- Button is always enabled (download is instant and never conflicts with in-flight Validate / Preview correlation calls). Sits at the end of the existing actions row as `btn--small`, alongside `Reset to sample`. Carries a `title=` tooltip clarifying "client-only export; no backend call".
- **Invalid JSON downloads as raw text.** This is export, not validation — operators may want to save a draft and fix it offline. Pinned by a dedicated test.
- Three new Playwright tests added (23 total now):
  - `Telemetry preview: Download JSON exports the default fixture as telemetry-bgp.json` — asserts `suggestedFilename()` equals `telemetry-bgp.json` and the temp-file body contains `"event_type": "bgp_neighbor_down"` and parses as valid JSON.
  - `Telemetry preview: Download JSON respects the active fixture and makes zero telemetry API calls` — intercepts both `/api/telemetry/validate` and `/api/telemetry/correlate/preview`, switches to the `unknown` fixture, downloads, settles 250 ms, asserts both call counts are **0** and the downloaded body contains `"event_type": "vendor_proprietary_trap"`.
  - `Telemetry preview: Download JSON exports raw textarea contents even when JSON is invalid` — fills the textarea with `{ this is not valid json }`, downloads, asserts the file body is exactly that string.
- All 20 prior tests preserved including Phase 19C keyboard + accessible-names assertions, Phase 19B stale-result + reset-to-active-fixture, and Phase 18D route-interception + invalid-JSON zero-call.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added, no API surface change, no upload/import path, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 20B — Telemetry export filename sanitization ✓

- **Filename hardening only — NOT a new capability, no upload, no import, no persistence.** Replaces the direct `\`telemetry-${activeFixtureId}.json\`` interpolation in `downloadJson()` with a small `safeTelemetryFilename(id)` helper. Defends against path traversal (`foo/../bar` → `foo-bar`), control characters, Unicode oddness (zero-width spaces, etc.), and empty input if any future fixture id ships with surprising characters.
- Pipeline (each step is one regex / string call, no allocations beyond the pipeline): lowercase → trim → replace runs of non-`[a-z0-9-]` with single `-` → collapse repeated `-` → trim leading/trailing `-` → fallback to `event` if the result is empty. Returns `telemetry-<safe>.json`.
- **The five current fixture filenames are pinned byte-for-byte unchanged**: `telemetry-bgp.json`, `telemetry-interface.json`, `telemetry-latency.json`, `telemetry-route-missing.json`, `telemetry-unknown.json`. Verified by tracing each id through the pipeline (no character outside `[a-z0-9-]`, no leading/trailing hyphens, no consecutive hyphens). Pinned by the new test that downloads under all 5 fixtures in sequence and asserts the exact `suggestedFilename()`.
- One new Playwright test added (24 total now): `Telemetry preview: Download JSON filename is sanitized and stable for all five fixtures` iterates the 5 known fixtures, triggers a download per fixture, and asserts the exact `suggestedFilename()` byte-for-byte. The Phase 20A `telemetry-bgp.json` + `telemetry-unknown.json` filename assertions remain in their original tests too, so the pin lives at two layers.
- **Fallback path (`telemetry-event.json`) is NOT directly tested through the UI.** The current `<select>` exposes only the five known fixture ids, and triggering the fallback would require either exporting the helper for a unit test (no unit-test harness exists — Vitest/Jest is deliberately not in the repo) or adding test-only UI scaffolding that has no operator value. Documented here so future readers understand the gap is intentional.
- All 23 prior tests preserved including the Phase 20A `Download JSON respects the active fixture and makes zero telemetry API calls` route-interception test and the invalid-JSON-raw-text download test.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added, no API surface change, no upload/import path, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Phase 21A — Lab snapshot collector: BGP + interfaces + config ✓

- **First real read-only collection from the Phase 8B FRR lab, building toward the BGP-flap + interface-errors + config-evidence → incident → RCA → runbook → remediation-plan demo.** Strictly lab-only: `docker exec` against the known `neuronoc-lab-*` containers, every vtysh command starts with `show ` and is read-only. **No Netmiko / pyATS / Genie / NAPALM / Paramiko added; no new dependency at all.**
- New umbrella `collect_lab_snapshot(db, *, runner=None, routers=None) -> LabSnapshotSummary` in `app/lab/collector.py`. Creates ONE `Incident` per call, `incident_type=lab_full_snapshot`, tagged `[lab-collector]`. Per-router output: BGP events (reusing the Phase 8C shapes), one `lab_interface_status` event per interface, one `running_config_snapshot` evidence row (capped at 64 KB with a `truncated` flag), and per-scrape `lab_*_collection_error` events on failure.
- **Two new defense-in-depth guards** wired into both `_vtysh_json` and the new `_vtysh_text` (the helper that handles `show running-config` since it's not JSON):
  - `_assert_known_router(name)` — rejects any name not in `LAB_ROUTERS_SET` (`edge-1`, `edge-2`, `core-1`, `branch-1`). Stops `docker exec` from ever being pointed at an arbitrary container via a wild `routers=` kwarg.
  - `_assert_show_command(cmd)` — rejects any vtysh command that doesn't start with `show ` (case-insensitive) OR contains a forbidden token (`clear`, `reset`, `debug`, `configure`, `conf t`, `write`, `copy`, `reload`, `delete`, `enable`, `no debug`). Whole-word matching via space-padded haystack — no false-positives like `showclear`.
- **Severity escalation**: low (everything healthy) → medium (any not-Established peer OR any interface with `input_errors > 0` or `output_errors > 0`) → high (any per-scrape failure OR any interface admin/oper down OR `lineProtocol` containing "down").
- New endpoint `POST /api/lab/collect/snapshot` → `LabSnapshotSummary`. CLI extended with `--collect-snapshot` (mutually exclusive with the existing `--collect` and `--watch`).
- **Backward-compat pinned**: `collect_lab_bgp_snapshot()` and `POST /api/lab/collect/bgp` are unchanged in behavior AND shape; a dedicated test (`test_api_collect_bgp_unchanged_by_phase21a`) verifies the BGP-only response body does NOT carry the new snapshot fields. Phase 8C tests (17) all still pass without modification.
- Frontend: new `Collect lab snapshot` button next to the existing `Collect lab BGP snapshot` in the header. New `api.collectLabSnapshot()` + `LabSnapshotSummary` interface mirror the backend. Both buttons are disabled while either is in flight. The selected incident jumps to the new snapshot Incident on success so the operator sees it immediately.
- Tests: 15 new in `test_lab_collector.py` (32 total) covering: 2 `_assert_known_router` paths, 3 `_assert_show_command` paths (including forbidden-token-in-show), umbrella happy-path with 4 routers / 6 peers / 4 interfaces / 4 configs, per-router config evidence shape, three severity escalations (one peer not-Established → medium / interface errors → medium / interface down → high), scrape-failure → high + dedicated error event, oversize config → `truncated=True` with content capped at 64 KB, new API endpoint happy-path, BGP-only endpoint shape unchanged, and new `--collect-snapshot` CLI mode.
- **Out of scope** (deferred): per-issue incident decomposition (single umbrella Incident only — Phase 21B candidate), route table sample, syslog sample, latency/ping sample, Netmiko/pyATS/NAPALM (explicitly never in this phase), external device targeting.
- Same guardrails as every prior phase — no schema migration, no dependency added, no LLM behavior change, no remediation execution path, no device connection outside lab containers, no auth/RBAC, no background daemon/scheduler.

## Phase 21B — Lab snapshot demo-path test + docs ✓

- **Tests + docs only — NOT a new capability.** Proves that the Phase 21A `collect_lab_snapshot()` output is consumable end-to-end by the existing chain (Phase 5 LangGraph → Phase 6 RCA → Phase 7 remediation planner) with no new plumbing.
- One new backend integration test: `test_phase21b_lab_snapshot_feeds_full_workflow_end_to_end`. Builds a fault scenario (1 not-Established BGP peer + 1 interface with input errors via the existing `_snapshot_runner` fake), collects the snapshot, then walks the full chain:
  1. Asserts `incident_type=lab_full_snapshot` + severity `medium` + `lab_bgp_peer_not_established` event present + `lab_interface_status` event present + 4 `running_config_snapshot` evidence rows.
  2. Runs `run_incident_analysis()` directly and asserts the resulting AgentRun completes with all 6 LangGraph step rows.
  3. Calls `generate_rca_explanation(require_llm=False)` with `generate_ollama_json` stubbed to raise `OllamaUnavailableError` — proves the deterministic fallback branch actually fires (`llm_available=False` asserted), so the test never depends on a live Ollama daemon.
  4. Calls `build_remediation_plan()` and asserts the returned plan has a non-empty title, a non-empty `plan_type`, and `requires_approval=True` — **pinning the plan-only contract end-to-end**. Also asserts exactly one AgentRun exists, confirming the planner reused the workflow's prior run rather than triggering a second one.
- **Boundary honestly documented**: the lab event types (`lab_bgp_peer_not_established`, `lab_interface_status`) don't currently match the existing Phase 4 anomaly rules (which look for simulator-shaped event_type strings like `bgp_state_change`), so the chain produces zero anomaly findings against lab data today and the planner falls through to the generic-investigation template. The chain still runs to completion — Phase 21B pins the data SHAPE is compatible; teaching the anomaly engine the lab event types is explicitly deferred to a Phase 21C+ candidate.
- **Playwright skipped on purpose.** Mocking `POST /api/lab/collect/snapshot` cleanly in the e2e suite would also require mocking the subsequent `GET /api/incidents/<id>` and detail-pane fetches (since the mock incident wouldn't exist in the test DB), which is more test infrastructure than this phase warrants. The user's spec explicitly carries that escape hatch ("If mocking this cleanly would require new test infrastructure, skip Playwright and keep it backend-only"). The 24/24 e2e suite is unchanged.
- New "Demo path" section in `backend/README.md` walks the recruiter-demo flow: bring up the FRR lab → inject BGP/interface fault (`docker stop` or `vtysh ... shutdown` an interface) → click `Collect lab snapshot` → inspect the incident → run agent analysis → generate RCA → search runbook → generate remediation plan → approval still required. Explicitly lab-only; explicitly no execution.
- Same guardrails as every prior phase — no new dependency, no schema migration, no external device contact (fake `_snapshot_runner` intercepts every `docker exec`), no remediation execution, no telemetry UI work, no Netmiko / pyATS / NAPALM / Paramiko, no frontend code change.

## Phase 21C — Map lab snapshot events into anomaly findings ✓

- **Backend rules + tests + docs only.** Closes the Phase 21B boundary: lab snapshot events now feed the existing Phase 4 anomaly rules, so a real lab fault produces specific findings that route Phase 7 to a specific remediation template instead of `generic_investigation`. No new collector behavior, no schema, no migration, no new dependency, no frontend change.
- **Three event-to-finding mappings** (all reuse existing finding categories so downstream Phase 5 themes + Phase 7 templates pick up automatically):

  | Lab event | Payload trigger | → finding | rule_id | Existing rule? |
  |---|---|---|---|---|
  | `lab_bgp_peer_not_established` | (presence is sufficient — collector only emits when state ≠ Established) | `bgp_neighbor_down_detected` | R001 | extended in place |
  | `lab_interface_status` | `payload.has_errors == True` | `interface_error_spike_detected` | R003 | extended in place |
  | `lab_interface_status` | `payload.down == True` | `link_down_detected` | R008 | **new rule** `rule_link_down` |
- **Simulator behavior preserved verbatim** — the rule predicates handle both payload shapes (simulator's `device`/`neighbor`/`metric_name=input_errors_per_min` AND the lab collector's `router`/`peer`/`event_type=lab_*`). 58/58 prior anomaly+workflow+remediation tests still pass; a dedicated `test_simulator_bgp_summary_text_preserved_after_phase21c` belt-and-suspenders pins the original simulator summary text.
- `_device()` helper now also recognizes `payload.router` (the docker container short name the lab collector emits), with precedence `device > router > source > unknown`.
- New `"link_down_detected": "interface_physical_issue"` entry in `app/agents/workflow.py:_THEME_MAP` so the new finding routes to the existing interface remediation template (no new template needed).
- **Plan-type upgrade pinned by the Phase 21B end-to-end test**: against the lab fault scenario (1 not-Established BGP peer + 1 interface with errors), `analyze_incident()` now returns specific findings; the Phase 5 workflow's `_THEME_MAP` produces `{"routing_failure", "interface_physical_issue"}`; Phase 7's `pick_template` iterates sorted themes and lands on `template_interface_errors_spike` first (plan_type `interface_physical_investigation`). Test asserts `plan.plan_type != "generic_investigation"` AND `len(direct_findings) >= 1` AND the finding names intersect the expected `{bgp_neighbor_down_detected, interface_error_spike_detected, link_down_detected}` set.
- Six new focused anomaly tests (21 total in `test_anomaly.py`): the three positive mappings (BGP not-established → BGP finding; lab interface with `has_errors=True` → interface error finding; lab interface with `down=True` → link-down finding), the negative pin (healthy lab interface → no finding), the simulator-summary regression guard (asserts `"transitioned to Idle"` is present AND `"is not Established"` is NOT — pinning the simulator phrasing byte-for-byte), and a companion lab-phrasing test (asserts the lab path uses `"is not Established"` AND that the simulator phrasing has not leaked in — pinning both directions of the per-event-type branch).
- README "Demo path" section updated: lab events now feed anomaly findings; remaining gap (route-table-sample + syslog-sample collectors not built yet) honestly documented.
- Same guardrails as every prior phase — no new dependency, no schema migration, no external device contact (rule tests use direct event-payload fixtures, no docker), no remediation execution, no LLM behavior change, no Netmiko / pyATS / NAPALM / Paramiko, no frontend code change.

## Phase 21D — Lab fault-injection helper (demo ergonomics) ✓

- **Shell + docs + smoke test only — NO backend, frontend, dependency, or schema changes.** Makes the recruiter-demo flow one command per scenario instead of remembering raw `docker stop` / `vtysh ... shutdown` recipes.
- Top-level verbs (per user direction; **not** `inject heal ...`):
  - `lab.sh inject bgp-down <router>` — `vtysh "router bgp <AS>" -c "neighbor <CANONICAL_PEER> shutdown"` inside the router.
  - `lab.sh heal bgp-down <router>` — symmetric `no shutdown`.
  - `lab.sh inject iface-down <router> <iface>` — `ip link set <iface> down` inside the container (works because the FRR images carry `NET_ADMIN`).
  - `lab.sh heal iface-down <router> <iface>` — `ip link set <iface> up`.
- **Canonical-only `bgp-down`** (no optional peer arg per user direction — the point is demo repeatability, not a mini fault-injection framework). Hardcoded peer map per router matches the Phase 8B topology and is chosen to produce a partial fault (the router's other peers stay up so the collector still scrapes successfully): `edge-1→172.30.1.2`, `edge-2→172.30.2.2`, `core-1→172.30.1.1`, `branch-1→172.30.3.2`. Operators who want a different peer drop to `lab.sh cli <router>` and use vtysh directly.
- **Idempotent by construction**: FRR accepts `neighbor X shutdown` / `no neighbor X shutdown` as no-ops when already in the requested state; `ip link set ... down` / `up` on an already-down / already-up interface is a no-op. Re-running `inject` or `heal` repeatedly never leaves the lab worse.
- **No new `status` command**; existing `lab.sh bgp all` is the post-inject verification step. Documented inline in `usage()` and the new README section.
- **`iface-errors` deferred**, rationale documented in `infra/lab/README.md`: `tc netem corrupt N%` would either tear BGP down (conflating with `bgp-down`) or require sustained traffic + stable error generation that's fragile for a demo. Note that `iface-down` already triggers BOTH Phase 21C R008 `link_down_detected` AND knocks down any BGP session on that link — richer single-command demo than `bgp-down` alone.
- **Bash 3.2 portable**: macOS ships bash 3.2 which doesn't support `declare -A`, so the canonical-peer / local-AS lookups are case-statement functions (`_canonical_bgp_peer`, `_local_as`) rather than associative arrays. Verified by the smoke test.
- **33-assertion smoke** in `infra/lab/scripts/test_lab.sh`. Runs in <1 s with **no docker daemon required** — half are argument-validation (unknown fault type, missing args, unknown router, usage text mentions the new subcommands, etc.) and half are fake-docker capture assertions: a stub `docker` script first on PATH captures every `docker exec` argv and the test asserts the exact vtysh / `ip link` command-string lab.sh emits per fault. Critically, this catches FRR-syntax drift — the suite has explicit pins that inject emits `neighbor X shutdown` and heal emits `no neighbor X shutdown` (NOT the syntactically-invalid `neighbor X no shutdown`). Actual fault mechanics against a live lab are documented as a manual validation recipe in `infra/lab/README.md` because the lab takes ~25-30 s per `up`/heal-converge cycle, too slow for CI.
- Files (5): `infra/lab/scripts/lab.sh` (modified), `infra/lab/scripts/test_lab.sh` (new, executable), `infra/lab/README.md` (modified — fault matrix, canonical-peer map, manual validation recipe, deferred-`iface-errors` rationale), `backend/README.md` (modified — cross-reference in Demo path so `docker stop` no longer appears as the inject step), `docs/roadmap.md` (modified).
- Same guardrails as every prior phase — lab-only (no host packet tricks, no privileged kernel knobs, no arbitrary container targeting), no new dependency, no schema, no migration, no backend/frontend code change, no remediation execution, no LLM behavior change.

## Phase 21E — Lab route-table snapshots ✓

- **Backend collector + rule tests + docs only.** Extends the Phase 21A umbrella snapshot with `show ip route json` per router. No frontend, no dependency, no schema migration, no template-priority change, no syslog.
- New lab-topology-scoped expected loopback table: each router expects the other three routers' loopback `/32`s via BGP (`edge-1→10.0.0.12/32,10.0.0.21/32,10.0.0.31/32`, etc.). This mirrors Phase 21D's canonical-peer map: small, explicit, and tied to the frozen Phase 8B FRR topology.
- Per healthy router, collector writes:
  - one `lab_route_table_snapshot` event with `total_route_count`, `bgp_route_count`, `connected_route_count`, `expected_bgp_loopbacks`, `present_bgp_loopbacks`, and `missing_bgp_loopbacks`;
  - one bounded `route_table_snapshot` evidence row containing the rendered `show ip route json` payload, capped at 64 KB with `truncated`, `byte_count`, and `max_bytes` metadata.
- Per missing expected loopback, collector writes one `lab_route_missing` event with `{router, prefix, expected_protocol="bgp", _origin="lab-collector"}`. Missing routes lift snapshot severity to `medium`; route scrape failure writes `lab_route_collection_error` and lifts severity to `high`.
- `LabSnapshotSummary` is additively extended with `routes_collected` and `routes_missing`. Existing fields remain unchanged; BGP-only `POST /api/lab/collect/bgp` response shape is still pinned separately.
- `rule_route_missing` (R007) now also matches `lab_route_missing`, reusing the existing `route_missing_detected` finding and downstream `routing_failure` theme/template behavior.
- Focused tests added: healthy route tables emit no missing routes, one missing expected loopback emits one `lab_route_missing` and medium severity, route scrape failure emits `lab_route_collection_error` and high severity, route-table evidence rows are written per healthy router, expected-loopback topology is pinned, and `lab_route_missing` produces R007.
- **Syslog deliberately deferred.** The FRR lab logs to stdout, so `docker logs --tail N` is mechanically possible, but it does not close an ACL-deny/packet-loss/latency rule gap and would add a second collection surface with weak signal. A future firewall or traffic component should introduce those signals instead.
- Real-lab rule coverage after 21E: covered from lab data: R001 `bgp_neighbor_down_detected`, R003 `interface_error_spike_detected` via collector payload, R007 `route_missing_detected`, R008 `link_down_detected`. Simulator-only / no lab signal: R002 `route_withdrawal_detected`, R004 `packet_loss_detected`, R005 `latency_spike_detected`, R006 `acl_deny_spike_detected`.
- Same guardrails as every prior lab phase — read-only `show *` commands only, known-router allow-list, forbidden-token command guard, no external device targeting, no remediation execution, no LLM behavior change.

## Phase 22A — Real telemetry persistence model and API ✓

- **Backend only — no frontend, no streaming, no daemon, no scheduler, no auto-correlation.** Turns the Phase 18 telemetry preview track from preview-only into a persisted sample store. The existing `/validate` and `/correlate/preview` endpoints stay **byte-for-byte unchanged** in behavior; persistence is a NEW endpoint triple, not a modification.
- **New table `telemetry_observations`** via Alembic migration `82c1f3c27505` (parent `33112e9b5b1c`):
  - `id` UUID PK (`gen_random_uuid()` server default)
  - `source` VARCHAR(256) NOT NULL
  - `vendor` VARCHAR(64) NULL — populated from `event.labels["vendor"]` at persist time when present
  - `observation_type` VARCHAR(64) NOT NULL (the `TelemetryEvent.event_type`)
  - `payload` JSONB NOT NULL — full normalized `TelemetryEvent` dict from `model_dump(mode="json")`
  - `received_at` TIMESTAMPTZ NOT NULL, `now()` server default, indexed
  - `created_incident_id` UUID NULL, FK `incidents.id` `ON DELETE SET NULL`, indexed
- **New SQLAlchemy model** `TelemetryObservation` in `app/db/models.py` mirroring the table.
- **New schemas** in `app/schemas/telemetry.py` (new file): `TelemetryObservationRead` (`from_attributes=True`) carrying `id`, `source`, `vendor`, `observation_type`, `payload`, `received_at`, `created_incident_id`. Request body for create reuses the existing `TelemetryEvent` schema directly so the validation contract is single-sourced.
- **New persistence helper** `persist_telemetry_observation(db, event)` in `app/telemetry/persistence.py` — single function, takes a validated `TelemetryEvent`, writes one row, returns the refreshed ORM instance. Vendor lookup is conservative: pulls from `event.labels["vendor"]` only when present; never guessed. Phase 22A pins `created_incident_id=NULL` on every row — no auto-correlation.
- **Three new endpoints** in `app/api/telemetry.py`:
  - `POST /api/telemetry/observations` → 201 + `TelemetryObservationRead`. Pydantic validates the body. No auto-incident creation. 422 on schema failure.
  - `GET /api/telemetry/observations?limit=N` → newest first. Ordering is `received_at DESC, id DESC` — the secondary key gives deterministic ordering when timestamps tie (the savepoint-based test fixture exposed this case because Postgres `now()` is transaction-start time). Limit 1..100; 422 for out-of-range.
  - `GET /api/telemetry/observations/{id}` → 200 or 404.
- **14 new focused tests** in `backend/tests/test_telemetry_observations.py`: direct helper writes the row; vendor extracted from labels; missing vendor label → NULL; POST returns 201 + persisted row; POST 422 on invalid event; POST does NOT auto-create Incident (row count pinned); JSONB round-trip; GET list returns newest first (uses direct-DB-insert with explicit ascending timestamps to bypass the shared-`now()` test-fixture limitation); GET limit respected; GET limit out-of-range → 422; GET single 404; GET single returns persisted row; `/validate` remains non-persisting (`telemetry_observations` count unchanged after POST `/validate`); `/correlate/preview` remains non-persisting AND still returns `persisted: false`.
- AST safety scan (`test_telemetry_package_blocks_network_and_execution_imports`) automatically covers the new `app/telemetry/persistence.py` because it scans `app/telemetry/` recursively — same 13-root forbid list applies.
- **Out of scope** (explicitly deferred): auto-incident creation from observations, background ingest / scheduler, streaming, real SNMP/syslog collection, auth/RBAC, frontend wiring, telemetry-to-incident automation, any LLM call. The migration's `created_incident_id` FK exists today only so a future phase can populate it without another schema change.
- Same guardrails as every prior phase — no new dependency, no external network/device contact, no remediation execution.

## Phase 22B — Persisted telemetry correlation ✓

- **Backend only — no migration, no new dependency, no frontend, no streaming, no daemon, no LLM call, no remediation execution.** Closes the Phase 22A boundary: the `created_incident_id` FK is now actually populated by a deterministic, on-demand correlate call.
- **New service function** `correlate_persisted_telemetry_observation(db, observation_id)` in `app/telemetry/correlate_persisted.py` (new file). Loads a `TelemetryObservation`, round-trips its `payload` JSONB back through the `TelemetryEvent` schema, runs the existing Phase 18B `build_correlation_preview`, and acts on three deterministic paths:
  1. **Already linked** (`obs.created_incident_id is not None`): returns the existing Incident link, `incident_created=False`. Idempotent re-run path.
  2. **Generic fallback** (`preview.would_create_incident is False`): no Incident created, `correlated=False`, `created_incident_id` stays NULL. Matches Phase 18B's `telemetry_observation` semantics — unknown vendor traps don't auto-open incidents.
  3. **Specific match**: creates ONE `Incident` with `incident_type=preview.suggested_incident_type`, `title=preview.suggested_title`, `severity=preview.suggested_severity` + ONE `IncidentEvent` with `event_type=preview.suggested_event_type`, `source=preview.suggested_event_source`, `payload=preview.suggested_event_payload`, then stamps `created_incident_id` on the observation.
- **New schema** `PersistedCorrelationResult` (`app/schemas/telemetry.py`, `extra="forbid"`): `{observation_id, correlated, incident_created, incident_id, suggested_incident_type, rationale}`. Small, deterministic, intentionally not a generic-purpose result wrapper.
- **New exception** `TelemetryObservationNotFoundError` — raised on missing id, mapped to HTTP 404.
- **New endpoint** `POST /api/telemetry/observations/{id}/correlate` → `PersistedCorrelationResult`. Wired in `app/api/telemetry.py`; existing endpoints (`/validate`, `/correlate/preview`, the Phase 22A persistence triple) are byte-for-byte unchanged in behavior.
- **8 new focused tests** in `backend/tests/test_telemetry_observations.py` (22 total in that file now): persisted BGP observation → creates Incident; created Incident has expected incident_type/title/severity; one IncidentEvent with suggested fields; observation gets `created_incident_id` stamped; correlating twice is idempotent (no duplicate Incident, no duplicate Event); unknown observation does NOT create an Incident and leaves FK null; 404 on missing id; LLM + remediation paths monkeypatched to raise (proves correlation never calls either); preview endpoint still non-persisting after 22B (`persisted: false` and zero new rows in either table).
- The Phase 18B `/correlate/preview` endpoint remains explicitly non-persisting — same `persisted: Literal[False]` response, same row-count assertion still passes.
- Same guardrails — no schema migration (Phase 22A's `created_incident_id` FK is what gets populated), no new dependency, no auth/RBAC, no background worker, no real device contact, no frontend work, no lab collector change.

## Phase 23 — Auth + RBAC ✓

- **Makes remediation approval safety credible.** The Phase 13A/B operator concept is upgraded with local password auth + bearer-token sessions; approve/reject endpoints are 401 without auth and 403 without role=`admin`; audit fields come from the authenticated session, not arbitrary submitted strings. **Local dev auth, honest in docs** — production should swap in a real identity provider (SSO/SAML/OAuth).
- **Migration `466922adacef`** (parent `82c1f3c27505`): adds `operators.password_hash VARCHAR(256) NULL` and creates `operator_sessions` (`id` UUID PK, `operator_id` FK CASCADE indexed, `token` VARCHAR(64) unique+indexed, `created_at`, `expires_at`). `password_hash` is nullable so prior operator rows remain representable — they just can't log in until a password is set.
- **Stdlib-only hashing.** New `app/auth/hashing.py`: PBKDF2-HMAC-SHA256, 600 000 iterations (OWASP 2023 floor), 16-byte random salt, 32-byte hash. Self-describing `pbkdf2_sha256$<iters>$<b64-salt>$<b64-hash>` format. Constant-time verify via `hmac.compare_digest`. Zero new pip dependencies.
- **Bearer-token sessions** in `app/auth/sessions.py`: `secrets.token_urlsafe(32)` opaque tokens, 7-day default TTL, stored in `operator_sessions`. `lookup_session` returns `None` on missing/unknown/expired tokens (no sliding-window renewal — out of scope).
- **FastAPI dependencies** in `app/auth/dependencies.py`: `get_current_operator` reads `Authorization: Bearer <token>` and returns the bound `Operator` or raises 401; `require_role(role)` wraps that with a 403 if the operator's role doesn't match. Used directly on the approve/reject endpoints in `app/api/remediation.py`.
- **New endpoints** in `app/api/auth.py`:
  - `POST /api/auth/login` `{display_name, password}` → 200 `{token, operator}`. Generic 401 "invalid credentials" for unknown user / wrong password / no password set (uniform message means the API doesn't leak username existence).
  - `POST /api/auth/logout` invalidates the current session (401 if unauthenticated — refusing a silent no-op).
  - `GET /api/auth/me` returns the bound `Operator` or 401. Used by the frontend to rehydrate state on page load.
- **`ApprovalRequest` body simplified** to `{note?}` only; `extra="forbid"` so stale callers sending `operator_id` / `operator_name` get 422. `set_recommendation_approval()` signature reduced to `(db, recommendation_id, status, operator: Operator, note=None)` — no more XOR-identity logic; the operator IS the bearer-token session. `OperatorNotFoundError` exception class stays for back-compat with imports but is no longer raised.
- **Seed CLI** (`app/operators/seed.py`) gains `--password`. Re-running with `--password` rotates the hash on the existing row; without it, the row is created with `password_hash=NULL` (login disabled).
- **Frontend (minimal per spec)**:
  - New `LoginPanel` component renders a 2-field login form (`display_name`, `password`) when logged out, and a "Logged in as X [role]" + Logout chip when logged in. Sits between StatusGrid and OperatorsPanel.
  - Bearer token persisted in `localStorage["neuronoc_auth_token"]`. On page load, the App calls `GET /api/auth/me` to validate the token and hydrate `currentOperator`; stale tokens are silently cleared. **Token storage is dev-appropriate** — documented in backend/README.md that production should move this to a HttpOnly cookie behind a real IdP.
  - `IncidentDetail` approval form drops the operator dropdown + free-form name input. The form shows a "Approving as <name> [role]" header (or "Not logged in — approval requires…" alert) and the Confirm button is disabled when not logged in OR not admin, with a `title=` tooltip explaining why.
  - `api.ts` injects `Authorization: Bearer <token>` on every request when a token is present (backend ignores it on endpoints that don't require auth).
- **Tests**:
  - Backend: 16 new tests in `test_auth.py` covering hashing primitives (round-trip, wrong-password, format, malformed-hash, empty / weak-iterations), login (success, wrong password, unknown user, no-password-set — all return same generic 401), `/me` (unauthenticated, malformed Authorization header, valid token, unknown token), and logout (invalidates token, unauthenticated 401).
  - Backend: `test_remediation.py` updated for the new auth contract — 6 prior approval tests rewritten to use a `_admin_headers(...)` helper that seeds an admin operator + logs in; 4 prior legacy tests (`test_api_approve_legacy_operator_name_still_works`, `test_api_approve_422_when_both_operator_id_and_name_supplied`, `test_planner_helper_rejects_both_identity_fields`, `test_api_approve_404_for_unknown_operator_id`) and `test_api_approve_with_operator_id_persists_display_name_and_fk` removed (the legacy paths are gone). 6 new RBAC tests added: unauthenticated approve → 401; unauthenticated reject → 401; non-admin approve → 403; non-admin reject → 403; legacy body fields → 422; audit fields come from auth not body.
  - Backend `test_operators.py` updated for the new CLI output phrasing (`role->admin`).
  - E2E: `global-setup.ts` now passes `--password demo-password` when seeding `local-operator`. New `loginAs(page, name, password)` helper drives the real LoginPanel UI. Existing "create operator then approve" test reshaped into a focused "OperatorsPanel still creates operators" test (since approval no longer flows through the dropdown). New "Approval requires login: unauthenticated Approve stays disabled" test pins the gated-button contract. The remaining approval test is rewritten to `loginAs('local-operator', 'demo-password')` first, then drive the simplified form (no dropdown). 25/25 e2e total (was 24; +1).
- **Backward compatibility honest, not bypassed**: existing non-approval APIs remain unauthenticated. Bearer tokens are accepted everywhere but ignored where auth isn't required. Legacy approval body fields are deliberately broken with 422 so stale callers fail loudly rather than silently bypassing auth.
- Same guardrails as every prior phase — no LLM behavior change, no remediation execution, no telemetry/lab feature work, no external auth provider, no new pip dependency.

## Phase 24 — Final packaging + demo readiness *(current)*

- **Documentation-only finish phase — no application behavior change.** Brings every README and the roadmap up to date so a fresh reader (or a recruiter following the demo checklist) can land on the repo and run the full real-lab → snapshot → agent → RCA → plan → admin-approve flow without hitting a stale instruction.
- Top-level `README.md` rewritten from the Phase 13B-era scope-section format into a finished portfolio README: what NeuroNOC is, the problem it addresses, the safety-first thesis, the real-vs-simulator data distinction, a Mermaid architecture diagram (React console → FastAPI backend → Postgres / Ollama / FRR lab, with the admin approval gate called out), the main user flow, the current feature set by phase, local run, test commands, a 16-step recruiter demo checklist (start Postgres → seed → start backend / frontend / lab → inject fault with `lab.sh` → collect snapshot → inspect findings → run agent analysis → generate RCA → generate plan → log in as admin → approve / reject → show no execution → heal → tear down), known limitations, future work.
- `backend/README.md` Operators section rewritten: replaces the legacy Phase 13A `operator_id` / `operator_name` XOR contract with the Phase 23 auth + RBAC model — PBKDF2-SHA256, bearer-token sessions, `role=admin` gated approval, `extra="forbid"` on the body so stale callers fail 422, audit fields from the session not the body. Cross-links `/api/auth/login` / `/auth/me` / `/auth/logout`.
- `frontend/README.md` updated: header now describes the Phase 23 login + role-gated approval; the OperatorsPanel step is honest that operators created in the UI ship with `password_hash=NULL` and can't log in until a password is set via the seed CLI; the e2e test count is corrected from 8 to 25 with a category breakdown (smoke, agent inspector, validation preview, runbook search, telemetry preview ×9, operators, auth + RBAC approval).
- `infra/lab/README.md` updated: the "not yet wired into the NeuroNOC backend" callout is removed (Phase 21A/C/E wired it); "What this lab is NOT" lists the real outstanding gaps (no R002/R004/R005/R006 lab signal, no syslog ingest, bridges-only) instead of the outdated "no collector" claim.
- `docs/roadmap.md` updated: Phase 23 marked ✓, Phase 24 added as *(current)*, project status summary appended below.
- **Hard constraints honored**: no product features added, no backend capabilities added, no frontend capabilities added (no broken documented demo path was found, so zero application code changed), no schema migrations, no new dependencies, no split into 24A/24B.

## Project status summary

NeuroNOC is feature-complete through Phase 23 as a portfolio-scale demonstration of safety-first agentic NetOps. The implementation spans:

- **Data plane**: Postgres 16 schema (5 incident-related tables + `operators` / `operator_sessions` / `agent_runs` / `agent_steps` / `telemetry_observations`), 6 Alembic migrations on linear ancestry (head: `466922adacef`).
- **Backend**: FastAPI + SQLAlchemy 2 + psycopg 3, **268 pytest tests** passing, AST safety scans pinning the no-execution / no-network contracts on `app/remediation/`, `app/validation/`, `app/telemetry/`.
- **Agent layer**: deterministic 6-node LangGraph workflow + 8 deterministic anomaly rules (R001–R008) + optional Ollama RCA (`qwen2.5:7b-instruct`) with a deterministic fallback.
- **Lab**: 4-router FRR v8.4.1 Compose stack with read-only snapshot collector (BGP + interfaces + route table + running-config), `lab.sh inject` / `heal` fault-injection helper covering `bgp-down` and `iface-down` per canonical-peer mapping.
- **Frontend**: Vite + React + TS strict, **25 Playwright e2e tests** (Chromium, serial, `retries: 0`), the full operator console (status grid, master/detail, agent run inspector, validation preview, runbook search, telemetry preview with 5 fixtures + sanitized export, login panel, operator management, plan cards with approval).
- **Auth**: PBKDF2-SHA256 (600k iterations, OWASP 2023 floor), bearer-token sessions in `operator_sessions`, FastAPI `Depends(require_role("admin"))` gating remediation approval.
- **CI**: GitHub Actions runs backend / frontend / e2e against a Postgres 16 service container on every push; Playwright captures retain-on-failure traces.
- **Honest scope boundaries** documented end-to-end: 4 of 8 anomaly rules covered by real lab data; remediation is plan-only by hard contract; local dev auth, not production-grade; no continuous telemetry pipeline.

The codebase is suitable as a working demonstration of agentic NetOps thinking with explicit safety contracts. It is intentionally not yet a production NetOps tool — see the "Future work" section of the top-level README for the next-frontier items (real IdP integration, continuous telemetry ingest, vector RAG, Kubernetes deployment).

## Beyond

- Real authentication (passwords / SSO / SAML) and RBAC enforcement.
- Multi-tenancy.
- Production deployment (Kubernetes + Helm).
- Observability (Prometheus, Grafana, OpenTelemetry).
- Batfish-based validation, Terraform / OpenTofu for IaC.
- Vector RAG over runbooks / device configs / past incidents (pgvector + embeddings).
- Continuous telemetry ingest (real collector, not the Phase 8C one-shot scraper).

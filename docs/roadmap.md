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

## Phase 18D — Telemetry preview API smoke coverage *(current)*

- **Client-contract coverage only — NOT a new telemetry capability.** Frontend has no Vitest / Jest harness (only Playwright); per spec, extended Playwright with `page.route()` interception rather than adding a new test framework / dependency.
- Strengthened the existing Phase 18C `Telemetry preview correlates the sample BGP event` test with route interception that counts requests to `/api/telemetry/correlate/preview`. The valid-sample click is now pinned to fire **exactly 1** request; the invalid-JSON click is pinned to fire **0** additional requests (count stays at 1 after a 250 ms settle). Existing inline `"not sent to backend"` parse-error assertion preserved.
- Two new e2e tests pin the client wrapper contracts:
  - `Telemetry preview API: Validate POSTs the JSON body to /api/telemetry/validate` — captures method (POST) + JSON body and asserts the BGP-shaped sample round-trips unchanged.
  - `Telemetry preview API: Preview correlation POSTs the body and the response carries persisted=false` — intercepts both directions via `route.fetch()`, asserts the outbound body matches the sample AND the inbound response carries `persisted: false`, `suggested_incident_type: bgp_neighbor_down`, `would_create_incident: true`, `would_create_event: true`.
- Panel placement and default-collapsed behavior unchanged. No new client code paths added — the panel itself, the API wrappers, and the backend endpoints are all untouched.
- Same guardrails — no backend change (zero `.py` files touched), no schema, no migration, no dependency added, no real SNMP/syslog collection, no socket, no device contact, no persistence, no LLM behavior change.

## Beyond

- Real authentication (passwords / SSO / SAML) and RBAC enforcement.
- Multi-tenancy.
- Production deployment (Kubernetes + Helm).
- Observability (Prometheus, Grafana, OpenTelemetry).
- Batfish-based validation, Terraform / OpenTofu for IaC.
- Vector RAG over runbooks / device configs / past incidents (pgvector + embeddings).
- Continuous telemetry ingest (real collector, not the Phase 8C one-shot scraper).

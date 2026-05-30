# NeuroNOC frontend

Vite + React + TypeScript operator console for the NeuroNOC backend. The console renders incidents, anomaly findings, events, evidence, agent runs, RCA explanations, remediation plans, validation previews, runbook search, telemetry preview fixtures, and operator management; exposes action buttons that hit the backend live; gates remediation approval behind Phase 23 bearer-token login + `role=admin` enforcement.

## Setup

```bash
pnpm install
```

## Dev server

```bash
pnpm dev
```

Runs on `http://localhost:5173`. The dev server proxies `/api/*` and `/health` to `http://127.0.0.1:8000` (Vite config), so no CORS setup is needed in dev.

## Build

```bash
pnpm build
```

## Demo / smoke flow

This is the full path for a visual walk-through of every UI section.

1. **Start Postgres** (Phase 1 stack, idempotent):

   ```bash
   docker compose up -d postgres
   ```

2. **Start the backend** (in one terminal):

   ```bash
   cd backend
   uv run alembic upgrade head        # idempotent
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

3. **Seed realistic data** (in another terminal, ~1 second):

   ```bash
   cd backend
   uv run python -m app.simulator.seed --reset       # idempotent
   uv run python -m app.simulator.seed --scenario all
   ```

   Five incidents are written, one per simulator scenario (BGP down, interface errors, latency spike, route missing, ACL block). Each carries 2–3 events + 2–3 evidence rows + 1 recommendation. Every row is tagged `[simulator]` in its summary so the same `--reset` removes it cleanly.

4. **Start the frontend** (in another terminal):

   ```bash
   cd frontend
   pnpm install                # first run only
   pnpm dev
   ```

5. **Open** `http://localhost:5173` in a browser. You should see:

   - **Status grid (5 cards):** Backend `online`, Incidents `5 (5 open / 0 closed)`, Anomaly findings `8`, Latest agent run `No runs yet` (until step 7), FRR lab collector `Ready` (until you click the header button or have the Phase 8B lab running).
   - **Header:** `Refresh` button and `Collect lab BGP snapshot` button (only meaningful if the Phase 8B lab is up — otherwise the call will fail and surface in the error banner).
   - **Incident list (left):** 5 rows with severity + status badges, type, short id, relative age.
   - **Detail pane (right):** an empty-state prompt until you select an incident.

6. **Click an incident** (try `bgp_neighbor_down` — the richest scenario):
   - **Summary** shows the simulator-written `[simulator] ...` line.
   - **Anomaly findings** shows 3 cards (R001/R002/R007). Their `refs:` line should read like `evt:bgp_state_change@simulator:edge-1`, *not* opaque UUIDs.
   - **Events** shows 3 cards in timeline order; click each `payload` for the raw JSONB.
   - **Evidence** shows 3 cards with command output / counters / route-table snippets.
   - **Agent runs** is empty.
   - **Remediation plans** is empty.

7. **Click `Run agent analysis`** in the action bar. After ~1 s:
   - An action message appears: `Agent run abc123ef completed; 6 steps recorded.`
   - A new `Agent runs` card appears with the 6 step names.
   - The `Latest agent run` status card jumps from `No runs yet` to `completed · <shortId>`.
   - **Inspect the run (Phase 15A):** click the new run card's summary to expand it. The header shows status badge, short id, workflow name, started/completed timestamps, and step count. Inside is a numbered step list — click any step (`load_incident`, `anomaly_detection`, `evidence_summary`, `correlation`, `validation`, `report`) to drill into its recorded `input`/`output` payload (and `error`, when a step failed). The full final report is also available under a collapsed `final report` block at the bottom of the run card. Read-only; nothing re-executes when you expand.
   - **Polish (Phase 15B):** the run summary now appends the total wall-clock duration (`123 ms` or `1.2 s`) when `completed_at` is available, and each step head shows a compact payload-shape chip (`· input N keys · output M keys`) so you can scan a run without clicking every step open. The collapsed `final report` block grows a `Copy final report JSON` button — clicking it writes the formatted JSON to the clipboard and shows an inline `Copied.` (or `Copy failed.` if the browser blocked clipboard access).

8. **Click `Generate RCA`**. If Ollama is reachable on `localhost:11434` and `qwen2.5:7b-instruct` is pulled, you'll see an RCA section appear with `(qwen2.5:7b-instruct)` in the title. Otherwise it'll say `(deterministic fallback)` and surface the templated summary. **Either way the RCA section persists** — it does not vanish on the next render (this was a Phase 9A cleanup).

9. **Click `Generate remediation plan`**. A new `Remediation plans` card appears with `risk medium · BGP neighbor recovery - investigate L1 and soft-reset session (draft)`. Open the `<details>` to see the human-readable summary plus the full plan JSON. **Nothing executes** — this is plan-only by design.

10. **Create a local operator** (Phase 13B inline panel). Above the incident list there's a compact **Operators** strip. Click `+ Add operator`, type a `display_name`, pick a role (`operator` / `admin`), click `Create operator`. The new operator chip appears immediately. The OperatorsPanel itself is unauthenticated and operators created here ship with `password_hash=NULL` — they're directory rows only and cannot log in until a password is set (`uv run python -m app.operators.seed --name <name> --password <pw>`). For demo purposes the e2e suite seeds `local-operator` with `--password demo-password` so step 11b can log in immediately.

10b. **Search runbooks (Phase 17B).** Below the Operators strip there's a compact **Runbooks** panel with a search input, a `Search` button, and a `Use selected incident` button. Type something like `bgp neighbor` and hit Enter or click Search — the deterministic Phase 17A keyword scorer ranks the bundled Markdown runbooks (`app/knowledge/runbooks/*.md`) and the panel lists the top 5 hits with title, file path (`<slug>.md`), score, and a bounded ~280-char excerpt. With an incident selected, `Use selected incident` instead derives the query from that incident's title + type + summary, so for the BGP scenario it surfaces `bgp.md` first. **Excerpt-only and read-only** — the full Markdown file is never fetched or rendered. No embeddings, no LLM call, no network hop beyond `/api/runbooks/search`.

10c. **Preview telemetry (Phase 18C).** Below the Runbooks panel there's a collapsible **Telemetry preview** panel (default-closed, "preview only · no persistence · no device contact" caveat visible even when collapsed). Expand it to see a JSON textarea pre-filled with a BGP-shaped sample event. Two buttons drive the Phase 18A/18B endpoints: `Validate` round-trips the payload through the `TelemetryEvent` schema and shows the normalized event; `Preview correlation` calls the rule mapper and renders `suggested_incident_type`, `correlation_key`, `confidence`, `would_create_incident`, `would_create_event`, `persisted: false`, and the rationale list. Local JSON parse errors are flagged inline as "Invalid JSON (not sent to backend)" so you can tell which layer (browser vs. API) rejected the payload. The textarea contents are session-only — never written to `localStorage` / `sessionStorage` / cookies. **Neither button persists anything or contacts a device** — pinned by row-count tests on the backend and by the e2e suite's contract assertions. *Phase 18D adds Playwright route-interception coverage for the telemetry client wrappers: the validate / correlate POST bodies are captured and asserted, the correlation response is asserted to carry `persisted: false`, and the invalid-JSON path is pinned to fire zero correlate API calls.*

10d. **Sample fixtures (Phase 19A).** Above the textarea there's now a `Sample fixture` dropdown with five hard-coded scenarios: BGP neighbor down (default), Interface down / errors, Latency spike, Route missing / withdrawn, and Unknown vendor trap (fallback). Picking a fixture replaces the textarea contents with that scenario's formatted JSON; `Reset to sample` snaps back to whichever fixture is currently selected. **Fixtures are client-side examples only** — no ingestion, no replay, no persistence. They live as module-level constants in `src/components/TelemetryPanel.tsx`; nothing is read from or written to `localStorage` / `sessionStorage` / cookies / URL params. The unknown-vendor fixture is shaped so it has no rule keywords in `event_type` or `message`, which lets you confirm the Phase 18B correlator falls through to `telemetry_observation` (with `would_create_incident: false`). *Phase 19B polish: switching fixtures, editing the JSON textarea, or clicking `Reset to sample` now clears any previously-rendered Validate / Preview correlation result and inline parse / API errors. A muted "Results reflect the last submitted payload." line appears under the action row whenever a result is on screen. Reset semantics are unchanged — it still snaps to the currently-active fixture, not always BGP.* *Phase 19C accessibility: the panel is reachable as an `aria-label="telemetry preview"` region; the fixture select, textarea, and all three buttons carry programmatic labels; the entire pick-fixture → submit flow is keyboard-only — Enter opens the panel, Tab reaches the fixture select, the select updates via standard `<select>` semantics, Tab reaches `Preview correlation`, and Enter submits.* *Phase 20A export: a small `Download JSON` button next to `Reset to sample` saves the current textarea as a local `.json` file with a deterministic filename (e.g. `telemetry-bgp.json`, `telemetry-unknown.json`). It uses browser primitives only (Blob + temporary `<a download>`) — **client-only export: no backend call, no upload/import path, no persistence**. Invalid JSON downloads as raw text too, so an unfinished draft can still be saved for offline editing.* *Phase 20B hardening: export filenames now go through a small `safeTelemetryFilename(id)` helper that lowercases, replaces any non-`[a-z0-9-]` runs with hyphens, collapses repeated hyphens, trims edges, and falls back to `telemetry-event.json` if the result would be empty. Defends against path traversal, Unicode oddness, and accidental future fixture id changes. The five current filenames are pinned byte-for-byte unchanged.*

11. **Preview validation for the plan (Phase 16B).** Before deciding, click **Preview validation** in the plan card's action row. A read-only block expands inline showing `Pre-checks`, `Post-checks`, `Validation criteria`, `Rollback steps`, and `Safety notes`, along with an `executable: false · source: remediation_plan` header and the caveat "Read-only. Nothing here executes a command or contacts a device. Proposed commands and Ansible playbook are intentionally omitted." The preview is fetched on demand and cached per recommendation id — closing then reopening it is instant, with no network roundtrip. Approve / Reject behavior is unaffected.

11b. **Log in as the seeded admin operator (Phase 23).** Between the StatusGrid and Operators panel there's a small **Operator login** strip. Type `local-operator` + `demo-password` (the e2e seed values; rotate via `python -m app.operators.seed --name local-operator --password <new>`) and click `Login`. The strip switches to "Logged in as local-operator [admin]" + a Logout button. The bearer token is stored in `localStorage["neuronoc_auth_token"]` — **dev-appropriate only; production should swap in a real IdP and move tokens to a HttpOnly cookie**.

12. **Approve a remediation plan (Phase 23 auth + RBAC).** Open the new plan card's details, click **Approve**. The inline form shows an "Approving as <name> [role]" header (the authenticated session) and a single optional `Note` textarea. `Confirm approve` is disabled with an explanatory tooltip if you're not logged in or your role isn't `admin`. Click it: the form collapses, the status badge flips to `approved`, and the approval block shows the authenticated operator's display_name + timestamp + note. **Nothing was executed** — the form's heading reminds you "records intent only; no execution." `Reject` works identically, with the badge ending up `rejected`. Only one approval form is open at a time across all plan cards. Clicking `Cancel` discards the in-progress draft. Backend `/api/remediation/recommendations/{id}/approve` returns 401 without bearer auth and 403 for non-admin roles; the audit trail (`approved_by`, `approved_by_operator_id`) is populated from the authenticated session, not from any body field.

13. **Resize the window** to a narrow width: the master/detail collapses to a single column under `900px`.

14. **Clean up** when done:

    ```bash
    cd backend
    uv run python -m app.simulator.seed --reset
    # Ctrl-C the backend + frontend dev servers
    ```

## End-to-end browser tests (Phase 12A)

A small Playwright smoke suite lives under `e2e/`. Run it once you've completed the prerequisites below.

One-time setup (downloads ~100 MB of Chromium):

```bash
pnpm install
pnpm exec playwright install chromium
```

Run the suite:

```bash
pnpm test:e2e              # headless
pnpm test:e2e:headed       # watch it in a real window
```

What it covers (25 tests, serial, single worker, `retries: 0`):

- **Smoke (Phases 9A–9C):** app loads, all 5 status cards render, incident list shows the 5 seeded scenarios, BGP incident detail renders findings/events/evidence with human-readable refs (`evt:` / `ev:`), `Run agent analysis` adds a run card, `Generate remediation plan` adds a plan card, `Generate RCA` shows the explanation and the section survives across re-renders.
- **Agent inspector (Phase 15A/15B):** newest run exposes the 6 deterministic LangGraph step names, the `report` step payload renders, the header shows a duration token, step heads carry payload-shape chips, and the copy-report button surfaces an inline `Copied.` / `Copy failed.` status.
- **Validation preview (Phase 16B):** the plan card's `Preview validation` block renders `executable: false`, `source: remediation_plan`, the `Pre-checks` and `Validation criteria` sections, and explicitly *omits* `proposed_commands` / `proposed_ansible_playbook`.
- **Runbook search (Phase 17B):** `Use selected incident` on a BGP-shaped incident ranks `bgp.md` first; the rendered excerpt is bounded (<400 chars) and does NOT contain a string unique to later in the file.
- **Telemetry preview (Phases 18C–20B, 9 tests):** panel caveat is visible, BGP sample correlates to `bgp_neighbor_down`, `persisted: false` pinned in both rendered output and route-intercepted response; invalid JSON shows an inline parse error AND fires zero correlate API calls; fixture picker swaps the textarea; the `unknown` fixture falls through to `telemetry_observation`; switching fixtures clears stale results; `Reset to sample` snaps to the active fixture (not always BGP); accessible-names + keyboard-only flow assertions; `Download JSON` writes the active fixture with a sanitized filename and zero API calls, including the raw-text path for invalid JSON.
- **Operators (Phase 13B → Phase 23):** OperatorsPanel still creates operators; duplicate `display_name` surfaces an inline `role="alert"` 409 error with the typed value preserved.
- **Auth + RBAC approval (Phase 23):** unauthenticated `Approve` button stays disabled with the explanatory tooltip; logging in as the pre-seeded `local-operator` (`demo-password`) unlocks the form and the approved badge + authenticated operator + note land on the card.

The suite uses real backend / real Postgres / real Vite proxy — no mocks. UI-created `e2e-ui-op-…` operator rows accumulate in the dev DB across runs; see "UI-created operators accumulate" under the demo flow for the optional cleanup recipe.

Prerequisites the suite assumes:

- Docker Postgres on `:5433` (Phase 1 stack: `docker compose up -d postgres`).
- Backend deps installed: `cd backend && uv sync`.
- `uv` on PATH (used by globalSetup/teardown to seed/reset the simulator).
- No external lab / Ollama dependency; the spec falls back cleanly when Ollama is unreachable.

Playwright auto-starts both dev servers via `webServer` in `playwright.config.ts`, runs `--reset` then `--scenario all` via `globalSetup`, executes the suite, then `--reset` again via `globalTeardown` so the dev database is left as it was found.

## What's not implemented yet

- No router — single-screen tool.
- No remediation execution path, no continuous ingest. See `docs/roadmap.md` in the project root.

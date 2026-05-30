# NeuroNOC frontend

Vite + React + TypeScript operator console for the NeuroNOC backend. The console renders incidents, anomaly findings, events, evidence, agent runs, RCA explanations, and remediation plans; exposes action buttons that hit the backend live; supports inline-form plan approval/rejection (Phase 10B) attributed to a managed operator dropdown (Phase 13A/B) with a legacy free-form name fallback.

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

8. **Click `Generate RCA`**. If Ollama is reachable on `localhost:11434` and `qwen2.5:7b-instruct` is pulled, you'll see an RCA section appear with `(qwen2.5:7b-instruct)` in the title. Otherwise it'll say `(deterministic fallback)` and surface the templated summary. **Either way the RCA section persists** — it does not vanish on the next render (this was a Phase 9A cleanup).

9. **Click `Generate remediation plan`**. A new `Remediation plans` card appears with `risk medium · BGP neighbor recovery - investigate L1 and soft-reset session (draft)`. Open the `<details>` to see the human-readable summary plus the full plan JSON. **Nothing executes** — this is plan-only by design.

10. **Create a local operator** (Phase 13B inline panel). Above the incident list there's a compact **Operators** strip. Click `+ Add operator`, type a `display_name`, pick a role (`operator` / `admin`), click `Create operator`. The new operator chip appears immediately and is available in step 11's approval dropdown. Operators are minimal local identity rows — **not a login system**: no password, no token, no RBAC enforcement. The `role` column is purely advisory.

11. **Approve a remediation plan** (Phase 10B inline form, Phase 13B operator picker). Open the new plan card's details, click **Approve**. A small inline form appears inside the card with three fields:
    - **Operator** dropdown (Phase 13B, populated from `/api/operators`) — pick one of the operators you created in step 10 (or any of the pre-seeded ones like `local-operator`). Selecting an operator records the FK so the approval is auditable.
    - **Or type a custom operator name** (legacy free-form fallback — used only when the dropdown is empty/disabled).
    - **Note** (optional textarea).

    `Confirm approve` is disabled until either the dropdown has a value OR the name field has non-empty text. Click it: the form collapses, the plan card's status badge flips from `pending` to `approved`, and an approval block shows the operator name + timestamp + note. **Nothing was executed** — the form's heading reminds you "records intent only; no execution."

    `Reject` works identically, with the badge ending up `rejected`. Only one approval form is open at a time across all plan cards. Clicking `Cancel` discards the in-progress draft.

12. **Resize the window** to a narrow width: the master/detail collapses to a single column under `900px`.

13. **Clean up** when done:

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

What it covers (8 tests, serial, single worker):

1. App loads and all 5 status cards render.
2. Incident list renders the 5 seeded scenarios.
3. BGP incident detail shows findings, events, evidence, and human-readable evidence refs (`evt:` / `ev:`).
4. `Run agent analysis` adds a new agent-run card.
5. `Generate remediation plan` adds a new plan card.
6. **Phase 13B:** create an operator via the `Operators` management panel, assert the chip carries role + a created_at signal, assert a duplicate `display_name` produces a `role="alert"` 409 error, then approve a plan via the operator dropdown using the just-created operator.
7. Approve a plan via the inline form (Phase 10B) using the pre-seeded `local-operator` and verify the approved badge + operator + note land on the card.
8. `Generate RCA` shows an RCA explanation and the section survives once it appears (live Ollama or deterministic fallback, both fine).

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

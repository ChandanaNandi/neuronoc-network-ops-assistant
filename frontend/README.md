# NeuroNOC frontend

Vite + React + TypeScript operator console for the NeuroNOC backend. As of Phase 9B the console renders incidents, anomaly findings, events, evidence, agent runs, RCA explanations, and remediation plans, and exposes action buttons that hit the backend live.

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

10. **Resize the window** to a narrow width: the master/detail collapses to a single column under `900px`.

11. **Clean up** when done:

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

What it covers (7 tests, serial, single worker):

1. App loads and all 5 status cards render.
2. Incident list renders the 5 seeded scenarios.
3. BGP incident detail shows findings, events, evidence, and human-readable evidence refs (`evt:` / `ev:`).
4. `Run agent analysis` adds a new agent-run card.
5. `Generate remediation plan` adds a new plan card.
6. Approve a plan via the native `window.prompt` flow and verify the approved badge + operator + note land on the card.
7. `Generate RCA` shows an RCA explanation and the section survives once it appears (live Ollama or deterministic fallback, both fine).

Prerequisites the suite assumes:

- Docker Postgres on `:5433` (Phase 1 stack: `docker compose up -d postgres`).
- Backend deps installed: `cd backend && uv sync`.
- `uv` on PATH (used by globalSetup/teardown to seed/reset the simulator).
- No external lab / Ollama dependency; the spec falls back cleanly when Ollama is unreachable.

Playwright auto-starts both dev servers via `webServer` in `playwright.config.ts`, runs `--reset` then `--scenario all` via `globalSetup`, executes the suite, then `--reset` again via `globalTeardown` so the dev database is left as it was found.

## What's not implemented yet

- No router — single-screen tool.
- No remediation execution path, no continuous ingest. See `docs/roadmap.md` in the project root.

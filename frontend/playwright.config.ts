import { defineConfig } from '@playwright/test'

// Phase 12A smoke-test config.
// - Auto-starts the backend (uvicorn) and the frontend (Vite dev server) and
//   waits for each to become reachable before tests run.
// - Seeds + resets the simulator data via globalSetup / globalTeardown so
//   tests run against deterministic state and leave nothing behind.
// - Chromium only by default - one browser is plenty for smoke. Add firefox
//   or webkit by extending `projects` when there's a reason to.
//
// Prerequisites:
//   - Docker Postgres on :5433 (Phase 1 compose), and `cd backend && uv sync`.
//   - Chromium binary installed once: `pnpm exec playwright install chromium`.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? 'line' : 'list',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  use: {
    baseURL: 'http://localhost:5173',
    // Phase 14B: in CI we run with retries=0, so 'on-first-retry' would never
    // actually capture anything. 'retain-on-failure' gives CI a trace on the
    // very first failing run, which is what's useful for debuggability.
    // Locally we keep the lighter default so green runs don't write trace
    // bundles to disk.
    trace: process.env.CI ? 'retain-on-failure' : 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command:
        'cd ../backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: 'pnpm dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})

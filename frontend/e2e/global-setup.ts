import { execSync } from 'node:child_process'

// Resets simulator data + seeds all five scenarios before the test suite
// starts. The backend dev server is already up by the time this runs because
// Playwright orchestrates `webServer` first.
export default async function globalSetup() {
  const sim =
    'uv --directory ../backend run python -m app.simulator.seed'
  console.log('[e2e] resetting simulator data...')
  execSync(`${sim} --reset`, { stdio: 'inherit' })
  console.log('[e2e] seeding all scenarios...')
  execSync(`${sim} --scenario all`, { stdio: 'inherit' })

  // Phase 13A: seed a local operator the approval test can pick from the
  // dropdown. Idempotent by display_name so re-runs are safe.
  console.log('[e2e] seeding local-operator...')
  execSync(
    'uv --directory ../backend run python -m app.operators.seed ' +
      '--name local-operator --role admin',
    { stdio: 'inherit' },
  )

  console.log('[e2e] test data ready')
}

import { execSync } from 'node:child_process'

// Resets simulator data + seeds all five scenarios before the test suite
// starts. The backend dev server is already up by the time this runs because
// Playwright orchestrates `webServer` first.
export default async function globalSetup() {
  const cmd =
    'uv --directory ../backend run python -m app.simulator.seed'
  console.log('[e2e] resetting simulator data...')
  execSync(`${cmd} --reset`, { stdio: 'inherit' })
  console.log('[e2e] seeding all scenarios...')
  execSync(`${cmd} --scenario all`, { stdio: 'inherit' })
  console.log('[e2e] simulator data ready')
}

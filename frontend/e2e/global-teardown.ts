import { execSync } from 'node:child_process'

// Always run a final --reset so the dev DB is the same shape it was before
// the suite. This runs even if tests failed.
export default async function globalTeardown() {
  console.log('[e2e] resetting simulator data...')
  try {
    execSync(
      'uv --directory ../backend run python -m app.simulator.seed --reset',
      { stdio: 'inherit' },
    )
  } catch (err) {
    console.warn('[e2e] teardown reset failed (continuing):', err)
  }
}

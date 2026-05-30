// Phase 12A operator-console smoke. Intentionally small + serial - one
// shared seeded database that the tests read and (carefully) extend. Mocks
// are avoided; we hit the real backend through the same Vite proxy a user
// would. RCA is tested in a fallback-safe way so the suite doesn't require
// a running Ollama daemon.

import { readFile } from 'node:fs/promises'

import { expect, test, type Page } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

// All seeded BGP/ACL/etc. incidents share the same `created_at` second
// resolution, so we always pick the BGP one by title - the simulator's titles
// are stable.
const BGP_TITLE_PATTERN = /BGP neighbor 10\.0\.0\.21 down/

async function selectBgpIncident(page: Page) {
  await page.goto('/')
  await page
    .locator('button.incident-row', { hasText: BGP_TITLE_PATTERN })
    .click()
  await expect(
    page.getByRole('heading', { name: /Anomaly findings/i }),
  ).toBeVisible()
}

// Phase 23 e2e helper: log in via the LoginPanel form (we deliberately
// drive the actual UI rather than poking localStorage, so the test
// exercises the real fetch path including the bearer-token header
// injection). Assumes `page.goto('/')` has already been called so the
// LoginPanel is mounted.
async function loginAs(
  page: Page,
  displayName: string,
  password: string,
) {
  await page.getByLabel('login display name').fill(displayName)
  await page.getByLabel('login password').fill(password)
  await page.getByRole('button', { name: /^Login$/ }).click()
  // The panel switches to the "Logged in as" header on success.
  await expect(page.locator('.login-panel__name')).toHaveText(
    displayName,
    { timeout: 5_000 },
  )
}

test('app loads and all five status cards render', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.app__brand')).toHaveText('NeuroNOC')
  await expect(page.locator('.status-card')).toHaveCount(5)
  for (const label of [
    'Backend',
    'Incidents',
    'Anomaly findings',
    'Latest agent run',
    'FRR lab collector',
  ]) {
    await expect(page.locator('.status-card__label', { hasText: label })).toBeVisible()
  }
})

test('incident list renders the five seeded scenarios', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.incident-row')).toHaveCount(5)
  await expect(page.locator('.incident-row', { hasText: BGP_TITLE_PATTERN })).toHaveCount(1)
  await expect(
    page.locator('.incident-row', { hasText: 'ACL denying branch-1' }),
  ).toHaveCount(1)
})

test('BGP incident detail shows findings, events, evidence and readable refs', async ({
  page,
}) => {
  await selectBgpIncident(page)

  // Anomaly findings - BGP scenario triggers 3 rules.
  const findings = page.locator('.finding-card')
  await expect(findings).toHaveCount(3)
  await expect(page.locator('.finding-card__rule')).toContainText([
    'bgp_neighbor_down_detected',
  ])

  // Events section: 3 events, including the BGP state change.
  await expect(
    page.getByRole('heading', { name: /^Events/i }),
  ).toBeVisible()
  await expect(page.locator('section.detail-section').filter({
    has: page.getByRole('heading', { name: /^Events/i }),
  }).locator('.event-card')).toHaveCount(3)
  await expect(page.getByText('bgp_state_change').first()).toBeVisible()

  // Evidence section: 3 evidence, including route_table_excerpt.
  await expect(
    page.getByRole('heading', { name: /^Evidence/i }),
  ).toBeVisible()
  await expect(page.getByText('route_table_excerpt').first()).toBeVisible()

  // The Phase 9B win: anomaly evidence_refs render as human-readable labels
  // (`evt:<type>@<source>` / `ev:<type>@<source>`) instead of opaque UUIDs.
  await expect(page.locator('.finding-card__refs').first()).toContainText(/evt:|ev:/)
})

// Note on success signals:
//
// IncidentDetail's load effect calls `setActionMsg(null)` so navigating
// between incidents is clean. The same effect re-fires whenever
// onPersistedMutation bumps refreshTrigger - so the inline action-msg shown
// by agent / plan / approve flows is wiped almost immediately by the
// triggered refetch. The DURABLE success signal is the new card itself
// (run-card, plan-card) appearing on screen. Tests below assert that
// outcome, which is what a real operator actually sees.

test('Run agent analysis adds a new agent run card', async ({ page }) => {
  await selectBgpIncident(page)

  const beforeCount = await page.locator('.run-card').count()
  await page.getByRole('button', { name: 'Run agent analysis' }).click()
  await expect(page.locator('.run-card')).toHaveCount(beforeCount + 1, {
    timeout: 20_000,
  })
})

test('Agent run inspector reveals the six deterministic LangGraph steps', async ({
  page,
}) => {
  await selectBgpIncident(page)

  // Generate a fresh run so this test isn't sensitive to ordering vs. the
  // previous test's run card.
  const beforeCount = await page.locator('.run-card').count()
  await page.getByRole('button', { name: 'Run agent analysis' }).click()
  await expect(page.locator('.run-card')).toHaveCount(beforeCount + 1, {
    timeout: 20_000,
  })

  // Newest run is first - the backend orders agent runs by created_at desc.
  const newest = page.locator('.run-card').first()
  await newest.locator('summary').first().click()

  // The Phase 15A inspector renders one .run-card__step per LangGraph node,
  // in graph execution order. Phase 5 declares 6 deterministic nodes.
  const stepList = newest.locator('ol[aria-label="agent run steps"]')
  await expect(stepList).toBeVisible()
  await expect(stepList.locator('> li.run-card__step')).toHaveCount(6)

  // Step names are stable - assert each one is present somewhere in the list.
  for (const stepName of [
    'load_incident',
    'anomaly_detection',
    'evidence_summary',
    'correlation',
    'validation',
    'report',
  ]) {
    await expect(
      stepList.locator('.run-card__step-name', { hasText: stepName }),
    ).toHaveCount(1)
  }

  // Open the report step and confirm its output payload renders (the report
  // node persists the IncidentAnalysisReport dict via _persist_step).
  const reportStep = stepList.locator('li.run-card__step', {
    has: page.locator('.run-card__step-name', { hasText: 'report' }),
  })
  await reportStep.locator('summary').click()
  await expect(reportStep.locator('.run-card__step-payload').first()).toBeVisible()
  await expect(reportStep.locator('.run-card__step-payload').first()).toContainText(
    /suspected_root_cause|key_findings|incident_type/,
  )

  // Pin the empty-body fallback: every expanded step must surface EITHER an
  // `output:` label (when output_payload has keys) OR the "No payload
  // recorded." muted line. Today Phase 5's report node always persists
  // output_payload, so the regex will match `output:` here - but the
  // assertion guards against the {} === truthy bug recurring if a future
  // step ever ships with an empty payload object.
  await expect(reportStep.locator('.run-card__step-body')).toContainText(
    /output:|No payload recorded\./,
  )

  // Phase 15B: run summary carries a duration token. We deliberately don't
  // assert exact ms - the workflow is fast but variable. Format helper emits
  // either `<n> ms` or `<n.n> s`, with leading separator " · ".
  await expect(newest.locator('summary').first()).toContainText(
    /·\s+\d+(\.\d+)?\s+(ms|s)\b/,
  )

  // Phase 15B: at least one step head shows the payload-key summary chip.
  // The Phase 5 report node always persists an output_payload with > 0 keys,
  // so `output \d+ keys` will be present somewhere in the list.
  await expect(
    newest
      .locator('.run-card__step-summary', {
        hasText: /input \d+ keys|output \d+ keys/,
      })
      .first(),
  ).toBeVisible()

  // Phase 15B: the copy-final-report button is wired and reports either a
  // success ("Copied.") or a graceful failure ("Copy failed.") inline.
  // Browsers gate clipboard.writeText on permission/secure-context; rather
  // than thread Playwright permission grants in here, accept either branch -
  // the operationally-important contract is "user got immediate feedback".
  const finalReport = newest.locator('.run-card__report-wrap')
  await finalReport.locator('summary').click()
  const copyBtn = finalReport.getByRole('button', {
    name: /Copy final report JSON/i,
  })
  await expect(copyBtn).toBeVisible()
  await copyBtn.click()
  await expect(finalReport.locator('.run-card__copy-msg')).toContainText(
    /Copied\.|Copy failed\./,
    { timeout: 5_000 },
  )
})

test('Preview validation renders read-only check lists and hides actionable fields', async ({
  page,
}) => {
  await selectBgpIncident(page)

  // Generate a fresh plan so this test is independent of prior runs.
  const beforeCount = await page.locator('.plan-card').count()
  await page.getByRole('button', { name: 'Generate remediation plan' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(beforeCount + 1, {
    timeout: 20_000,
  })

  const newest = page.locator('.plan-card').first()
  await newest.locator('summary').click()
  await newest.getByRole('button', { name: 'Preview validation' }).click()

  const preview = newest.locator('.validation-preview')
  await expect(preview).toBeVisible({ timeout: 10_000 })

  // Required sections - BGP plan template always populates pre-checks +
  // validation criteria, so these are stable assertions.
  await expect(preview.getByText('Pre-checks', { exact: true })).toBeVisible()
  await expect(
    preview.getByText('Validation criteria', { exact: true }),
  ).toBeVisible()

  // Phase 16A contract: response carries executable=false and a
  // remediation_plan validation_source. The microcopy renders both inline.
  await expect(preview).toContainText(/executable:\s+false/i)
  await expect(preview).toContainText(/source:\s+remediation_plan/i)

  // The read-only caveat must be present and the actionable fields
  // (proposed_commands / proposed_ansible_playbook) must NOT leak through.
  await expect(preview).toContainText(/read-only/i)
  await expect(preview).not.toContainText('proposed_commands')
  await expect(preview).not.toContainText('proposed_ansible_playbook')

  // Toggle off then on again - the cached preview should re-appear without
  // needing a network roundtrip. We don't sniff the network; instead we
  // assert the contract holds (no loading state on the second open).
  await newest.getByRole('button', { name: 'Hide validation' }).click()
  await expect(preview).toHaveCount(0)
  await newest.getByRole('button', { name: 'Preview validation' }).click()
  await expect(preview).toBeVisible()
  await expect(preview).not.toContainText('Loading validation preview')
})

test('Generate remediation plan adds a new plan card', async ({ page }) => {
  await selectBgpIncident(page)

  const beforeCount = await page.locator('.plan-card').count()
  await page.getByRole('button', { name: 'Generate remediation plan' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(beforeCount + 1, {
    timeout: 20_000,
  })
})

test('Operators panel still creates operators (Phase 13B UX, post-Phase-23)', async ({
  page,
}) => {
  // Phase 13B + 13A behaviors still hold: the panel creates operator
  // rows, surfaces them as chips, and rejects duplicates inline. After
  // Phase 23 approval is decoupled from operator creation — approving
  // requires an authenticated admin session, not a freshly-created
  // operator in the panel. So this test now just verifies the panel,
  // and a separate test covers the auth-gated approval flow.
  const uniqueName = `e2e-ui-op-${Date.now()}`

  await page.goto('/')

  await page.getByRole('button', { name: '+ Add operator' }).click()
  await page.getByLabel('new operator name').fill(uniqueName)
  await page.getByLabel('new operator role').selectOption('operator')
  await page.getByRole('button', { name: 'Create operator' }).click()

  const chip = page.locator('.operator-chip', { hasText: uniqueName })
  await expect(chip).toBeVisible({ timeout: 5_000 })
  await expect(chip).toContainText('[operator]')
  await expect(chip.locator('.operator-chip__when')).toBeVisible()

  // Duplicate display_name → inline 409 via role=alert.
  await page.getByRole('button', { name: '+ Add operator' }).click()
  await page.getByLabel('new operator name').fill(uniqueName)
  await page.getByRole('button', { name: 'Create operator' }).click()
  const dupErr = page.getByRole('alert')
  await expect(dupErr).toBeVisible({ timeout: 5_000 })
  await expect(dupErr).toContainText(/already exists/i)
})


test('Approval requires login: unauthenticated Approve button stays disabled with explanatory copy', async ({
  page,
}) => {
  // Phase 23 contract: opening the approval form without being logged
  // in surfaces an inline "Not logged in" alert and disables the
  // Confirm button. No API request is fired.
  await selectBgpIncident(page)

  const planCountBefore = await page.locator('.plan-card').count()
  await page.getByRole('button', { name: 'Generate remediation plan' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(planCountBefore + 1, {
    timeout: 20_000,
  })

  const newest = page.locator('.plan-card').first()
  await newest.locator('summary').click()
  await newest.getByRole('button', { name: 'Approve' }).click()

  const form = newest.locator('.approval-form')
  await expect(form).toBeVisible()
  await expect(form).toContainText(/not logged in/i)
  await expect(
    form.getByRole('button', { name: /^Confirm approve$/i }),
  ).toBeDisabled()
})


test('Approve a plan via the inline form (Phase 23): login as admin, approve, badge + operator + note land', async ({
  page,
}) => {
  // Phase 23 e2e: log in as the seeded `local-operator` (role=admin),
  // then approve. The form no longer has an operator dropdown — the
  // approving identity is the authenticated bearer-token session.
  await page.goto('/')
  await loginAs(page, 'local-operator', 'demo-password')

  // Drive the rest of the flow.
  await page
    .locator('button.incident-row', { hasText: BGP_TITLE_PATTERN })
    .click()
  await expect(
    page.getByRole('heading', { name: /Anomaly findings/i }),
  ).toBeVisible()

  const planCountBefore = await page.locator('.plan-card').count()
  await page.getByRole('button', { name: 'Generate remediation plan' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(planCountBefore + 1, {
    timeout: 20_000,
  })

  const newest = page.locator('.plan-card').first()
  await newest.locator('summary').click()
  await newest.getByRole('button', { name: 'Approve' }).click()

  // Form is visible; "Approving as" header reflects the logged-in operator.
  const form = newest.locator('.approval-form')
  await expect(form).toBeVisible()
  await expect(form).toContainText('Approving as')
  await expect(form).toContainText('local-operator')
  await expect(form).toContainText('[admin]')

  const confirmBtn = form.getByRole('button', { name: /^Confirm approve$/i })
  // No dropdown / no required name — the button is enabled the moment the
  // form opens (note is optional).
  await expect(confirmBtn).toBeEnabled()

  await form.getByLabel('approval note').fill('approved during smoke run')
  await confirmBtn.click()

  // The refreshed plan card carries the approval state (Phase 10A's cleanup
  // intentionally relies on the card's own approval block, not an ephemeral
  // inline message). The inline form should collapse on success.
  await expect(newest.locator('.badge--approval-approved')).toBeVisible({
    timeout: 10_000,
  })
  // approved_by resolved from the authenticated operator (not body input).
  await expect(newest.locator('.plan-card__approval')).toContainText(
    'local-operator',
  )
  await expect(newest.locator('.plan-card__approval')).toContainText(
    'approved during smoke run',
  )
  await expect(newest.locator('.approval-form')).toHaveCount(0)
})

test('Runbook search returns BGP runbook for a BGP query and via Use selected incident', async ({
  page,
}) => {
  await page.goto('/')
  const panel = page.locator('.runbooks-panel')
  await expect(panel).toBeVisible()

  // Search button is disabled until the input has non-empty trimmed text.
  const searchBtn = panel.getByRole('button', { name: /^Search$/i })
  await expect(searchBtn).toBeDisabled()
  await panel.getByLabel('runbook search query').fill('   ')
  await expect(searchBtn).toBeDisabled() // whitespace-only stays disabled
  await panel.getByLabel('runbook search query').fill('bgp neighbor')
  await expect(searchBtn).toBeEnabled()
  await searchBtn.click()

  // BGP runbook is the top hit for a BGP-shaped query.
  const firstHit = panel.locator('.runbook-hit').first()
  await expect(firstHit).toBeVisible({ timeout: 5_000 })
  await expect(firstHit.locator('.runbook-hit__title')).toContainText(/BGP/i)
  await expect(firstHit.locator('.runbook-hit__path')).toContainText('bgp.md')
  await expect(firstHit.locator('.runbook-hit__score')).toContainText(/score \d/)

  // Phase 17B contract: render only the bounded excerpt, never the full file.
  // bgp.md's "Reload the router" line sits near the end of the file (~chars
  // 1300+); the bundled excerpt cuts off well before it. If a future change
  // accidentally swaps the excerpt for the full file body, this fails.
  const excerptText = (
    await firstHit.locator('.runbook-hit__excerpt').textContent()
  ) ?? ''
  expect(excerptText.length).toBeLessThan(400)
  expect(excerptText).not.toContain('Reload the router')

  // "Use selected incident" button: disabled until an incident is selected.
  const useIncidentBtn = panel.getByRole('button', {
    name: /Use selected incident/i,
  })
  await expect(useIncidentBtn).toBeDisabled()

  // Select the BGP incident, then derive the search from it.
  await page
    .locator('button.incident-row', { hasText: BGP_TITLE_PATTERN })
    .click()
  await expect(
    page.getByRole('heading', { name: /Anomaly findings/i }),
  ).toBeVisible()
  await expect(useIncidentBtn).toBeEnabled()
  await useIncidentBtn.click()

  // Incident-derived search must surface the BGP runbook first.
  await expect(panel.locator('.runbook-hit').first()).toBeVisible({
    timeout: 5_000,
  })
  await expect(
    panel.locator('.runbook-hit').first().locator('.runbook-hit__title'),
  ).toContainText(/BGP/i)

  // Empty-state path: a query that matches nothing renders the muted note.
  await panel.getByLabel('runbook search query').fill('zzzqqq nopematch')
  await searchBtn.click()
  await expect(panel.locator('.runbooks-panel__results')).toContainText(
    /No runbook matched/i,
    { timeout: 5_000 },
  )
})

test('Telemetry preview correlates the sample BGP event without persisting anything', async ({
  page,
}) => {
  // Phase 18D strengthening: intercept the correlate/preview endpoint so we
  // can prove the invalid-JSON path makes ZERO API calls. The handler just
  // counts and forwards via route.continue() so the real backend still
  // serves the request.
  let correlateCallCount = 0
  await page.route('**/api/telemetry/correlate/preview', async (route) => {
    correlateCallCount += 1
    await route.continue()
  })

  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await expect(panel).toBeVisible()

  // Microcopy contract: the panel header carries the no-persistence /
  // no-device-contact caveat even before expansion.
  await expect(panel).toContainText(/no persistence/i)
  await expect(panel).toContainText(/no device contact/i)

  // Panel is collapsed by default - open it.
  await panel.locator('summary').first().click()

  // The body's intro reaffirms "Neither call persists anything or contacts
  // a device." so the operator sees it inline before clicking.
  await expect(panel.locator('.telemetry-panel__intro')).toContainText(
    /persist/i,
  )
  await expect(panel.locator('.telemetry-panel__intro')).toContainText(
    /contacts a device|device/i,
  )

  // Pre-click sanity: the page load alone must NOT have fired a
  // correlate/preview request.
  expect(correlateCallCount).toBe(0)

  // Hit Preview correlation on the pre-filled BGP-shaped sample.
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()

  // Correlation preview block appears and carries the BGP mapping.
  const result = panel.locator('.telemetry-panel__result')
  await expect(result).toBeVisible({ timeout: 5_000 })
  await expect(result).toContainText('Correlation preview')
  await expect(result).toContainText('bgp_neighbor_down')

  // persisted=false is rendered in the dl AND in the header caveat - assert
  // BOTH branches so the contract is pinned at two layers.
  await expect(result).toContainText(/persisted:?\s*false/i)
  // Also pinned in the would_create_incident dl row (BGP rule -> true) and
  // would_create_event (always true).
  await expect(result).toContainText(/would_create_incident/i)
  await expect(result).toContainText(/would_create_event/i)

  // Valid sample click fired exactly one correlate/preview request.
  expect(correlateCallCount).toBe(1)

  // Now flip the JSON to invalid syntax and assert the parse error renders
  // INLINE, without firing the API (the API error banner would say
  // "API rejected payload"; the parse error explicitly says "not sent to
  // backend").
  const textarea = panel.getByLabel('telemetry event json')
  await textarea.fill('{ this is not valid json }')
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()
  await expect(panel.locator('.telemetry-panel__parse-error')).toBeVisible()
  await expect(panel.locator('.telemetry-panel__parse-error')).toContainText(
    /not sent to backend/i,
  )

  // Phase 18D contract: invalid JSON must NOT fire a correlate/preview
  // request. The count is still 1, not 2. Tiny settle delay rules out a
  // late-arriving request that the test might miss; if a stray request
  // was queued, it would land in the next ~250 ms.
  await page.waitForTimeout(250)
  expect(correlateCallCount).toBe(1)
})

test('Telemetry preview API: Validate POSTs the JSON body to /api/telemetry/validate', async ({
  page,
}) => {
  // Phase 18D client-contract coverage for `api.validateTelemetry`. Capture
  // the method + body, then let the real backend handle the request so the
  // result still renders normally.
  const captured: { method: string; body: string }[] = []
  await page.route('**/api/telemetry/validate', async (route, request) => {
    captured.push({
      method: request.method(),
      body: request.postData() ?? '',
    })
    await route.continue()
  })

  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()
  await panel.getByRole('button', { name: /^Validate$/i }).click()

  // Wait for the result block to render - that's the side effect that
  // proves the request both fired AND succeeded round-trip.
  await expect(panel.locator('.telemetry-panel__result')).toBeVisible({
    timeout: 5_000,
  })

  // Exactly one POST captured.
  expect(captured).toHaveLength(1)
  expect(captured[0].method).toBe('POST')

  // Body is the JSON the textarea was carrying (the BGP-shaped sample) -
  // round-tripped through JSON.parse so we can assert specific fields
  // without depending on Pydantic key ordering.
  const parsed = JSON.parse(captured[0].body)
  expect(parsed.collector_type).toBe('snmp')
  expect(parsed.event_type).toBe('bgp_neighbor_down')
  expect(parsed.source).toBe('snmp:edge-1')
  expect(parsed.severity).toBe('critical')
})

test('Telemetry fixture picker swaps the textarea contents to the selected event', async ({
  page,
}) => {
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  const textarea = panel.getByLabel('telemetry event json')
  const picker = panel.getByLabel('telemetry sample fixture')

  // Default fixture is BGP - textarea opens with bgp_neighbor_down content.
  await expect(textarea).toHaveValue(/"event_type":\s*"bgp_neighbor_down"/)

  // Each fixture's value -> expected event_type string in the textarea.
  // Pinning all 5 catches any wiring regression (wrong fixture id, lost
  // entry, drift between value and label).
  const cases: { value: string; expected: RegExp }[] = [
    { value: 'interface', expected: /"event_type":\s*"interface_down"/ },
    { value: 'latency', expected: /"event_type":\s*"latency_spike"/ },
    { value: 'route-missing', expected: /"event_type":\s*"route_withdrawn"/ },
    {
      value: 'unknown',
      expected: /"event_type":\s*"vendor_proprietary_trap"/,
    },
    { value: 'bgp', expected: /"event_type":\s*"bgp_neighbor_down"/ },
  ]
  for (const { value, expected } of cases) {
    await picker.selectOption(value)
    await expect(textarea).toHaveValue(expected)
  }
})

test('Telemetry panel: accessible names and roles are present', async ({
  page,
}) => {
  // Phase 19C smoke check - no axe / no new dependency, just confirm that
  // the structural ARIA contract the rest of the suite relies on actually
  // surfaces through Playwright's role/label queries. If any of these
  // started returning zero, the panel's accessibility would have silently
  // regressed and the more elaborate keyboard test below would be
  // misleading.
  await page.goto('/')

  // The panel is a labelled section, which makes it a "region" landmark.
  const region = page.getByRole('region', { name: 'telemetry preview' })
  await expect(region).toBeVisible()

  // Expand so the body comes into the DOM.
  await region.locator('summary').first().click()

  // Fixture picker + textarea by their accessible names.
  await expect(region.getByLabel('telemetry sample fixture')).toBeVisible()
  await expect(region.getByLabel('telemetry event json')).toBeVisible()

  // Buttons by role + name (exact-match regex so a future rename or text
  // suffix would fail the test instead of silently broadening the match).
  await expect(
    region.getByRole('button', { name: /^Validate$/ }),
  ).toBeVisible()
  await expect(
    region.getByRole('button', { name: /^Preview correlation$/ }),
  ).toBeVisible()
  await expect(
    region.getByRole('button', { name: /^Reset to sample$/ }),
  ).toBeVisible()
})

test('Telemetry panel: keyboard-only flow opens, picks unknown fixture, submits, gets fallback', async ({
  page,
}) => {
  // Phase 19C contract: the whole preview flow must be operable without
  // mouse. Open the collapsed <details> with Enter, Tab to the fixture
  // <select>, change to unknown via the standard <select> API (the same
  // path a screen-reader user takes through their AT), Tab to the
  // Preview correlation button, press Enter, and assert the fallback
  // mapping landed.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await expect(panel).toBeVisible()

  // Open the collapsed panel via keyboard only (focus summary + Enter).
  const summary = panel.locator('summary').first()
  await summary.focus()
  await page.keyboard.press('Enter')

  // After expansion, body content is in the DOM and focusable.
  const textarea = panel.getByLabel('telemetry event json')
  await expect(textarea).toBeVisible()

  // Tab from summary lands on the fixture <select> (next focusable element
  // in document order). Pin via the focused element's id.
  await page.keyboard.press('Tab')
  await expect(page.locator('*:focus')).toHaveAttribute(
    'id',
    'telemetry-fixture',
  )

  // Change the focused select to unknown. selectOption is the canonical
  // Playwright primitive for <select> and is non-mouse; AT bridges drive
  // selects through the same OS path.
  const picker = panel.getByLabel('telemetry sample fixture')
  await picker.selectOption('unknown')
  await expect(textarea).toHaveValue(/vendor_proprietary_trap/)

  // Tab from the select to Preview correlation. Document order is:
  // select -> textarea -> Validate button -> Preview correlation button.
  await page.keyboard.press('Tab') // -> textarea
  await page.keyboard.press('Tab') // -> Validate
  await page.keyboard.press('Tab') // -> Preview correlation
  await expect(page.locator('*:focus')).toHaveText(/^Preview correlation$/)

  // Activate via keyboard.
  await page.keyboard.press('Enter')

  // Result block appears with the fallback mapping. Pin all four flags
  // via the rendered dl so a regression on any one of them fails here.
  const result = panel.locator('.telemetry-panel__result')
  await expect(result).toBeVisible({ timeout: 5_000 })
  await expect(result).toContainText('telemetry_observation')
  const dl = result.locator('.telemetry-panel__dl')
  await expect(
    dl
      .locator('dt', { hasText: 'would_create_incident' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('false')
  await expect(
    dl
      .locator('dt', { hasText: 'would_create_event' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('true')
  await expect(
    dl
      .locator('dt', { hasText: 'persisted' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('false')
})

test('Telemetry preview clears stale result block when fixture is switched', async ({
  page,
}) => {
  // Phase 19B contract: a result block from a prior payload must NOT
  // remain on screen after the operator picks a different fixture - the
  // displayed result must always belong to the most recently submitted
  // payload.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  // Default BGP fixture - produce a result.
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()
  const result = panel.locator('.telemetry-panel__result')
  await expect(result).toBeVisible({ timeout: 5_000 })
  await expect(result).toContainText('bgp_neighbor_down')

  // Switch fixture - the stale BGP result must disappear BEFORE the
  // operator clicks preview again. If we left it on screen, it would
  // misrepresent the unknown fixture they're about to submit.
  await panel.getByLabel('telemetry sample fixture').selectOption('unknown')
  await expect(result).toHaveCount(0)

  // Now re-submit and confirm the fresh result reflects the new fixture
  // with zero BGP leakage.
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()
  await expect(result).toBeVisible({ timeout: 5_000 })
  await expect(result).toContainText('telemetry_observation')
  await expect(result).not.toContainText('bgp_neighbor_down')
})

test('Reset to sample after editing returns to the active fixture, not BGP', async ({
  page,
}) => {
  // Phase 19A semantics preserved by Phase 19B: Reset snaps back to the
  // currently-active fixture, not always BGP. So picking unknown, then
  // editing the textarea, then resetting must bring back the unknown
  // fixture's JSON - never BGP.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  await panel.getByLabel('telemetry sample fixture').selectOption('unknown')
  const textarea = panel.getByLabel('telemetry event json')
  await expect(textarea).toHaveValue(/vendor_proprietary_trap/)

  // Edit the textarea to something completely different so we can prove
  // the reset actually restored the unknown fixture (not just left
  // whatever was there).
  await textarea.fill('{ "edited": true }')
  await expect(textarea).not.toHaveValue(/vendor_proprietary_trap/)
  await expect(textarea).toHaveValue('{ "edited": true }')

  await panel.getByRole('button', { name: /^Reset to sample$/i }).click()

  // Active fixture is unknown, so reset returns to vendor_proprietary_trap.
  await expect(textarea).toHaveValue(/vendor_proprietary_trap/)
  // And critically NOT BGP - that would be the bug if Reset always went
  // back to the default fixture instead of the active one.
  await expect(textarea).not.toHaveValue(/bgp_neighbor_down/)
})

test('Telemetry preview: Download JSON exports the default fixture as telemetry-bgp.json', async ({
  page,
}) => {
  // Phase 20A: client-only export via Blob + <a download>. Playwright's
  // waitForEvent('download') captures the browser-initiated download; we
  // then read the temp file path to verify the body matches the textarea.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  const downloadPromise = page.waitForEvent('download')
  await panel.getByRole('button', { name: /^Download JSON$/ }).click()
  const download = await downloadPromise

  // Filename is deterministic and ends with .json.
  expect(download.suggestedFilename()).toBe('telemetry-bgp.json')
  expect(download.suggestedFilename()).toMatch(/\.json$/)

  const downloadPath = await download.path()
  expect(downloadPath).not.toBeNull()
  const content = await readFile(downloadPath!, 'utf-8')

  // The default fixture is BGP-shaped.
  expect(content).toContain('"event_type": "bgp_neighbor_down"')
  // And it round-trips as valid JSON.
  expect(() => JSON.parse(content)).not.toThrow()
})

test('Telemetry preview: Download JSON respects the active fixture and makes zero telemetry API calls', async ({
  page,
}) => {
  // Phase 20A no-network contract: clicking Download JSON must NOT fire
  // a request to either telemetry endpoint. Intercept both and count.
  let validateCallCount = 0
  let correlateCallCount = 0
  await page.route('**/api/telemetry/validate', async (route) => {
    validateCallCount += 1
    await route.continue()
  })
  await page.route('**/api/telemetry/correlate/preview', async (route) => {
    correlateCallCount += 1
    await route.continue()
  })

  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  // Switch to the unknown vendor fixture so we can pin BOTH the filename
  // suffix AND the body content to that fixture.
  await panel.getByLabel('telemetry sample fixture').selectOption('unknown')

  const downloadPromise = page.waitForEvent('download')
  await panel.getByRole('button', { name: /^Download JSON$/ }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toBe('telemetry-unknown.json')
  const content = await readFile((await download.path())!, 'utf-8')
  expect(content).toContain('"event_type": "vendor_proprietary_trap"')

  // Settle for any late-firing request, then assert NEITHER endpoint was
  // contacted. Clicking Download must be a purely client-side action.
  await page.waitForTimeout(250)
  expect(validateCallCount).toBe(0)
  expect(correlateCallCount).toBe(0)
})

test('Telemetry preview: Download JSON filename is sanitized and stable for all five fixtures', async ({
  page,
}) => {
  // Phase 20B contract: the safeTelemetryFilename pipeline must leave the
  // five current fixture ids byte-for-byte unchanged. If a future change
  // to the helper accidentally over-sanitizes one of them (e.g. eats the
  // hyphen in 'route-missing'), this test fails immediately. Keeps prior
  // downloads + any operator scripts that consume these filenames working.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  const cases: { fixtureId: string; expectedFilename: string }[] = [
    { fixtureId: 'bgp', expectedFilename: 'telemetry-bgp.json' },
    { fixtureId: 'interface', expectedFilename: 'telemetry-interface.json' },
    { fixtureId: 'latency', expectedFilename: 'telemetry-latency.json' },
    {
      fixtureId: 'route-missing',
      expectedFilename: 'telemetry-route-missing.json',
    },
    { fixtureId: 'unknown', expectedFilename: 'telemetry-unknown.json' },
  ]

  for (const { fixtureId, expectedFilename } of cases) {
    await panel.getByLabel('telemetry sample fixture').selectOption(fixtureId)
    const downloadPromise = page.waitForEvent('download')
    await panel.getByRole('button', { name: /^Download JSON$/ }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe(expectedFilename)
  }
})

test('Telemetry preview: Download JSON exports raw textarea contents even when JSON is invalid', async ({
  page,
}) => {
  // Phase 20A: download is export, not validation. The button must work
  // regardless of whether the textarea parses as JSON - operators may want
  // to save a draft to fix offline.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  const textarea = panel.getByLabel('telemetry event json')
  const badPayload = '{ this is not valid json }'
  await textarea.fill(badPayload)

  const downloadPromise = page.waitForEvent('download')
  await panel.getByRole('button', { name: /^Download JSON$/ }).click()
  const download = await downloadPromise

  const content = await readFile((await download.path())!, 'utf-8')
  expect(content).toBe(badPayload)
})

test('Telemetry preview: unknown vendor fixture falls back to telemetry_observation', async ({
  page,
}) => {
  // Phase 19A contract: the unknown vendor fixture has no rule keywords
  // in event_type or message, so the Phase 18B correlator must fall
  // through to telemetry_observation. Pinning all four flags here so a
  // future rule rewrite that accidentally swept unknown traps into a
  // specific incident_type would fail this test.
  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()

  await panel.getByLabel('telemetry sample fixture').selectOption('unknown')
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()

  const result = panel.locator('.telemetry-panel__result')
  await expect(result).toBeVisible({ timeout: 5_000 })
  await expect(result).toContainText('telemetry_observation')

  // The dl pairs key/value text. Anchor each assertion to the specific
  // <dt> so we don't accidentally match a substring elsewhere on the page.
  const dl = result.locator('.telemetry-panel__dl')
  await expect(
    dl.locator('dt', { hasText: 'would_create_incident' }),
  ).toBeVisible()
  await expect(
    dl.locator('dt', { hasText: 'would_create_incident' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('false')
  await expect(
    dl.locator('dt', { hasText: 'would_create_event' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('true')
  await expect(
    dl.locator('dt', { hasText: 'persisted' })
      .locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('false')
})

test('Telemetry preview API: Preview correlation POSTs the body and the response carries persisted=false', async ({
  page,
}) => {
  // Phase 18D client-contract coverage for `api.previewTelemetryCorrelation`.
  // Intercept BOTH directions: the outbound POST body AND the response
  // body. route.fetch() forwards to the real backend and lets us inspect
  // the response before fulfilling it back to the page.
  const captured: { method: string; body: string }[] = []
  let responseJson: Record<string, unknown> | null = null
  await page.route(
    '**/api/telemetry/correlate/preview',
    async (route, request) => {
      captured.push({
        method: request.method(),
        body: request.postData() ?? '',
      })
      const response = await route.fetch()
      const text = await response.text()
      responseJson = JSON.parse(text) as Record<string, unknown>
      await route.fulfill({ response, body: text })
    },
  )

  await page.goto('/')
  const panel = page.locator('.telemetry-panel')
  await panel.locator('summary').first().click()
  await panel.getByRole('button', { name: /^Preview correlation$/i }).click()

  await expect(panel.locator('.telemetry-panel__result')).toBeVisible({
    timeout: 5_000,
  })

  // Outbound: POST, body matches the BGP sample.
  expect(captured).toHaveLength(1)
  expect(captured[0].method).toBe('POST')
  const requestBody = JSON.parse(captured[0].body)
  expect(requestBody.event_type).toBe('bgp_neighbor_down')
  expect(requestBody.collector_type).toBe('snmp')

  // Inbound: response carries the Phase 18B contract - persisted=false,
  // and the BGP-rule mapping landed.
  expect(responseJson).not.toBeNull()
  const body = responseJson!
  expect(body.persisted).toBe(false)
  expect(body.suggested_incident_type).toBe('bgp_neighbor_down')
  expect(body.would_create_incident).toBe(true)
  expect(body.would_create_event).toBe(true)
})

test('Generate RCA shows the RCA section and it persists past the response', async ({
  page,
}) => {
  await selectBgpIncident(page)

  await page.getByRole('button', { name: 'Generate RCA' }).click()

  // Either branch satisfies the assertion. Live Ollama may take a while.
  await expect(page.locator('.action-msg')).toContainText(
    /RCA via .+ \(LLM available\)|RCA fallback used/,
    { timeout: 60_000 },
  )

  // The Phase 9A cleanup proved the section survives once shown - explicitly
  // confirm it doesn't vanish after the action settles (the bug we fixed was
  // a refetch wiping the local rca state).
  await expect(
    page.getByRole('heading', { name: /RCA explanation/i }),
  ).toBeVisible()
  await page.waitForTimeout(1500)
  await expect(
    page.getByRole('heading', { name: /RCA explanation/i }),
  ).toBeVisible()
})

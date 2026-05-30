// Phase 12A operator-console smoke. Intentionally small + serial - one
// shared seeded database that the tests read and (carefully) extend. Mocks
// are avoided; we hit the real backend through the same Vite proxy a user
// would. RCA is tested in a fallback-safe way so the suite doesn't require
// a running Ollama daemon.

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

test('Create an operator via the management panel, then approve a plan with it', async ({
  page,
}) => {
  // Unique-per-run name so re-running the suite doesn't 409 on the create
  // call. These operator rows accumulate in the dev DB and are documented
  // as such; no delete endpoint exists and removing them isn't worth the
  // psql plumbing in teardown.
  const uniqueName = `e2e-ui-op-${Date.now()}`

  await page.goto('/')

  // Open the OperatorsPanel inline form.
  await page.getByRole('button', { name: '+ Add operator' }).click()
  await page.getByLabel('new operator name').fill(uniqueName)
  await page.getByLabel('new operator role').selectOption('operator')
  await page.getByRole('button', { name: 'Create operator' }).click()

  // The new operator chip must appear in the panel without a page reload.
  const chip = page.locator('.operator-chip', { hasText: uniqueName })
  await expect(chip).toBeVisible({ timeout: 5_000 })
  // Phase 13B contract: chip carries role + a created_at signal.
  await expect(chip).toContainText('[operator]')
  await expect(chip.locator('.operator-chip__when')).toBeVisible()

  // Submitting the same display_name again must surface the 409 inline via
  // the role=alert element.
  await page.getByRole('button', { name: '+ Add operator' }).click()
  await page.getByLabel('new operator name').fill(uniqueName)
  await page.getByRole('button', { name: 'Create operator' }).click()
  const dupErr = page.getByRole('alert')
  await expect(dupErr).toBeVisible({ timeout: 5_000 })
  await expect(dupErr).toContainText(/already exists/i)
  // Close the dup form so it doesn't bleed into the approval flow below.
  await page.getByRole('button', { name: 'Cancel' }).click()

  // Now drive the approval flow using THIS just-created operator.
  await page
    .locator('button.incident-row', { hasText: BGP_TITLE_PATTERN })
    .click()
  await expect(
    page.getByRole('heading', { name: /Anomaly findings/i }),
  ).toBeVisible()

  const planCountBefore = await page.locator('.plan-card').count()
  await page
    .getByRole('button', { name: 'Generate remediation plan' })
    .click()
  await expect(page.locator('.plan-card')).toHaveCount(planCountBefore + 1, {
    timeout: 20_000,
  })

  const newest = page.locator('.plan-card').first()
  await newest.locator('summary').click()
  await newest.getByRole('button', { name: 'Approve' }).click()

  const form = newest.locator('.approval-form')
  await expect(form).toBeVisible()
  await form
    .getByLabel('operator', { exact: true })
    .selectOption({ label: `${uniqueName} (operator)` })
  await form.getByLabel('approval note').fill('approved with UI-created op')
  await form
    .getByRole('button', { name: /^Confirm approve$/i })
    .click()

  await expect(newest.locator('.badge--approval-approved')).toBeVisible({
    timeout: 10_000,
  })
  await expect(newest.locator('.plan-card__approval')).toContainText(
    uniqueName,
  )
  await expect(newest.locator('.plan-card__approval')).toContainText(
    'approved with UI-created op',
  )
})


test('Approve a plan via the inline form shows approved badge + operator + note', async ({
  page,
}) => {
  await selectBgpIncident(page)

  // Make sure there is at least one plan with status `pending` to approve.
  // Generate a fresh one so we don't depend on prior-test state.
  const planCountBefore = await page.locator('.plan-card').count()
  await page.getByRole('button', { name: 'Generate remediation plan' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(planCountBefore + 1, {
    timeout: 20_000,
  })

  // Phase 13A: inline form has an operator dropdown populated from
  // /api/operators. global-setup.ts seeds `local-operator` for this test.
  const newest = page.locator('.plan-card').first()
  await newest.locator('summary').click()
  await newest.getByRole('button', { name: 'Approve' }).click()

  // Form is now visible inside the plan card.
  const form = newest.locator('.approval-form')
  await expect(form).toBeVisible()

  // Submit should be disabled until an operator is picked or a name typed.
  const confirmBtn = form.getByRole('button', { name: /^Confirm approve$/i })
  await expect(confirmBtn).toBeDisabled()

  // Select `local-operator` from the dropdown (seeded by global-setup with
  // role=admin, so the rendered option text is "local-operator (admin)").
  // `exact: true` is required because the other input is aria-labelled
  // "operator name" - a partial match would resolve to both.
  await form
    .getByLabel('operator', { exact: true })
    .selectOption({ label: 'local-operator (admin)' })
  await form.getByLabel('approval note').fill('approved during smoke run')

  await expect(confirmBtn).toBeEnabled()
  await confirmBtn.click()

  // The refreshed plan card carries the approval state (Phase 10A's cleanup
  // intentionally relies on the card's own approval block, not an ephemeral
  // inline message). The inline form should collapse on success.
  await expect(newest.locator('.badge--approval-approved')).toBeVisible({
    timeout: 10_000,
  })
  // Phase 13A: approved_by is resolved from the Operator's display_name.
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

import { useState } from 'react'
import {
  ApiError,
  api,
  type TelemetryCorrelationPreview,
  type TelemetryEvent,
} from '../api'

// Phase 19A fixture set. Hard-coded, in-memory only - never read from or
// written to localStorage / sessionStorage / cookies / URL params. The
// dropdown above the textarea picks one; selecting replaces the textarea
// contents with the fixture's formatted JSON. Operator edits are local
// React state and vanish on reload by design.
//
// Each fixture is shaped to exercise a specific Phase 18B correlator
// branch (BGP / interface / latency / route / fallback). The "unknown
// vendor trap" deliberately uses no rule keywords in event_type / message
// so it falls through to telemetry_observation - that's the contract the
// fallback test pins.
interface TelemetryFixture {
  id: string
  label: string
  event: Record<string, unknown>
}

const FIXTURES: TelemetryFixture[] = [
  {
    id: 'bgp',
    label: 'BGP neighbor down',
    event: {
      source: 'snmp:edge-1',
      collector_type: 'snmp',
      hostname: 'edge-1',
      mgmt_ip: '10.0.0.11',
      device_hint: 'edge-1.lab',
      observed_at: '2026-05-30T12:00:00Z',
      event_type: 'bgp_neighbor_down',
      severity: 'critical',
      message: 'BGP neighbor 10.0.0.21 transitioned to Idle',
      labels: { neighbor: '10.0.0.21', vrf: 'default' },
      raw: { trap_oid: '1.3.6.1.4.1.9.9.187.0.1', peer_state: 'idle' },
    },
  },
  {
    id: 'interface',
    label: 'Interface down / errors',
    event: {
      source: 'snmp:core-1',
      collector_type: 'snmp',
      hostname: 'core-1',
      mgmt_ip: '10.0.0.1',
      device_hint: 'core-1.lab',
      observed_at: '2026-05-30T12:00:00Z',
      event_type: 'interface_down',
      severity: 'error',
      message: 'GigabitEthernet0/1 transitioned to down',
      labels: { interface: 'Gi0/1', site: 'dc-a' },
      raw: {
        ifname: 'GigabitEthernet0/1',
        oid: '1.3.6.1.2.1.2.2.1.8.1',
        value: '2',
      },
    },
  },
  {
    id: 'latency',
    label: 'Latency spike',
    event: {
      source: 'syslog:edge-2',
      collector_type: 'syslog',
      hostname: 'edge-2',
      mgmt_ip: '10.0.0.12',
      device_hint: 'edge-2.lab',
      observed_at: '2026-05-30T12:00:00Z',
      event_type: 'latency_spike',
      severity: 'warning',
      message: 'RTT to 10.0.0.21 above 500 ms over last 60 s',
      labels: { peer_ip: '10.0.0.21', path: 'edge-2->edge-1' },
      raw: { metric: 'rtt_ms', value: 612.4 },
    },
  },
  {
    id: 'route-missing',
    label: 'Route missing / withdrawn',
    event: {
      source: 'syslog:core-1',
      collector_type: 'syslog',
      hostname: 'core-1',
      mgmt_ip: '10.0.0.1',
      device_hint: 'core-1.lab',
      observed_at: '2026-05-30T12:00:00Z',
      event_type: 'route_withdrawn',
      severity: 'error',
      message: 'prefix 10.0.0.0/24 withdrawn from RIB',
      labels: { prefix: '10.0.0.0/24', protocol: 'bgp' },
      raw: { rib: 'inet.0', via: '10.0.0.21' },
    },
  },
  {
    id: 'unknown',
    label: 'Unknown vendor trap (fallback)',
    event: {
      source: 'snmp:custom-vendor-7',
      collector_type: 'snmp',
      hostname: 'custom-vendor-7',
      mgmt_ip: '10.0.0.99',
      device_hint: 'custom-vendor-7.lab',
      observed_at: '2026-05-30T12:00:00Z',
      event_type: 'vendor_proprietary_trap',
      severity: 'info',
      // No BGP / interface / latency / route / ACL keyword on purpose -
      // this must fall through to telemetry_observation.
      message:
        'device emitted a vendor-specific diagnostic (no NeuroNOC rule mapped)',
      labels: { vendor: 'acme-net', trap_kind: 'diag-notify' },
      raw: { trap_oid: '1.3.6.1.4.1.99999.1.2.3', value: 'informational' },
    },
  },
]

const DEFAULT_FIXTURE_ID = 'bgp'

function fixtureJson(fixture: TelemetryFixture): string {
  return JSON.stringify(fixture.event, null, 2)
}

function findFixture(id: string): TelemetryFixture {
  return FIXTURES.find((f) => f.id === id) ?? FIXTURES[0]
}

// Phase 20B: keep export filenames deterministic and filesystem-safe even
// if a future fixture id carries surprising characters. The five current
// fixture ids (bgp / interface / latency / route-missing / unknown) all
// round-trip unchanged through this pipeline.
//
// Pipeline:
//   lowercase -> trim -> replace runs of non [a-z0-9-] with single '-'
//   -> collapse repeated '-' -> trim leading/trailing '-'
//   -> fallback to 'event' if empty
//
// Defends against path traversal (`foo/../bar` -> `foo-bar`), control
// characters, Unicode oddness (zero-width spaces, etc.), and empty input.
function safeTelemetryFilename(id: string): string {
  const sanitized = id
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
  return `telemetry-${sanitized || 'event'}.json`
}

// Phase 18C operator-facing wrapper around the Phase 18A `/validate` and
// Phase 18B `/correlate/preview` endpoints. Read-only by construction:
// - neither endpoint persists anything (backend row-count tests pin this)
// - this component never reads or writes localStorage / sessionStorage
// - errors are split into "JSON parse failed locally" vs "API rejected"
//   so the operator can tell which layer needs fixing
//
// Default-collapsed via <details> so it doesn't clutter the main workflow.
export function TelemetryPanel() {
  // Phase 19A: active fixture id picks which sample replaces the textarea
  // on "Reset to sample" and which entry is highlighted in the dropdown.
  // Pure React state - never written to localStorage / sessionStorage /
  // cookies / URL params.
  const [activeFixtureId, setActiveFixtureId] = useState(DEFAULT_FIXTURE_ID)
  const [json, setJson] = useState(() => fixtureJson(findFixture(DEFAULT_FIXTURE_ID)))
  const [parseError, setParseError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [validateLoading, setValidateLoading] = useState(false)
  const [correlateLoading, setCorrelateLoading] = useState(false)
  const [validateResult, setValidateResult] = useState<TelemetryEvent | null>(
    null,
  )
  const [correlateResult, setCorrelateResult] =
    useState<TelemetryCorrelationPreview | null>(null)

  // Parse the textarea contents. Returns null and sets parseError on failure;
  // returns the parsed object (always an object since the schema requires it)
  // on success. Clears previous parseError on success.
  function parsePayload(): unknown | null {
    try {
      const parsed = JSON.parse(json) as unknown
      setParseError(null)
      return parsed
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'invalid JSON')
      return null
    }
  }

  async function onValidate() {
    const payload = parsePayload()
    if (payload === null) return
    setValidateLoading(true)
    setApiError(null)
    try {
      const data = await api.validateTelemetry(payload)
      setValidateResult(data)
    } catch (err) {
      setApiError(
        err instanceof ApiError ? err.detail : 'validate request failed',
      )
    } finally {
      setValidateLoading(false)
    }
  }

  async function onCorrelate() {
    const payload = parsePayload()
    if (payload === null) return
    setCorrelateLoading(true)
    setApiError(null)
    try {
      const data = await api.previewTelemetryCorrelation(payload)
      setCorrelateResult(data)
    } catch (err) {
      setApiError(
        err instanceof ApiError
          ? err.detail
          : 'correlate preview request failed',
      )
    } finally {
      setCorrelateLoading(false)
    }
  }

  // Phase 19B: any operation that changes the payload source - picking a
  // new fixture, manually editing the textarea, or resetting to the active
  // fixture - must clear the previously-rendered result so the operator
  // never sees a result block that belongs to an older payload.
  function clearResultsAndErrors() {
    setValidateResult(null)
    setCorrelateResult(null)
    setParseError(null)
    setApiError(null)
  }

  function loadFixture(id: string) {
    const fixture = findFixture(id)
    setActiveFixtureId(fixture.id)
    setJson(fixtureJson(fixture))
    clearResultsAndErrors()
  }

  function resetToSample() {
    // Snap back to the currently-active fixture so picking "unknown vendor"
    // then editing then resetting brings back the unknown vendor, not BGP.
    setJson(fixtureJson(findFixture(activeFixtureId)))
    clearResultsAndErrors()
  }

  function downloadJson() {
    // Phase 20A: client-only export. No backend call, no upload path,
    // no persistence. Uses browser primitives only: Blob + object URL +
    // temporary <a download>. Even invalid JSON downloads as raw text -
    // this is export, not validation.
    const filename = safeTelemetryFilename(activeFixtureId)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    // Some browsers ignore clicks on nodes that aren't in the document, so
    // attach briefly. Detach immediately after click - the URL has been
    // captured by the download flow at that point.
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // Defer revoke by one tick so the download has fully initiated before
    // the object URL is invalidated. Standard pattern; safe across browsers.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  const busy = validateLoading || correlateLoading

  return (
    <section className="telemetry-panel" aria-label="telemetry preview">
      <details className="telemetry-panel__details">
        <summary className="telemetry-panel__summary">
          <span className="telemetry-panel__title">Telemetry preview</span>
          <span className="muted telemetry-panel__caveat">
            preview only · no persistence · no device contact
          </span>
        </summary>

        <div className="telemetry-panel__body">
          <p className="muted telemetry-panel__intro">
            Paste a normalized telemetry event. <code>Validate</code> runs
            the Phase 18A schema check; <code>Preview correlation</code>{' '}
            shows how Phase 18B would map it to an incident. Neither call
            persists anything or contacts a device.
          </p>

          <div className="telemetry-panel__fixture-row">
            <label
              className="telemetry-panel__fixture-label"
              htmlFor="telemetry-fixture"
            >
              Sample fixture
            </label>
            <select
              id="telemetry-fixture"
              className="telemetry-panel__fixture-select"
              aria-label="telemetry sample fixture"
              value={activeFixtureId}
              onChange={(e) => loadFixture(e.target.value)}
              disabled={busy}
            >
              {FIXTURES.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
            <span className="muted telemetry-panel__fixture-hint">
              session-only · selecting replaces the textarea contents
            </span>
          </div>

          <label className="telemetry-panel__label" htmlFor="telemetry-json">
            Telemetry event JSON
          </label>
          <textarea
            id="telemetry-json"
            className="telemetry-panel__textarea"
            aria-label="telemetry event json"
            rows={14}
            spellCheck={false}
            value={json}
            onChange={(e) => {
              // Phase 19B: editing the JSON makes any visible result stale -
              // it was produced for a different payload. Clear the result
              // blocks AND both error kinds so the operator sees a clean
              // slate and submits to see fresh output.
              if (e.target.value !== json) {
                setJson(e.target.value)
                clearResultsAndErrors()
              }
            }}
            disabled={busy}
          />

          <div className="telemetry-panel__actions">
            <button
              type="button"
              className="btn btn--action"
              onClick={() => void onValidate()}
              disabled={busy}
            >
              {validateLoading ? 'Validating...' : 'Validate'}
            </button>
            <button
              type="button"
              className="btn btn--action"
              onClick={() => void onCorrelate()}
              disabled={busy}
            >
              {correlateLoading ? 'Previewing...' : 'Preview correlation'}
            </button>
            <button
              type="button"
              className="btn btn--action btn--small"
              onClick={resetToSample}
              disabled={busy}
            >
              Reset to sample
            </button>
            <button
              type="button"
              className="btn btn--action btn--small"
              onClick={downloadJson}
              title="Download the current textarea as a local .json file (client-only export; no backend call)"
            >
              Download JSON
            </button>
          </div>

          {parseError && (
            <div
              className="error-banner telemetry-panel__parse-error"
              role="alert"
            >
              Invalid JSON (not sent to backend): {parseError}
            </div>
          )}
          {apiError && (
            <div className="error-banner telemetry-panel__api-error" role="alert">
              API rejected payload: {apiError}
            </div>
          )}

          {(validateResult || correlateResult) && (
            <div className="muted telemetry-panel__results-caveat">
              Results reflect the last submitted payload.
            </div>
          )}

          {validateResult && (
            <div className="telemetry-panel__result">
              <strong>Validated event</strong>
              <span className="muted"> — normalized, not persisted.</span>
              <pre className="telemetry-panel__pre">
                {JSON.stringify(validateResult, null, 2)}
              </pre>
            </div>
          )}

          {correlateResult && (
            <CorrelateResultBlock preview={correlateResult} />
          )}
        </div>
      </details>
    </section>
  )
}

function CorrelateResultBlock({
  preview,
}: {
  preview: TelemetryCorrelationPreview
}) {
  return (
    <div className="telemetry-panel__result">
      <strong>Correlation preview</strong>
      <span className="muted"> — preview only, persisted: false.</span>
      <dl className="telemetry-panel__dl">
        <dt>suggested_incident_type</dt>
        <dd>
          <code>{preview.suggested_incident_type}</code>
        </dd>
        <dt>suggested_title</dt>
        <dd>{preview.suggested_title}</dd>
        <dt>suggested_severity</dt>
        <dd>
          <code>{preview.suggested_severity}</code>
        </dd>
        <dt>correlation_key</dt>
        <dd>
          <code>{preview.correlation_key}</code>
        </dd>
        <dt>confidence</dt>
        <dd>{preview.confidence}</dd>
        <dt>would_create_incident</dt>
        <dd>
          <code>{String(preview.would_create_incident)}</code>
        </dd>
        <dt>would_create_event</dt>
        <dd>
          <code>{String(preview.would_create_event)}</code>
        </dd>
        <dt>persisted</dt>
        <dd>
          <code>{String(preview.persisted)}</code>
        </dd>
      </dl>
      {preview.rationale.length > 0 && (
        <div className="telemetry-panel__rationale">
          <div className="telemetry-panel__rationale-label">rationale</div>
          <ul>
            {preview.rationale.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

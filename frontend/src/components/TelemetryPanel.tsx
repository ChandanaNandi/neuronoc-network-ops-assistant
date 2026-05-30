import { useState } from 'react'
import {
  ApiError,
  api,
  type TelemetryCorrelationPreview,
  type TelemetryEvent,
} from '../api'

// Hard-coded session-only sample. Never written to localStorage / cookies.
// BGP-shaped so clicking "Preview correlation" out of the box exercises the
// most informative rule branch.
const SAMPLE_EVENT_JSON = JSON.stringify(
  {
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
  null,
  2,
)

// Phase 18C operator-facing wrapper around the Phase 18A `/validate` and
// Phase 18B `/correlate/preview` endpoints. Read-only by construction:
// - neither endpoint persists anything (backend row-count tests pin this)
// - this component never reads or writes localStorage / sessionStorage
// - errors are split into "JSON parse failed locally" vs "API rejected"
//   so the operator can tell which layer needs fixing
//
// Default-collapsed via <details> so it doesn't clutter the main workflow.
export function TelemetryPanel() {
  const [json, setJson] = useState(SAMPLE_EVENT_JSON)
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

  function resetToSample() {
    setJson(SAMPLE_EVENT_JSON)
    setParseError(null)
    setApiError(null)
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
              setJson(e.target.value)
              // Clear parse error as soon as the user edits; let them see
              // success/failure on the next submit instead of stale red.
              if (parseError) setParseError(null)
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

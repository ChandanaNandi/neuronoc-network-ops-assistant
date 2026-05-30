"""Phase 22B: turn a persisted telemetry observation into an Incident.

Single entry point: `correlate_persisted_telemetry_observation(db, observation_id)`
loads a `TelemetryObservation`, re-runs the existing Phase 18B deterministic
`build_correlation_preview` over its payload, and persists an `Incident` +
`IncidentEvent` when the preview signals incident-worthy.

Hard rules (Phase 22B):
- Deterministic only. No LLM call, no remediation code, no device contact.
- Idempotent. If `observation.created_incident_id` is already set, this
  returns the existing Incident link rather than creating a duplicate.
- Generic fallback (`telemetry_observation` mapping with
  `would_create_incident=False`) does NOT create an Incident and leaves
  `created_incident_id` NULL.
- One Incident per correlation; one `IncidentEvent` per correlation. No
  extra evidence rows; future phases can layer more.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Incident, IncidentEvent, TelemetryObservation
from app.schemas.telemetry import PersistedCorrelationResult
from app.telemetry.correlator import (
    TelemetryCorrelationPreview,
    build_correlation_preview,
)
from app.telemetry.events import TelemetryEvent


class TelemetryObservationNotFoundError(Exception):
    """Raised when the requested observation id is unknown (-> 404)."""


def _load_event_from_observation(obs: TelemetryObservation) -> TelemetryEvent:
    """Round-trip the stored JSONB payload back through `TelemetryEvent`.

    The payload was written by `persist_telemetry_observation` via
    `model_dump(mode="json")`, so it parses cleanly. Re-validating here
    guarantees the correlator only sees well-formed events even if a
    future row was written by a less-careful caller.
    """
    return TelemetryEvent.model_validate(obs.payload)


def correlate_persisted_telemetry_observation(
    db: Session, observation_id: UUID
) -> PersistedCorrelationResult:
    """Correlate one persisted observation into an Incident.

    Returns a `PersistedCorrelationResult` describing what happened
    (correlated / incident_created / incident_id / suggested_incident_type
    / rationale). Raises `TelemetryObservationNotFoundError` if the id
    doesn't exist.
    """
    obs = db.get(TelemetryObservation, observation_id)
    if obs is None:
        raise TelemetryObservationNotFoundError(
            f"telemetry observation {observation_id} not found"
        )

    event = _load_event_from_observation(obs)
    preview: TelemetryCorrelationPreview = build_correlation_preview(event)

    # Path 1: already linked. Return the existing Incident; don't duplicate.
    if obs.created_incident_id is not None:
        return PersistedCorrelationResult(
            observation_id=obs.id,
            correlated=True,
            incident_created=False,
            incident_id=obs.created_incident_id,
            suggested_incident_type=preview.suggested_incident_type,
            rationale=list(preview.rationale),
        )

    # Path 2: generic fallback. Don't create an incident.
    if not preview.would_create_incident:
        return PersistedCorrelationResult(
            observation_id=obs.id,
            correlated=False,
            incident_created=False,
            incident_id=None,
            suggested_incident_type=preview.suggested_incident_type,
            rationale=list(preview.rationale),
        )

    # Path 3: create Incident + one IncidentEvent. Link back on the
    # observation row so a re-run hits path 1.
    incident = Incident(
        title=preview.suggested_title,
        severity=preview.suggested_severity,
        incident_type=preview.suggested_incident_type,
        summary=(
            f"[telemetry-correlator] Incident derived from telemetry "
            f"observation {obs.id} (source={obs.source})."
        ),
    )
    db.add(incident)
    db.flush()

    event_row = IncidentEvent(
        incident_id=incident.id,
        event_type=preview.suggested_event_type,
        source=preview.suggested_event_source,
        message=event.message,
        payload=preview.suggested_event_payload,
    )
    db.add(event_row)

    obs.created_incident_id = incident.id
    db.add(obs)
    db.commit()
    db.refresh(incident)
    db.refresh(obs)

    return PersistedCorrelationResult(
        observation_id=obs.id,
        correlated=True,
        incident_created=True,
        incident_id=incident.id,
        suggested_incident_type=preview.suggested_incident_type,
        rationale=list(preview.rationale),
    )

"""Phase 22A telemetry persistence helper.

Single entry point: `persist_telemetry_observation(db, event)` writes
one `TelemetryObservation` row from a validated `TelemetryEvent` and
returns the persisted ORM instance.

Hard rules (Phase 22A):
- NO auto-incident creation. `created_incident_id` is always NULL on
  rows produced by this phase; a later phase may wire correlation.
- NO LLM call, NO device contact, NO subprocess.
- Vendor pulled from `event.labels["vendor"]` only when present; never
  inferred or guessed.
- Idempotency is the caller's concern. Each call writes a fresh row;
  duplicate detection belongs to the future correlation layer.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import TelemetryObservation
from app.telemetry.events import TelemetryEvent


def persist_telemetry_observation(
    db: Session, event: TelemetryEvent
) -> TelemetryObservation:
    """Insert one `TelemetryObservation` row from a validated event.

    Returns the refreshed ORM instance with server-side defaults
    (id, received_at) populated.
    """
    vendor = event.labels.get("vendor") if event.labels else None
    obs = TelemetryObservation(
        source=event.source,
        vendor=vendor,
        observation_type=event.event_type,
        payload=event.model_dump(mode="json"),
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs

"""Phase 22A telemetry persistence schemas.

Wraps the existing `TelemetryEvent` (transport DTO) with read/create
shapes for the persisted `TelemetryObservation` table. Phase 22A is
persistence-only: NO auto-incident creation, NO auto-correlation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TelemetryObservationRead(BaseModel):
    """Read shape for a persisted telemetry observation row.

    `payload` carries the full normalized `TelemetryEvent` dict
    (`model_dump(mode="json")`) — flat JSONB on the way out so callers
    can round-trip without re-validating the inner shape. Phase 22A
    does NOT populate `created_incident_id`; it will always be `None`
    for rows created by this phase.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    vendor: str | None = None
    observation_type: str
    payload: dict[str, Any]
    received_at: datetime
    created_incident_id: UUID | None = None


class PersistedCorrelationResult(BaseModel):
    """Phase 22B: result of running deterministic correlation against a
    persisted `TelemetryObservation`.

    - `correlated` is False only for the generic-fallback path
      (`telemetry_observation` suggestion) where no Incident is opened.
    - `incident_created` is False when correlation reused an existing
      linked Incident (idempotency) OR when nothing was created at all.
    - `incident_id` is set whenever an Incident exists (newly created
      OR re-linked); null only on the no-correlation path.
    """

    model_config = ConfigDict(extra="forbid")

    observation_id: UUID
    correlated: bool
    incident_created: bool
    incident_id: UUID | None = None
    suggested_incident_type: str
    rationale: list[str]

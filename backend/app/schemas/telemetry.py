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

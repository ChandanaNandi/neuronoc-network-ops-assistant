"""Phase 18A telemetry validate endpoint.

POST-only by accident - it accepts a JSON body, runs it through the
`TelemetryEvent` model, and echoes the normalized form back. NO database
write, NO device contact, NO socket. FastAPI / Pydantic do the heavy
lifting; the handler is one line on purpose.

This is the only HTTP surface for `app/telemetry/` in Phase 18A; real
ingest endpoints land in a later phase under separate review.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import TelemetryObservation
from app.db.session import get_db
from app.schemas.telemetry import TelemetryObservationRead
from app.telemetry.correlator import (
    TelemetryCorrelationPreview,
    build_correlation_preview,
)
from app.telemetry.events import TelemetryEvent
from app.telemetry.persistence import persist_telemetry_observation

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.post("/validate", response_model=TelemetryEvent)
def validate_telemetry_event(payload: TelemetryEvent) -> TelemetryEvent:
    """Validate and normalize a telemetry payload WITHOUT persisting it.

    Returns the normalized event on success. FastAPI returns 422 with a
    field-level breakdown if the payload fails schema validation
    (`extra="forbid"`, bounds on `raw` / `labels`, enum constraints,
    etc.) - that's deliberately the default Pydantic behavior, no custom
    error handling needed.

    Read-only by construction: the handler does not touch the database
    session, does not contact a device, does not open a socket, does not
    invoke any LLM.
    """
    return payload


@router.post(
    "/correlate/preview", response_model=TelemetryCorrelationPreview
)
def preview_telemetry_correlation(
    payload: TelemetryEvent,
) -> TelemetryCorrelationPreview:
    """Map a `TelemetryEvent` to a preview of how it WOULD land as an
    `Incident` + `IncidentEvent`, WITHOUT persisting anything.

    Pure deterministic projection of the input through the rule mapping
    in `app/telemetry/correlator.py`. The response model pins
    `persisted: Literal[False]` so the type system refuses any future
    attempt to flip this into a write path.

    `would_create_incident` is True for events matched by a specific rule
    (BGP / interface / latency / route / ACL) and False for the generic
    `telemetry_observation` fallback. `would_create_event` is always True
    - every telemetry event would land as an IncidentEvent row.

    Read-only by construction: the handler does not touch the database
    session, does not contact a device, does not open a socket, does not
    invoke any LLM. 422 on schema failure (same Pydantic path as
    `/validate`).
    """
    return build_correlation_preview(payload)


# ============================================================
# Phase 22A - persisted telemetry observations
# ============================================================
#
# Backward-compat note: the `/validate` and `/correlate/preview`
# endpoints above are intentionally unchanged and STILL do not touch
# the database. The Phase 22A persistence path is a NEW endpoint
# triple, not a modification of existing behavior.


@router.post(
    "/observations",
    response_model=TelemetryObservationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_telemetry_observation(
    payload: TelemetryEvent, db: Session = Depends(get_db)
) -> TelemetryObservation:
    """Phase 22A: persist a validated `TelemetryEvent` as a row in the
    `telemetry_observations` table.

    Pydantic validates the body (same `TelemetryEvent` schema the
    Phase 18A `/validate` endpoint uses). On success returns the
    persisted row with server-side defaults (id, received_at) populated.
    `created_incident_id` is always `None` for rows created in Phase 22A
    — no auto-correlation, no auto-incident creation. 422 on schema
    failure.
    """
    return persist_telemetry_observation(db, payload)


@router.get(
    "/observations", response_model=list[TelemetryObservationRead]
)
def list_telemetry_observations(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[TelemetryObservation]:
    """Phase 22A: list persisted telemetry observations, newest first.

    Mirrors the existing `/api/incidents` pagination contract — `limit`
    1..100, 422 for out-of-range values. No filtering parameters in
    Phase 22A; filter / search surfaces land in a later phase if needed.
    """
    # Secondary `id DESC` key gives deterministic ordering when multiple
    # rows share an identical `received_at` (Postgres `now()` resolution
    # can tie under heavy concurrent inserts; the savepoint-based test
    # fixture also exposes this case because `now()` is transaction-start
    # time). UUID v4 isn't time-sortable, but the secondary key is
    # consistent across calls.
    stmt = (
        select(TelemetryObservation)
        .order_by(
            desc(TelemetryObservation.received_at),
            desc(TelemetryObservation.id),
        )
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


@router.get(
    "/observations/{observation_id}",
    response_model=TelemetryObservationRead,
)
def get_telemetry_observation(
    observation_id: UUID, db: Session = Depends(get_db)
) -> TelemetryObservation:
    """Phase 22A: fetch one persisted observation by id; 404 if missing."""
    obs = db.get(TelemetryObservation, observation_id)
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"telemetry observation {observation_id} not found",
        )
    return obs

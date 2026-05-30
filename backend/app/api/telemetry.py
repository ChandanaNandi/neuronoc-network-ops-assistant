"""Phase 18A telemetry validate endpoint.

POST-only by accident - it accepts a JSON body, runs it through the
`TelemetryEvent` model, and echoes the normalized form back. NO database
write, NO device contact, NO socket. FastAPI / Pydantic do the heavy
lifting; the handler is one line on purpose.

This is the only HTTP surface for `app/telemetry/` in Phase 18A; real
ingest endpoints land in a later phase under separate review.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.telemetry.correlator import (
    TelemetryCorrelationPreview,
    build_correlation_preview,
)
from app.telemetry.events import TelemetryEvent

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

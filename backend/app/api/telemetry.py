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

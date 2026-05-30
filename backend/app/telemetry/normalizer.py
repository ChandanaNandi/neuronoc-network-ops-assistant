"""Phase 18A manual-ingest normalizer.

Single function. Takes a dict (or an already-built TelemetryEvent) and
validates it through the `TelemetryEvent` model. NO persistence side
effect: the only result of calling this is a validated model instance
or a `pydantic.ValidationError`.

Persistence (mapping a TelemetryEvent onto an Incident / IncidentEvent
row) is deferred to a later phase - it forces correlation, dedup, and
incident-creation rules that are out of scope for the interface-only
work.
"""

from __future__ import annotations

from typing import Any

from app.telemetry.events import TelemetryEvent


def normalize_manual_event(payload: TelemetryEvent | dict[str, Any]) -> TelemetryEvent:
    """Round-trip `payload` through `TelemetryEvent`.

    Raises:
        pydantic.ValidationError: payload missing required fields,
            unknown fields (extra="forbid"), or violates a bound.
    """
    if isinstance(payload, TelemetryEvent):
        # Already validated - just hand it back so callers can use the
        # function as a single normalization entry point regardless of
        # whether they constructed the model themselves.
        return payload
    return TelemetryEvent.model_validate(payload)

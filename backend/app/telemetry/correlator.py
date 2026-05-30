"""Phase 18B telemetry-to-incident correlation PREVIEW.

Pure deterministic mapping. Given a validated `TelemetryEvent`, produce a
`TelemetryCorrelationPreview` describing how it WOULD land if real ingest
was running - which `incident_type` would be chosen, which severity, which
correlation key would dedupe duplicates, and whether it would open a new
incident vs. only log an event.

Hard rules:
- NO database read or write. Function takes a model in, returns a model out.
- NO device contact, NO socket, NO subprocess, NO LLM.
- `persisted` is `Literal[False]` on the response model - the type system
  refuses any attempt to flip it.
- The mapping rule order is the contract. Tests pin each branch; reorder
  with care.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.telemetry.events import TelemetryEvent, TelemetrySeverity


# ---------- response schema ----------


class TelemetryCorrelationPreview(BaseModel):
    """Read-only "what would happen if this event were ingested?" view.

    `would_create_incident` reflects rule confidence: only events whose
    `suggested_incident_type` is one of the known specific types (not the
    generic `telemetry_observation` fallback) are flagged as incident-
    worthy. `would_create_event` is always True - every telemetry event
    would land as an `IncidentEvent` row, even when not promoted to a
    new incident.

    `persisted` is hard-pinned to False; the type system refuses any
    reassignment, so a future caller can't quietly turn this into a write
    path.
    """

    model_config = ConfigDict(extra="forbid")

    telemetry_event: TelemetryEvent
    suggested_incident_type: str
    suggested_title: str
    suggested_severity: str  # low | medium | high | critical
    suggested_event_type: str
    suggested_event_source: str
    suggested_event_payload: dict[str, Any]
    correlation_key: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: list[str]
    would_create_incident: bool
    would_create_event: bool
    persisted: Literal[False] = False


# ---------- internal helpers ----------


# TelemetrySeverity is broader than Incident severity (which is low / medium
# / high / critical). Collapse: info+notice -> low, warning -> medium,
# error -> high, critical -> critical.
_SEVERITY_MAP: dict[TelemetrySeverity, str] = {
    TelemetrySeverity.info: "low",
    TelemetrySeverity.notice: "low",
    TelemetrySeverity.warning: "medium",
    TelemetrySeverity.error: "high",
    TelemetrySeverity.critical: "critical",
}

# Incident type names align with the existing simulator scenarios so the
# downstream anomaly engine + remediation planner pick up the right rules
# without any new wiring.
_TELEMETRY_OBSERVATION = "telemetry_observation"


def _suggest_severity(event: TelemetryEvent) -> str:
    return _SEVERITY_MAP[event.severity]


def _suggest_mapping(event: TelemetryEvent) -> tuple[str, str, list[str]]:
    """Return (incident_type, suggested_event_type, rationale).

    Rule order matters - more specific patterns first. event_type matches
    weigh higher than message matches (confidence 0.9 vs 0.6).
    """
    et = event.event_type.lower()
    msg = event.message.lower()
    rationale: list[str] = []

    # BGP - check event_type first.
    if "bgp" in et and any(tok in et for tok in ("down", "neighbor", "state")):
        rationale.append(
            f"event_type {event.event_type!r} matches a BGP session shape"
        )
        return ("bgp_neighbor_down", "bgp_state_change", rationale)
    if "bgp" in msg and any(tok in msg for tok in ("down", "neighbor", "session")):
        rationale.append(
            "message mentions BGP plus a session/down/neighbor indicator"
        )
        return ("bgp_neighbor_down", "bgp_state_change", rationale)

    # Interface link-down and error-spike are different shapes but feed
    # the same incident_type - the simulator collapses them too.
    if et in {"interface_down", "link_down", "if_down"}:
        rationale.append(
            f"event_type {event.event_type!r} indicates interface/link down"
        )
        return ("interface_errors_spike", "interface_down", rationale)
    if "interface" in et and ("error" in et or "errors" in et):
        rationale.append(
            f"event_type {event.event_type!r} indicates interface errors"
        )
        return ("interface_errors_spike", "interface_error_spike", rationale)
    if "interface" in msg and any(
        tok in msg for tok in ("down", "error", "crc", "drop")
    ):
        rationale.append(
            "message mentions interface plus a down/error/crc/drop indicator"
        )
        return ("interface_errors_spike", "interface_down", rationale)

    # Latency / loss.
    if any(tok in et for tok in ("latency", "loss", "rtt")):
        rationale.append(
            f"event_type {event.event_type!r} indicates latency or loss"
        )
        return ("latency_spike", "latency_spike", rationale)
    if any(tok in msg for tok in ("latency", "packet loss", "high rtt")):
        rationale.append(
            "message mentions latency / packet loss / high rtt"
        )
        return ("latency_spike", "latency_spike", rationale)

    # Route missing / withdrawn / unreachable.
    if "route" in et and any(
        tok in et for tok in ("missing", "withdraw", "withdrawn", "unreach")
    ):
        rationale.append(
            f"event_type {event.event_type!r} indicates route missing / withdrawn"
        )
        return ("route_missing", "route_missing", rationale)
    if "route" in msg and any(
        tok in msg for tok in ("missing", "withdraw", "withdrawn", "unreach")
    ):
        rationale.append(
            "message mentions route plus missing / withdrawn / unreachable"
        )
        return ("route_missing", "route_missing", rationale)

    # ACL deny / block.
    if "acl" in et and ("deny" in et or "block" in et):
        rationale.append(
            f"event_type {event.event_type!r} indicates ACL deny / block"
        )
        return ("acl_blocking_traffic", "acl_deny", rationale)
    if "acl" in msg or "permit denied" in msg:
        rationale.append("message mentions ACL or 'permit denied'")
        return ("acl_blocking_traffic", "acl_deny", rationale)

    # Fallback.
    rationale.append(
        "no specific rule matched; cataloging as a generic telemetry observation "
        "(would NOT auto-open an incident)"
    )
    return (_TELEMETRY_OBSERVATION, event.event_type, rationale)


def _device_descriptor(event: TelemetryEvent) -> str:
    """Pick the best identity we have for this device, falling back to a
    stable sentinel so the correlation_key never ends with `::`."""
    return (
        event.hostname
        or event.device_hint
        or event.mgmt_ip
        or "unknown-device"
    )


_TITLES = {
    "bgp_neighbor_down": "BGP neighbor down on {device}",
    "interface_errors_spike": "Interface errors / down on {device}",
    "latency_spike": "Latency / packet loss on {device}",
    "route_missing": "Missing route on {device}",
    "acl_blocking_traffic": "ACL blocking traffic on {device}",
    _TELEMETRY_OBSERVATION: "Telemetry observation from {device}",
}


def _suggest_title(incident_type: str, device: str) -> str:
    return _TITLES.get(
        incident_type, f"Telemetry observation from {device}"
    ).format(device=device)


def _build_correlation_key(
    incident_type: str, event: TelemetryEvent, device: str
) -> str:
    """Build the deterministic dedup key. Future correlator code in a
    later phase will use this string to decide whether to attach the
    event to an existing open incident vs. open a new one."""
    parts = [incident_type, device]

    # Per-type narrowing: two different BGP sessions on the same router
    # shouldn't collapse into one incident, and neither should two
    # different failing interfaces.
    if incident_type == "bgp_neighbor_down":
        peer = (
            event.labels.get("neighbor")
            or event.labels.get("peer")
            or event.labels.get("peer_ip")
        )
        if peer:
            parts.append(f"peer={peer}")
    elif incident_type == "interface_errors_spike":
        interface = (
            event.labels.get("interface")
            or event.labels.get("ifname")
            or event.labels.get("if_name")
        )
        if interface:
            parts.append(f"if={interface}")
    elif incident_type == "route_missing":
        prefix = event.labels.get("prefix") or event.labels.get("destination")
        if prefix:
            parts.append(f"prefix={prefix}")

    return "::".join(parts)


def _confidence_for(incident_type: str, rationale: list[str]) -> float:
    """0.9 when matched by event_type (the most reliable signal),
    0.6 when matched by message keywords, 0.3 for the generic fallback.

    The strings checked below mirror the prefixes inserted by
    `_suggest_mapping` - keep these in sync if you rewrite the rationales.
    """
    if incident_type == _TELEMETRY_OBSERVATION:
        return 0.3
    primary = rationale[0] if rationale else ""
    if primary.startswith("event_type"):
        return 0.9
    return 0.6


def _build_event_payload(event: TelemetryEvent) -> dict[str, Any]:
    """Shape the payload exactly the way an `IncidentEvent.payload` JSONB
    would receive it. Nesting under a single `telemetry` key keeps this
    obviously-derived data separate from any future hand-added fields."""
    return {
        "telemetry": {
            "source": event.source,
            "collector_type": event.collector_type.value,
            "hostname": event.hostname,
            "mgmt_ip": event.mgmt_ip,
            "device_hint": event.device_hint,
            "observed_at": event.observed_at.isoformat(),
            "severity": event.severity.value,
            "labels": event.labels,
            "raw": event.raw,
        }
    }


# ---------- public entry point ----------


def build_correlation_preview(
    event: TelemetryEvent,
) -> TelemetryCorrelationPreview:
    """Map a `TelemetryEvent` to a read-only correlation preview.

    Pure function: no DB session, no I/O, no LLM call. Future Phase 18C+
    work can layer real correlation (against open incidents) and write
    paths behind a separate review without touching this contract.
    """
    incident_type, suggested_event_type, rationale = _suggest_mapping(event)
    severity = _suggest_severity(event)
    device = _device_descriptor(event)
    title = _suggest_title(incident_type, device)
    correlation_key = _build_correlation_key(incident_type, event, device)
    confidence = _confidence_for(incident_type, rationale)
    payload = _build_event_payload(event)

    would_create_incident = incident_type != _TELEMETRY_OBSERVATION
    would_create_event = True  # every telemetry event would log an IncidentEvent

    return TelemetryCorrelationPreview(
        telemetry_event=event,
        suggested_incident_type=incident_type,
        suggested_title=title,
        suggested_severity=severity,
        suggested_event_type=suggested_event_type,
        suggested_event_source=event.source,
        suggested_event_payload=payload,
        correlation_key=correlation_key,
        confidence=confidence,
        rationale=rationale,
        would_create_incident=would_create_incident,
        would_create_event=would_create_event,
    )

"""Phase 4 anomaly rules.

Each rule is a pure function that takes an Incident + its events + its evidence
and returns a (possibly empty) list of `AnomalyFinding` objects. Rules are
deterministic and depend only on the supplied data - no ML, no LLM, no I/O.

Rules tolerate small naming variations seen in real telemetry (e.g. `rtt_ms`
vs `latency_ms`, `loss_pct` vs `packet_loss_percent`) because Phase 3 / future
phases may emit either form.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Incident, IncidentEvent, IncidentEvidence


class AnomalyFinding(BaseModel):
    model_config = ConfigDict(json_schema_extra={"phase": 4})

    rule_id: str
    rule_name: str
    severity: str  # informational | low | medium | high | critical
    confidence: float = Field(ge=0.0, le=1.0)
    incident_id: UUID
    incident_type: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    recommended_next_step: str


# ---------- helpers ----------


def _payload(obj: IncidentEvent | IncidentEvidence) -> dict[str, Any]:
    return obj.payload or {}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _device(payload: dict[str, Any]) -> str:
    return str(payload.get("device") or payload.get("source") or "unknown")


def _ids(items: Iterable[IncidentEvent | IncidentEvidence]) -> list[str]:
    return [str(item.id) for item in items]


_LATENCY_METRICS = {"latency_ms", "rtt_ms"}
_LOSS_METRICS = {"packet_loss_percent", "loss_pct", "loss_percent"}


# ---------- rules ----------


def rule_bgp_neighbor_down(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches = [
        e
        for e in events
        if e.event_type == "bgp_state_change" and _payload(e).get("after") == "Idle"
    ]
    if not matches:
        return []
    p = _payload(matches[0])
    return [
        AnomalyFinding(
            rule_id="R001",
            rule_name="bgp_neighbor_down_detected",
            severity="critical",
            confidence=0.95,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"BGP session on {_device(p)} transitioned to Idle "
                f"(neighbor {p.get('neighbor', 'unknown')})."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Verify L1/link state and BGP neighbor config on both ends; "
                "only perform soft reset after explicit approval."
            ),
        )
    ]


def rule_route_withdrawal(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches: list[IncidentEvent] = []
    for e in events:
        if e.event_type == "route_withdrawal":
            matches.append(e)
            continue
        p = _payload(e)
        if p.get("metric_name") == "withdrawn_prefixes" and (_num(p.get("metric_value")) or 0) > 0:
            matches.append(e)
    if not matches:
        return []
    p = _payload(matches[0])
    count = int(_num(p.get("metric_value")) or len(matches))
    return [
        AnomalyFinding(
            rule_id="R002",
            rule_name="route_withdrawal_detected",
            severity="high",
            confidence=0.9,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"Route withdrawal observed on {_device(p)}: "
                f"{count} prefix(es) withdrawn from neighbor {p.get('neighbor', 'unknown')}."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Identify the upstream session that withdrew the routes; "
                "check session state and outbound policy on both ends."
            ),
        )
    ]


def rule_interface_error_spike(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches = [
        e
        for e in events
        if _payload(e).get("metric_name") == "input_errors_per_min"
        and (_num(_payload(e).get("metric_value")) or 0) > 50
    ]
    if not matches:
        return []
    p = _payload(matches[0])
    value = _num(p.get("metric_value"))
    return [
        AnomalyFinding(
            rule_id="R003",
            rule_name="interface_error_spike_detected",
            severity="high",
            confidence=0.9,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"Input error rate on {_device(p)} {p.get('interface', '?')} "
                f"is {value:.0f}/min (threshold 50/min)."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Inspect optics and patch cabling; if errors persist, "
                "drain traffic and replace the SFP."
            ),
        )
    ]


def rule_packet_loss(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches = [
        e
        for e in events
        if _payload(e).get("metric_name") in _LOSS_METRICS
        and (_num(_payload(e).get("metric_value")) or 0) > 1
    ]
    if not matches:
        return []
    p = _payload(matches[0])
    value = _num(p.get("metric_value")) or 0.0
    return [
        AnomalyFinding(
            rule_id="R004",
            rule_name="packet_loss_detected",
            severity="medium",
            confidence=0.85,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"Packet loss on {_device(p)} -> {p.get('target', 'target')} "
                f"is {value:.2f}% (threshold 1%)."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Trace the path and inspect queue / drop counters on each hop; "
                "look for congestion or oversubscription."
            ),
        )
    ]


def rule_latency_spike(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches: list[IncidentEvent] = []
    for e in events:
        p = _payload(e)
        if p.get("metric_name") not in _LATENCY_METRICS:
            continue
        value = _num(p.get("after")) or _num(p.get("metric_value"))
        if value is not None and value > 100:
            matches.append(e)
    if not matches:
        return []
    p = _payload(matches[0])
    value = _num(p.get("after")) or _num(p.get("metric_value")) or 0.0
    baseline = _num(p.get("before"))
    baseline_str = f" (baseline {baseline:.0f} ms)" if baseline is not None else ""
    return [
        AnomalyFinding(
            rule_id="R005",
            rule_name="latency_spike_detected",
            severity="medium",
            confidence=0.85,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"Latency on {_device(p)} -> {p.get('target', 'target')} "
                f"is {value:.0f} ms (threshold 100 ms){baseline_str}."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Run a path trace; inspect queue depth / drop counters on the slow hop."
            ),
        )
    ]


def rule_acl_deny_spike(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    matches: list[IncidentEvent] = []
    for e in events:
        if e.event_type == "traffic_denied":
            matches.append(e)
            continue
        p = _payload(e)
        if p.get("metric_name") == "acl_deny_hits" and (_num(p.get("metric_value")) or 0) > 0:
            matches.append(e)
    if not matches:
        return []
    p = _payload(matches[0])
    count = int(_num(p.get("metric_value")) or len(matches))
    return [
        AnomalyFinding(
            rule_id="R006",
            rule_name="acl_deny_spike_detected",
            severity="medium",
            confidence=0.85,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"ACL deny hits on {_device(p)} policy {p.get('policy', 'unknown')}: "
                f"{count} hit(s) for src={p.get('src', '?')} dst={p.get('dst', '?')}."
            ),
            evidence_refs=_ids(matches),
            recommended_next_step=(
                "Pull the most recent policy change-record; if the denied flow "
                "should be permitted, add an explicit permit ahead of the deny rule."
            ),
        )
    ]


def rule_route_missing(
    incident: Incident,
    events: list[IncidentEvent],
    evidence: list[IncidentEvidence],
) -> list[AnomalyFinding]:
    event_matches = [e for e in events if e.event_type == "route_missing"]
    evidence_matches = [v for v in evidence if _payload(v).get("result") == "not_in_table"]
    if not event_matches and not evidence_matches:
        return []
    source = event_matches[0] if event_matches else evidence_matches[0]
    p = _payload(source)
    return [
        AnomalyFinding(
            rule_id="R007",
            rule_name="route_missing_detected",
            severity="high",
            confidence=0.9,
            incident_id=incident.id,
            incident_type=incident.incident_type,
            summary=(
                f"Prefix {p.get('prefix', 'unknown')} is not present in "
                f"{_device(p)}'s route table."
            ),
            evidence_refs=_ids(event_matches) + _ids(evidence_matches),
            recommended_next_step=(
                "Confirm intended advertisement; review outbound prefix-list / "
                "route-map filters on the expected advertiser."
            ),
        )
    ]


RuleFn = Callable[
    [Incident, list[IncidentEvent], list[IncidentEvidence]],
    list[AnomalyFinding],
]

RULES: list[RuleFn] = [
    rule_bgp_neighbor_down,
    rule_route_withdrawal,
    rule_interface_error_spike,
    rule_packet_loss,
    rule_latency_spike,
    rule_acl_deny_spike,
    rule_route_missing,
]
